"""
YAMP — Yet Another Media Provider
A Plex Custom Metadata Provider for yt-dlp downloads.
"""

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from collection_map import (
    MATCH_FIELDS,
    diff_collections,
    find_collection_map,
    load_map,
    match_video,
    recompute_all_collections,
    resolve_collections,
    save_map,
)
from metadata import (
    _BILIBILI_ID_RE,
    _GENERIC_ID_RE,
    _YOUTUBE_ID_RE,
    build_metadata_response,
    extract_video_id,
    parse_upload_date,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

IDENTIFIER = "tv.plex.agents.custom.yamp"
DATA_PATH = os.environ.get("YOUTUBE_DATA_PATH", "/data")
PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
YAMP_URL = os.environ.get("YAMP_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8765"))
API_KEY = os.environ.get("API_KEY", "")
APP_VERSION = os.environ.get("APP_VERSION", "dev")

METADATA_KEY = "/library/metadata"
MATCH_KEY = "/library/metadata/matches"

# ── Video index ───────────────────────────────────────────────────────────────
# Maps video_id → absolute path to its .info.json file

_video_index: dict[str, str] = {}
_stem_index: dict[str, str] = {}  # info.json filename stem → video_id (match endpoint fallback)
_video_meta_cache: dict[str, dict] = {}  # video_id → MATCH_FIELDS subset of info_json
_last_rebuild: float = 0.0
_REBUILD_COOLDOWN = 60.0


# Sanity-check IDs read directly from info.json (build_index fallback for no-bracket filenames)
_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,}$")


def build_index(data_path: str) -> tuple[dict[str, str], dict[str, str]]:
    """Walk data_path and index all .info.json files by video ID.

    Returns (video_index, stem_index). video_index maps video_id → absolute
    path to the .info.json. stem_index maps the info.json filename stem
    (filename minus ".info.json") → video_id, used as a last-resort fallback
    in the match endpoint for video files whose names contain no embedded ID.
    """
    index: dict[str, str] = {}
    stem_index: dict[str, str] = {}

    def onerror(err: OSError) -> None:
        logger.warning("Index walk error at '%s' (errno %d) — skipping: %s", err.filename, err.errno, err)

    for root, _, files in os.walk(data_path, onerror=onerror):
        for f in files:
            if not f.endswith(".info.json"):
                continue
            # Try the filename first (yt-dlp default: "Title [VIDEO_ID].info.json").
            # Fall back to the containing directory name, which covers MeTube's
            # per-video folder layout: "Channel/Title [VIDEO_ID]/Title.info.json".
            video_id = extract_video_id(f) or extract_video_id(os.path.basename(root))
            if not video_id:
                # No bracket-wrapped ID found — read the canonical ID from the JSON itself.
                # Handles non-standard output templates where yt-dlp omits [id] from the name.
                try:
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        raw_id = json.load(fh).get("id", "")
                    video_id = raw_id if raw_id and _VALID_ID_RE.match(raw_id) else None
                except (OSError, json.JSONDecodeError):
                    pass
            if not video_id:
                continue
            path = os.path.join(root, f)
            index[video_id] = path
            stem_index[f.removesuffix(".info.json")] = video_id

    if not index:
        logger.warning("Index is empty — no .info.json files found under %s", data_path)
    else:
        logger.info("Indexed %d videos from %s", len(index), data_path)
    return index, stem_index


def build_meta_cache(video_index: dict[str, str]) -> dict[str, dict]:
    """Read all indexed info_json files and cache the fields used for collection matching.

    This eliminates disk I/O from recompute_all_collections on subsequent saves.
    Called once at startup and after periodic index rebuilds.
    """
    cache: dict[str, dict] = {}
    for video_id, path in video_index.items():
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
            cache[video_id] = {k: info[k] for k in MATCH_FIELDS if k in info}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("build_meta_cache: skipping %s: %s", video_id, e)
    logger.info("build_meta_cache: cached %d videos", len(cache))
    return cache


def _try_index_from_filename(video_id: str, media_path: str) -> bool:
    """Try to index a single video by finding its .info.json alongside the media file.

    Plex sends the full media file path in the match request. The sidecar .info.json
    lives next to it (yt-dlp flat layout) or in the same per-video folder (MeTube layout).
    Returns True if the entry was added to the index.
    """
    p = Path(media_path)
    # yt-dlp flat:  Title [ID].mp4  →  Title [ID].info.json
    # MeTube:       Title [ID]/Title.mp4  →  Title [ID]/Title.info.json
    candidate = p.with_suffix(".info.json")
    if candidate.is_file():
        _video_index[video_id] = str(candidate)
        _stem_index[candidate.name.removesuffix(".info.json")] = video_id
        logger.info("Indexed new video '%s' from sidecar: %s", video_id, candidate)
        try:
            with open(candidate, encoding="utf-8") as f:
                info = json.load(f)
            _video_meta_cache[video_id] = {k: info[k] for k in MATCH_FIELDS if k in info}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("_try_index_from_filename: could not cache meta for %s: %s", video_id, e)
        return True
    return False


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _video_index, _stem_index, _video_meta_cache
    if not os.path.isdir(DATA_PATH):
        logger.error(
            "YOUTUBE_DATA_PATH '%s' does not exist or is not a directory. Refusing to start.",
            DATA_PATH,
        )
        raise RuntimeError(f"YOUTUBE_DATA_PATH '{DATA_PATH}' is not a directory")
    _video_index, _stem_index = build_index(DATA_PATH)
    _video_meta_cache = build_meta_cache(_video_index)
    yield


app = FastAPI(title="YAMP", lifespan=lifespan)

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_info_json(video_id: str) -> dict:
    """
    Load info_json for a video.

    If the video ID is not in the current index, triggers an index rebuild
    (rate-limited to once per 60 s) before retrying. Raises HTTP 404 if
    still not found after rebuild, or HTTP 500 on read/parse failure.
    """
    global _video_index, _stem_index, _video_meta_cache, _last_rebuild
    path = _video_index.get(video_id)
    if not path:
        if time.monotonic() - _last_rebuild > _REBUILD_COOLDOWN:
            _video_index, _stem_index = await asyncio.to_thread(build_index, DATA_PATH)
            _video_meta_cache = await asyncio.to_thread(build_meta_cache, _video_index)
            _last_rebuild = time.monotonic()
        path = _video_index.get(video_id)
    if not path:
        logger.warning("Video '%s' not found in index (after rebuild)", video_id)
        raise HTTPException(status_code=404, detail=f"Video '{video_id}' not found")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        logger.error("Failed to open info_json for '%s' at '%s': %s", video_id, path, e)
        raise HTTPException(status_code=500, detail=f"Could not read metadata for '{video_id}'") from e
    except json.JSONDecodeError as e:
        logger.error("Failed to parse info_json for '%s' at '%s': %s", video_id, path, e)
        raise HTTPException(status_code=500, detail=f"Corrupt metadata for '{video_id}'") from e


def _collection_map_path() -> str | None:
    return find_collection_map(DATA_PATH, DATA_PATH)


def _require_api_key(request: Request) -> None:
    """FastAPI dependency — enforces Bearer token auth when API_KEY is set."""
    if not API_KEY:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[len("Bearer ") :] != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


def _validate_video_id(video_id: str) -> bool:
    """Return True if video_id matches a known yt-dlp ID format."""
    probe = f"[{video_id}]"
    return bool(_YOUTUBE_ID_RE.search(probe) or _BILIBILI_ID_RE.search(probe) or _GENERIC_ID_RE.search(probe))


def _media_container(metadata_list: list[dict]) -> dict:
    return {
        "MediaContainer": {
            "offset": 0,
            "totalSize": len(metadata_list),
            "identifier": IDENTIFIER,
            "size": len(metadata_list),
            "Metadata": metadata_list,
        }
    }


# ── Plex provider endpoints ───────────────────────────────────────────────────


@app.get("/movies")
async def get_provider():
    """MediaProvider definition — Plex calls this to discover the provider."""
    return {
        "MediaProvider": {
            "identifier": IDENTIFIER,
            "title": "YAMP — Yet Another Media Provider",
            "version": "1.0.0",
            "Types": [{"type": 1, "Scheme": [{"scheme": IDENTIFIER}]}],
            "Feature": [
                {"type": "match", "key": MATCH_KEY},
                {"type": "metadata", "key": METADATA_KEY},
            ],
        }
    }


@app.post("/movies/library/metadata/matches")
async def match(request: Request):
    """
    Match endpoint — Plex sends file info, we return the best match.
    We extract the video ID from the filename (yt-dlp embeds [VIDEO_ID] in the name).
    """
    body = await request.json()
    filename = body.get("filename", "")

    # Try the filename itself, then the immediate parent directory name
    # (covers MeTube's "Title [ID]/Title.ext" per-video folder layout),
    # then fall back to the stem index for bare filenames with no ID anywhere.
    video_id = extract_video_id(filename)
    if not video_id:
        video_id = extract_video_id(Path(filename).parent.name)
    if not video_id:
        video_id = _stem_index.get(Path(filename).stem)
    if not video_id:
        safe_filename = "".join(c for c in filename[:256] if c >= " ")
        logger.warning("Could not extract video ID from filename: %s", safe_filename)
        return JSONResponse(_media_container([]))

    if video_id not in _video_index and filename:
        _try_index_from_filename(video_id, filename)

    try:
        info_json = await _get_info_json(video_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("No info_json found for video ID: %s", video_id)
            return JSONResponse(_media_container([]))
        logger.error("Failed to load info_json for video ID '%s': %s", video_id, exc.detail)
        raise

    title = info_json.get("title")
    if not title:
        logger.error("Missing title in info_json for video ID: %s (file corrupt?)", video_id)
        return JSONResponse(_media_container([]))

    upload_date_raw = info_json.get("upload_date")
    if not upload_date_raw:
        logger.error("Missing upload_date in info_json for video ID: %s (file corrupt?)", video_id)
        return JSONResponse(_media_container([]))

    try:
        upload_date = parse_upload_date(upload_date_raw)
    except ValueError:
        logger.warning("Unparseable upload_date %r for video ID: %s", upload_date_raw, video_id)
        return JSONResponse(_media_container([]))

    return JSONResponse(
        _media_container(
            [
                {
                    "ratingKey": video_id,
                    "key": f"{METADATA_KEY}/{video_id}",
                    "guid": f"{IDENTIFIER}://movie/{video_id}",
                    "type": "movie",
                    "title": title,
                    "year": upload_date.year,
                    "originallyAvailableAt": upload_date.isoformat(),
                }
            ]
        )
    )


_THUMB_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _local_thumb_path(video_id: str) -> Path | None:
    """Return the path to a local thumbnail file for video_id, or None."""
    path = _video_index.get(video_id)
    if not path:
        return None
    base = Path(path).with_suffix("").with_suffix("")
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = base.with_suffix(ext)
        if not candidate.resolve().is_relative_to(Path(DATA_PATH).resolve()):
            logger.warning("_local_thumb_path: '%s' escapes DATA_PATH — skipping", candidate)
            continue
        if candidate.exists():
            return candidate
    return None


@app.get("/api/thumbnail/{video_id}")
async def api_thumbnail(video_id: str):
    """Serve a thumbnail — local file if available, otherwise proxy from the remote URL."""
    if not _validate_video_id(video_id):
        raise HTTPException(status_code=404)
    thumb = _local_thumb_path(video_id)
    if thumb:
        return FileResponse(str(thumb), media_type=_THUMB_MIME.get(thumb.suffix.lower(), "image/jpeg"))
    # No local file — proxy the remote thumbnail so Plex always gets a YAMP-served URL
    info_path = _video_index.get(video_id)
    if not info_path:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
    except OSError as e:
        logger.error("api_thumbnail: could not read info_json for '%s' at '%s': %s", video_id, info_path, e)
        raise HTTPException(status_code=500, detail=f"Could not read metadata for '{video_id}'") from e
    except json.JSONDecodeError as e:
        logger.error("api_thumbnail: corrupt info_json for '%s' at '%s': %s", video_id, info_path, e)
        raise HTTPException(status_code=500, detail=f"Corrupt metadata for '{video_id}'") from e
    thumb_url = info.get("thumbnail")
    if not thumb_url:
        raise HTTPException(status_code=404, detail="No thumbnail available")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
            resp = await client.get(thumb_url)
    except httpx.TimeoutException as e:
        logger.warning("api_thumbnail: timed out fetching thumbnail for '%s'", video_id)
        raise HTTPException(status_code=504, detail="Thumbnail fetch timed out") from e
    except httpx.RequestError as e:
        logger.warning("api_thumbnail: network error fetching thumbnail for '%s': %s", video_id, e)
        raise HTTPException(status_code=502, detail="Could not fetch thumbnail") from e
    if resp.status_code != 200:
        logger.warning("api_thumbnail: upstream returned HTTP %d for video '%s'", resp.status_code, video_id)
        raise HTTPException(status_code=502, detail=f"Thumbnail upstream returned {resp.status_code}")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))


@app.get("/movies/library/metadata/{rating_key}/images")
async def get_images(rating_key: str):
    """Images endpoint — return poster/backdrop URLs for a video."""
    video_id = rating_key
    if not _validate_video_id(video_id):
        logger.warning("get_images: invalid video ID format: %r", video_id)
        raise HTTPException(status_code=404)

    info_json = await _get_info_json(video_id)
    images = []

    # Route through YAMP proxy when configured — Plex can't always reach YouTube directly
    if YAMP_URL:
        images.append({"type": "coverPoster", "url": f"{YAMP_URL}/api/thumbnail/{video_id}"})
    elif thumb := info_json.get("thumbnail"):
        images.append({"type": "coverPoster", "url": thumb})

    return JSONResponse(
        {
            "MediaContainer": {
                "offset": 0,
                "totalSize": len(images),
                "size": len(images),
                "Image": images,
            }
        }
    )


@app.get("/movies/library/metadata/{rating_key}")
async def get_metadata(rating_key: str):
    """Full metadata endpoint — called after a successful match."""
    video_id = rating_key
    if not _validate_video_id(video_id):
        logger.warning("get_metadata: invalid video ID format: %r", video_id)
        raise HTTPException(status_code=404)

    info_json = await _get_info_json(video_id)

    mapping_path = _collection_map_path()
    collections: list[str] = []
    if mapping_path:
        try:
            collections = await asyncio.to_thread(resolve_collections, info_json, mapping_path)
        except OSError as e:
            logger.error(
                "resolve_collections failed for '%s' (I/O error, collection state not persisted): %s",
                video_id,
                e,
            )
        except ValueError as e:
            logger.error(
                "resolve_collections failed for '%s' (invalid data in collection map): %s",
                video_id,
                e,
            )
    logger.info("get_metadata: '%s' → collections=%s", video_id, collections)

    try:
        meta = build_metadata_response(info_json, collections, rating_key, IDENTIFIER, METADATA_KEY)
    except ValueError as e:
        logger.error("build_metadata_response failed for '%s': %s", video_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to build metadata for '{video_id}'") from e
    return JSONResponse(_media_container([meta]))


# ── Collection management API (consumed by the React UI) ─────────────────────


class RuleModel(BaseModel):
    field: str
    match: Literal["exact", "in"]
    values: list[str]


class CollectionModel(BaseModel):
    name: str
    rules: list[RuleModel]
    image: str | None = None


class CollectionsBody(BaseModel):
    collections: list[CollectionModel]

    @field_validator("collections")
    @classmethod
    def names_are_unique(cls, v: list[CollectionModel]) -> list[CollectionModel]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            raise ValueError("Collection names must be unique")
        return v


@app.get("/api/version")
async def api_version():
    """Return the running YAMP version."""
    return {"version": APP_VERSION}


@app.get("/api/collections")
async def api_get_collections():
    mapping_path = _collection_map_path()
    if not mapping_path:
        return {
            "collections": [],
            "unmatched_tags": {},
            "matched_count": 0,
            "unmatched_count": 0,
        }
    try:
        data = load_map(mapping_path)
    except (OSError, ValueError) as e:
        logger.error("api_get_collections: failed to load collection map at '%s': %s", mapping_path, e)
        raise HTTPException(status_code=500, detail="Could not read collection map") from e

    plex_thumbs: dict[str, str] = {}
    plex_thumb_error = False
    if PLEX_URL and PLEX_TOKEN:
        try:
            plex_thumbs = await asyncio.to_thread(_fetch_plex_collection_thumbs)
        except Exception as e:
            logger.error("api_get_collections: unexpected error fetching Plex thumbs: %s", e)
            plex_thumb_error = True

    collections = [{**col, "plex_thumb": plex_thumbs.get(col.get("name"))} for col in data.get("collections", [])]
    result: dict = {
        "collections": collections,
        "unmatched_tags": data.get("unmatched_tags", {}),
        "matched_count": len(data.get("matched_ids", [])),
        "unmatched_count": len(data.get("unmatched_ids", [])),
    }
    if plex_thumb_error:
        result["plex_thumb_error"] = True
    return result


@app.put("/api/collections", dependencies=[Depends(_require_api_key)])
async def api_put_collections(body: CollectionsBody, background_tasks: BackgroundTasks):
    mapping_path = _collection_map_path()
    if not mapping_path:
        raise HTTPException(status_code=404, detail="Collection map not found")
    try:
        data = load_map(mapping_path)
    except (OSError, ValueError) as e:
        logger.error("api_put_collections: failed to load collection map at '%s': %s", mapping_path, e)
        raise HTTPException(status_code=500, detail="Could not read collection map") from e

    old_cols = data.get("collections", [])
    new_cols = [c.model_dump() for c in body.collections]
    rules_changed, has_rule_changes = diff_collections(old_cols, new_cols)

    data["collections"] = new_cols
    try:
        save_map(mapping_path, data)
    except OSError as e:
        logger.error("api_put_collections: failed to save collection map at '%s': %s", mapping_path, e)
        raise HTTPException(status_code=500, detail="Could not write collection map") from e

    if has_rule_changes:
        cache = _video_meta_cache  # capture ref before thread dispatch
        try:
            stats = await asyncio.to_thread(
                recompute_all_collections,
                _video_index,
                mapping_path,
                cache,
            )
        except (OSError, ValueError) as e:
            logger.error("api_put_collections: recompute failed: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Collections saved but recompute failed — trigger a rescan to retry",
            ) from e
    else:
        stats = {
            "matched": len(data.get("matched_ids", [])),
            "unmatched": len(data.get("unmatched_ids", [])),
            "skipped": 0,
        }

    # Schedule artwork sync for collections that changed (rules or image)
    if PLEX_URL and PLEX_TOKEN:
        old_images = {c["name"]: c.get("image") for c in old_cols}
        for col in body.collections:
            if col.image and (col.name in rules_changed or old_images.get(col.name) != col.image):
                background_tasks.add_task(_sync_collection_artwork_bg, col)
        background_tasks.add_task(_do_rescan_bg)

    plex_sync = bool(PLEX_URL and PLEX_TOKEN)
    return {"ok": True, **stats, "plex_sync": plex_sync}


def _build_video_list(video_index: dict[str, str], collections: list[dict]) -> tuple[list[dict], list[str]]:
    """Synchronous helper: build the video list from the index. Run via asyncio.to_thread.

    Returns (videos, skipped_ids) where skipped_ids contains video IDs that could
    not be read or parsed.
    """
    videos = []
    skipped_ids = []
    for video_id, path in video_index.items():
        try:
            with open(path, encoding="utf-8") as f:
                info_json = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("api_videos: skipping %s: %s", video_id, e)
            skipped_ids.append(video_id)
            continue

        thumb_path = _local_thumb_path(video_id)
        if thumb_path and YAMP_URL:
            thumbnail = f"{YAMP_URL}/api/thumbnail/{video_id}"
        elif thumb_path:
            thumbnail = f"/api/thumbnail/{video_id}"
        else:
            thumbnail = info_json.get("thumbnail", "")

        c_matches, _ = match_video(info_json, collections)

        upload_date_raw = info_json.get("upload_date", "")
        try:
            upload_date = parse_upload_date(upload_date_raw).isoformat() if upload_date_raw else ""
        except ValueError:
            logger.warning("api_videos: unparseable upload_date %r for %s — omitting date", upload_date_raw, video_id)
            upload_date = ""

        videos.append(
            {
                "id": video_id,
                "title": info_json.get("title", ""),
                "channel": info_json.get("channel", "") or info_json.get("uploader", ""),
                "thumbnail": thumbnail,
                "upload_date": upload_date,
                "collections": c_matches,
                "matched": bool(c_matches),
                "tags": info_json.get("tags", []),
            }
        )

    videos.sort(key=lambda v: v["upload_date"], reverse=True)
    return videos, skipped_ids


@app.get("/api/videos")
async def api_videos():
    """Return all indexed videos with metadata and matched collections."""
    mapping_path = _collection_map_path()
    collections: list[dict] = []
    collections_error = False
    if mapping_path:
        try:
            collections = load_map(mapping_path).get("collections", [])
        except (OSError, ValueError) as e:
            logger.error("api_videos: failed to load collection map at '%s': %s", mapping_path, e)
            collections_error = True

    videos, skipped_ids = await asyncio.to_thread(_build_video_list, _video_index, collections)
    result: dict = {"videos": videos}
    if collections_error:
        result["collections_error"] = True
    if skipped_ids:
        result["skipped_videos"] = skipped_ids
    return result


async def _fetch_plex_sections(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Plex library sections. Raises HTTPException on any failure."""
    plex_headers = {"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"}
    try:
        resp = await client.get(f"{PLEX_URL}/library/sections", headers=plex_headers)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="Plex server timed out") from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Plex returned {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Could not reach Plex: {e}") from e
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        raise HTTPException(status_code=502, detail="Plex returned unexpected response format") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Plex returned unexpected response format")
    return data.get("MediaContainer", {}).get("Directory", [])


def _video_id_from_plex_item(item) -> str | None:
    """Extract the YAMP video ID from a plexapi library item, or None if not a YAMP item."""
    guid = getattr(item, "guid", "") or ""
    prefix = f"{IDENTIFIER}://movie/"
    if guid.startswith(prefix):
        return guid[len(prefix) :].rstrip("/") or None
    return None


def _has_local_thumbnail(video_id: str, video_index: dict[str, str]) -> bool:
    """Return True if a local image file exists alongside this video's .info.json."""
    info_path = video_index.get(video_id)
    if not info_path:
        return False
    base = Path(info_path).with_suffix("").with_suffix("")  # strip both .json and .info
    return any(base.with_suffix(ext).exists() for ext in (".jpg", ".jpeg", ".png", ".webp"))


def _find_matching_plex_items(section, col_spec: list) -> list:
    """Return Plex video objects in `section` whose info_json matches col_spec."""
    results = []
    for item in section.all():
        video_id = _video_id_from_plex_item(item)
        if not video_id:
            continue
        info_path = _video_index.get(video_id)
        if not info_path:
            continue
        try:
            with open(info_path, encoding="utf-8") as f:
                info_json = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("_find_matching_plex_items: skipping '%s' at '%s': %s", video_id, info_path, e)
            continue
        matches, _ = match_video(info_json, col_spec)
        if matches:
            results.append(item)
    return results


def _fetch_plex_collection_thumbs() -> dict[str, str]:
    """Return {collection_name: relative proxy path} for all collections in YAMP-managed Plex sections.

    Paths are relative (e.g. /api/plex-collection-thumb?path=…) so the Plex token
    is never sent to the browser.
    """
    import requests.exceptions
    from plexapi.exceptions import PlexApiException
    from plexapi.server import PlexServer

    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    except (PlexApiException, requests.exceptions.RequestException) as e:
        logger.error("_fetch_plex_collection_thumbs: failed to connect to Plex at '%s': %s", PLEX_URL, e)
        return {}
    try:
        sections = plex.library.sections()
    except (PlexApiException, requests.exceptions.RequestException) as e:
        logger.error("_fetch_plex_collection_thumbs: failed to fetch sections from Plex: %s", e)
        return {}
    thumbs: dict[str, str] = {}
    for section in sections:
        if section.agent != IDENTIFIER:
            continue
        try:
            for col in section.collections():
                if col.thumb:
                    thumbs[col.title] = f"/api/plex-collection-thumb?path={quote(col.thumb)}"
        except (PlexApiException, requests.exceptions.RequestException) as e:
            logger.error(
                "_fetch_plex_collection_thumbs: error fetching collections for section '%s': %s",
                section.title,
                e,
            )
    return thumbs


def _sync_collection_artwork(col: CollectionModel) -> dict:
    """Ensure `col` exists in Plex and upload its poster. Synchronous — call via asyncio.to_thread."""
    from plexapi.exceptions import NotFound
    from plexapi.server import PlexServer

    if not col.image:
        return {"ok": False, "created": False, "error": "no image set"}
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    except Exception as e:
        logger.error("_sync_collection_artwork: Plex connection failed: %s", e)
        return {"ok": False, "created": False, "error": f"Plex connection failed: {e}"}

    col_spec = [{"name": col.name, "rules": [r.model_dump() for r in col.rules]}]
    yamp_sections = [s for s in plex.library.sections() if s.agent == IDENTIFIER]
    if not yamp_sections:
        return {"ok": False, "created": False, "error": "No YAMP-managed sections found in Plex"}

    for section in yamp_sections:
        created = False
        try:
            plex_col = section.collection(col.name)
        except NotFound:
            items = _find_matching_plex_items(section, col_spec)
            if not items:
                return {"ok": False, "created": False, "error": f"'{col.name}' not in Plex and no matched videos found"}
            try:
                plex_col = plex.createCollection(title=col.name, section=section, items=items)
                created = True
            except Exception as e:
                logger.error("_sync_collection_artwork: createCollection failed for '%s': %s", col.name, e)
                return {"ok": False, "created": False, "error": f"Could not create collection: {e}"}

        try:
            plex_col.uploadPoster(url=col.image)
        except Exception as e:
            logger.error("_sync_collection_artwork: uploadPoster failed for '%s': %s", col.name, e)
            return {"ok": False, "created": created, "error": f"Poster upload failed: {e}"}

        logger.info("_sync_collection_artwork: '%s' — ok (created=%s)", col.name, created)
        return {"ok": True, "created": created, "error": None}

    return {"ok": False, "created": False, "error": "No YAMP sections processed"}


_PLEX_THUMB_PATH_RE = re.compile(r"^/library/(collections|metadata)/\d+/(thumb|composite)(/\d+(\?[a-zA-Z0-9=&]+)?)?$")


@app.get("/api/plex-collection-thumb")
async def api_plex_collection_thumb(path: str):
    """Proxy a Plex collection poster server-side so the Plex token never reaches the browser."""
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(status_code=404)
    if not _PLEX_THUMB_PATH_RE.match(path):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
            resp = await client.get(f"{PLEX_URL}{path}", headers={"X-Plex-Token": PLEX_TOKEN})
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="Plex timed out") from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Plex: {e}") from e
    if resp.status_code != 200:
        logger.warning(
            "api_plex_collection_thumb: Plex returned HTTP %d for path '%s'",
            resp.status_code,
            path,
        )
        raise HTTPException(status_code=502, detail=f"Plex returned {resp.status_code}")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))


@app.get("/api/plex/sections")
async def api_plex_sections():
    """Diagnostic: return all Plex library sections and their configured agents."""
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(status_code=400, detail="PLEX_URL and PLEX_TOKEN env vars not set")
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        sections = await _fetch_plex_sections(client)
    return {"sections": sections}


async def _do_rescan() -> dict:
    """Trigger a Plex metadata refresh on all YAMP-managed libraries. Returns result dict."""
    if not PLEX_URL or not PLEX_TOKEN:
        return {"triggered_sections": [], "failed_sections": []}
    plex_headers = {"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        sections = await _fetch_plex_sections(client)

        triggered = []
        failed = []
        for section in sections:
            if section.get("agent") == IDENTIFIER:
                section_id = section["key"]
                if not str(section_id).isdigit():
                    logger.warning("Skipping section with non-numeric key: %r", section_id)
                    continue
                try:
                    resp = await client.get(
                        f"{PLEX_URL}/library/sections/{section_id}/refresh",
                        headers=plex_headers,
                        params={"force": 1},
                    )
                    resp.raise_for_status()
                    triggered.append(section_id)
                except httpx.TimeoutException:
                    logger.error("Timed out refreshing section %s", section_id)
                    failed.append({"section_id": section_id, "error": "timeout"})
                except httpx.HTTPStatusError as e:
                    logger.error("Failed to refresh section %s: HTTP %s", section_id, e.response.status_code)
                    failed.append({"section_id": section_id, "error": f"HTTP {e.response.status_code}"})
                except httpx.RequestError as e:
                    logger.error("Network error refreshing section %s: %s", section_id, e)
                    failed.append({"section_id": section_id, "error": str(e)})

    return {"triggered_sections": triggered, "failed_sections": failed}


async def _do_rescan_bg() -> None:
    """Background wrapper for _do_rescan: logs failures instead of raising."""
    try:
        result = await _do_rescan()
        if result.get("failed_sections"):
            logger.error("Background rescan: failed sections: %s", result["failed_sections"])
    except Exception:
        logger.exception("Background rescan raised an unhandled exception")


@app.post("/api/rescan", dependencies=[Depends(_require_api_key)])
async def api_rescan():
    """Trigger a Plex metadata refresh on all libraries using this provider."""
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(status_code=400, detail="PLEX_URL and PLEX_TOKEN env vars not set")
    return await _do_rescan()


async def _sync_collection_artwork_bg(col) -> None:
    """Background wrapper: sync artwork for one collection and log any errors."""
    try:
        result = await asyncio.to_thread(_sync_collection_artwork, col)
        if not result.get("ok"):
            logger.error("Background artwork sync failed for '%s': %s", col.name, result.get("error"))
    except Exception:
        logger.exception("Background artwork sync raised an unhandled exception for '%s'", col.name)


def _fix_all_thumbnails(
    meta_cache: dict[str, dict] | None = None,
    video_index: dict[str, str] | None = None,
) -> dict:
    """Upload YAMP-proxied thumbnails for every video in YAMP-managed Plex sections.

    Synchronous — call via asyncio.to_thread. Returns {fixed, failed, skipped}.
    meta_cache and video_index are passed in by the caller before thread dispatch to
    avoid reading globals that may be replaced concurrently.
    """
    import requests.exceptions
    from plexapi.exceptions import PlexApiException
    from plexapi.server import PlexServer

    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    except (PlexApiException, requests.exceptions.RequestException) as e:
        logger.error("_fix_all_thumbnails: Plex connection failed: %s", e)
        return {"fixed": 0, "failed": 0, "skipped": 0, "error": f"Plex connection failed: {e}"}

    fixed = failed = skipped = 0
    try:
        sections = plex.library.sections()
    except (PlexApiException, requests.exceptions.RequestException) as e:
        logger.error("_fix_all_thumbnails: failed to fetch sections: %s", e)
        return {"fixed": 0, "failed": 0, "skipped": 0, "error": f"Could not list sections: {e}"}

    for section in sections:
        if section.agent != IDENTIFIER:
            continue
        try:
            items = section.all()
        except (PlexApiException, requests.exceptions.RequestException) as e:
            logger.error("_fix_all_thumbnails: failed to list items in section '%s': %s", section.title, e)
            failed += 1
            continue
        for item in items:
            video_id = _video_id_from_plex_item(item)
            if not video_id:
                logger.warning(
                    "_fix_all_thumbnails: skipping item with unrecognised guid %r", getattr(item, "guid", "")
                )  # noqa: E501
                skipped += 1
                continue
            _index = video_index or _video_index
            has_local = _has_local_thumbnail(video_id, _index)
            has_youtube = bool(((meta_cache or {}).get(video_id) or {}).get("thumbnail"))
            if not (has_local or has_youtube):
                skipped += 1
                continue
            if YAMP_URL:
                thumb_url = f"{YAMP_URL}/api/thumbnail/{video_id}"
            else:
                thumb_url = ((meta_cache or {}).get(video_id) or {}).get("thumbnail") or ""
                if not thumb_url:
                    logger.warning("_fix_all_thumbnails: no thumbnail URL for %r — skipping", video_id)
                    skipped += 1
                    continue
            try:
                item.uploadPoster(url=thumb_url)
                fixed += 1
            except (PlexApiException, requests.exceptions.RequestException) as e:
                logger.error("_fix_all_thumbnails: uploadPoster failed for %r: %s", video_id, e)
                failed += 1

    logger.info("_fix_all_thumbnails: done — fixed=%d failed=%d skipped=%d", fixed, failed, skipped)
    return {"fixed": fixed, "failed": failed, "skipped": skipped}


@app.post("/api/thumbnails/fix", dependencies=[Depends(_require_api_key)])
async def api_fix_thumbnails():
    """Push YAMP-proxied thumbnails to Plex for all videos in YAMP-managed libraries."""
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(status_code=400, detail="PLEX_URL and PLEX_TOKEN env vars not set")
    cache = _video_meta_cache  # capture refs before thread dispatch
    index = _video_index
    result = await asyncio.to_thread(_fix_all_thumbnails, cache, index)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/index/rebuild", dependencies=[Depends(_require_api_key)])
async def api_rebuild_index():
    """Force a rebuild of the in-memory video index."""
    global _video_index, _stem_index, _video_meta_cache
    _video_index, _stem_index = await asyncio.to_thread(build_index, DATA_PATH)
    _video_meta_cache = await asyncio.to_thread(build_meta_cache, _video_index)
    if not _video_index:
        logger.warning("Rebuilt index is empty — no videos found under %s", DATA_PATH)
    return {"indexed": len(_video_index)}


# ── Static UI (served last so API routes take priority) ──────────────────────

_UI_DIR = Path(__file__).parent / "ui" / "dist"
_UI_BASE = _UI_DIR.resolve()
if _UI_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_UI_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_ui():
        return FileResponse(_UI_DIR / "index.html")

    @app.get("/{path:path}")
    async def serve_ui_path(path: str):
        file_path = (_UI_DIR / path).resolve()
        if not file_path.is_relative_to(_UI_BASE):
            raise HTTPException(status_code=404)
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_UI_DIR / "index.html")
