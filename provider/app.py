"""
YAMP — YouTube Auto Metadata Provider
A Plex Custom Metadata Provider for yt-dlp downloads.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from collection_map import find_collection_map, load_map, resolve_collections, save_map
from metadata import (
    _BILIBILI_ID_RE,
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
PORT = int(os.environ.get("PORT", "8765"))
API_KEY = os.environ.get("API_KEY", "")

METADATA_KEY = "/movies/library/metadata"
MATCH_KEY = "/movies/library/metadata/matches"

# ── Video index ───────────────────────────────────────────────────────────────
# Maps video_id → absolute path to its .info.json file

_video_index: dict[str, str] = {}
_last_rebuild: float = 0.0
_REBUILD_COOLDOWN = 60.0


def build_index(data_path: str) -> dict[str, str]:
    """Walk data_path and index all .info.json files by video ID."""
    index: dict[str, str] = {}

    def onerror(err: OSError) -> None:
        logger.warning("Index walk error at '%s' (errno %d) — skipping: %s", err.filename, err.errno, err)

    for root, _, files in os.walk(data_path, onerror=onerror):
        for f in files:
            if not f.endswith(".info.json"):
                continue
            video_id = extract_video_id(f)
            if video_id:
                index[video_id] = os.path.join(root, f)

    if not index:
        logger.warning("Index is empty — no .info.json files found under %s", data_path)
    else:
        logger.info("Indexed %d videos from %s", len(index), data_path)
    return index


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _video_index
    if not os.path.isdir(DATA_PATH):
        logger.error(
            "YOUTUBE_DATA_PATH '%s' does not exist or is not a directory. Refusing to start.",
            DATA_PATH,
        )
        raise RuntimeError(f"YOUTUBE_DATA_PATH '{DATA_PATH}' is not a directory")
    _video_index = build_index(DATA_PATH)
    yield


app = FastAPI(title="YAMP", lifespan=lifespan)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_info_json(video_id: str) -> dict:
    """Load info_json for a video, rebuilding the index once if not found."""
    global _video_index, _last_rebuild
    path = _video_index.get(video_id)
    if not path:
        if time.monotonic() - _last_rebuild > _REBUILD_COOLDOWN:
            _video_index = build_index(DATA_PATH)
            _last_rebuild = time.monotonic()
        path = _video_index.get(video_id)
    if not path:
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
    if not auth.startswith("Bearer ") or auth[len("Bearer "):] != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


def _validate_video_id(video_id: str) -> bool:
    """Return True if video_id matches a known YouTube or Bilibili ID format."""
    probe = f"[{video_id}]"
    return bool(_YOUTUBE_ID_RE.search(probe) or _BILIBILI_ID_RE.search(probe))


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
            "title": "YAMP — YouTube Auto Metadata Provider",
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

    video_id = extract_video_id(filename)
    if not video_id:
        safe_filename = "".join(c for c in filename[:256] if c >= " ")
        logger.warning("Could not extract video ID from filename: %s", safe_filename)
        return JSONResponse(_media_container([]))

    try:
        info_json = _get_info_json(video_id)
    except HTTPException:
        logger.warning("No info_json found for video ID: %s", video_id)
        return JSONResponse(_media_container([]))

    upload_date_raw = info_json.get("upload_date")
    title = info_json.get("title")

    if not upload_date_raw or not title:
        logger.warning("Missing upload_date or title in info_json for video ID: %s", video_id)
        return JSONResponse(_media_container([]))

    rating_key = f"youtube-{video_id}"
    upload_date = parse_upload_date(upload_date_raw)

    return JSONResponse(
        _media_container([
            {
                "ratingKey": rating_key,
                "key": f"{METADATA_KEY}/{rating_key}",
                "guid": f"{IDENTIFIER}://movie/{video_id}",
                "type": "movie",
                "title": title,
                "year": upload_date.year,
                "originallyAvailableAt": upload_date.isoformat(),
            }
        ])
    )


@app.get("/movies/library/metadata/{rating_key}/images")
async def get_images(rating_key: str):
    """Images endpoint — return poster/backdrop URLs for a video."""
    if not rating_key.startswith("youtube-"):
        raise HTTPException(status_code=404)
    video_id = rating_key[len("youtube-"):]
    if not _validate_video_id(video_id):
        raise HTTPException(status_code=404)

    info_json = _get_info_json(video_id)
    images = []
    if thumb := info_json.get("thumbnail"):
        images.append({"type": "coverPoster", "url": thumb})

    return JSONResponse({
        "MediaContainer": {
            "offset": 0,
            "totalSize": len(images),
            "size": len(images),
            "Image": images,
        }
    })


@app.get("/movies/library/metadata/{rating_key}")
async def get_metadata(rating_key: str):
    """Full metadata endpoint — called after a successful match."""
    if not rating_key.startswith("youtube-"):
        raise HTTPException(status_code=404)
    video_id = rating_key[len("youtube-"):]
    if not _validate_video_id(video_id):
        raise HTTPException(status_code=404)

    info_json = _get_info_json(video_id)

    mapping_path = _collection_map_path()
    collections: list[str] = []
    if mapping_path:
        try:
            collections = await asyncio.to_thread(resolve_collections, info_json, mapping_path)
        except (OSError, ValueError) as e:
            logger.error("resolve_collections failed for '%s': %s", video_id, e)

    meta = build_metadata_response(info_json, collections, rating_key, IDENTIFIER, METADATA_KEY)
    return JSONResponse(_media_container([meta]))


# ── Collection management API (consumed by the React UI) ─────────────────────


class RuleModel(BaseModel):
    field: str
    match: Literal["exact", "in"]
    values: List[str]


class CollectionModel(BaseModel):
    name: str
    rules: List[RuleModel]


class CollectionsBody(BaseModel):
    collections: List[CollectionModel]


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
    data = load_map(mapping_path)
    return {
        "collections": data.get("collections", []),
        "unmatched_tags": data.get("unmatched_tags", {}),
        "matched_count": len(data.get("matched_ids", [])),
        "unmatched_count": len(data.get("unmatched_ids", [])),
    }


@app.put("/api/collections", dependencies=[Depends(_require_api_key)])
async def api_put_collections(body: CollectionsBody):
    mapping_path = _collection_map_path()
    if not mapping_path:
        raise HTTPException(status_code=404, detail="Collection map not found")
    data = load_map(mapping_path)
    data["collections"] = [c.model_dump() for c in body.collections]
    save_map(mapping_path, data)
    return {"ok": True}


@app.post("/api/rescan", dependencies=[Depends(_require_api_key)])
async def api_rescan():
    """Trigger a Plex metadata refresh on all libraries using this provider."""
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(status_code=400, detail="PLEX_URL and PLEX_TOKEN env vars not set")

    plex_headers = {"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        try:
            sections_resp = await client.get(
                f"{PLEX_URL}/library/sections",
                headers=plex_headers,
            )
            sections_resp.raise_for_status()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Plex server timed out")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Plex returned {e.response.status_code}")

        triggered = []
        for section in sections_resp.json().get("MediaContainer", {}).get("Directory", []):
            if section.get("agent") == IDENTIFIER:
                section_id = section["key"]
                if not str(section_id).isdigit():
                    logger.warning("Skipping section with non-numeric key: %r", section_id)
                    continue
                try:
                    resp = await client.put(
                        f"{PLEX_URL}/library/sections/{section_id}/refresh",
                        headers=plex_headers,
                        params={"force": 1},
                    )
                    resp.raise_for_status()
                    triggered.append(section_id)
                except httpx.TimeoutException:
                    logger.error("Timed out refreshing section %s", section_id)
                except httpx.HTTPStatusError as e:
                    logger.error("Failed to refresh section %s: HTTP %s", section_id, e.response.status_code)

    return {"triggered_sections": triggered}


@app.post("/api/index/rebuild", dependencies=[Depends(_require_api_key)])
async def api_rebuild_index():
    """Force a rebuild of the in-memory video index."""
    global _video_index
    _video_index = build_index(DATA_PATH)
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
