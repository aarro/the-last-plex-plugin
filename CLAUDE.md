# CLAUDE.md — YAMP Project Reference

## Repo Layout

```
the-last-plex-plugin/
├── Makefile                              # Common dev tasks (test, build, dev, docker-*)
├── legacy/
│   └── youtube-as-movies-agent.bundle/   # Original Python .bundle (reference only)
└── provider/                             # YAMP — the active HTTP provider
    ├── app.py                            # FastAPI: Plex endpoints + collection REST API
    ├── collection_map.py                 # Collection rule matching logic
    ├── metadata.py                       # yt-dlp info_json → Plex metadata mapping
    ├── pyproject.toml                    # uv-managed dependencies
    ├── Dockerfile                        # Multi-stage: bun (UI build) + python/uv (server)
    ├── docker-compose.yml                # Template: plex + metube + yamp
    ├── tests/                            # pytest tests for Python logic
    │   ├── test_app.py                   # FastAPI endpoint tests
    │   ├── test_collection_map.py        # Collection matching logic tests
    │   ├── test_metadata.py              # Metadata mapping tests
    │   └── fixtures/                     # sample.info.json, _collection_map.json
    └── ui/                               # React SPA (Vite + bun)
        ├── index.html
        ├── package.json
        ├── vite.config.js
        └── src/
            ├── main.jsx                  # Entry point
            ├── App.jsx                   # Root: fetches data, owns state, action bar
            ├── App.css                   # Global styles
            ├── Collections.jsx           # Collection list with rule editor + image picker
            └── DiscoverPanel.jsx         # Video browser: search unmatched/all, click tags to create collections
```

## How YAMP Works

### File Discovery

yt-dlp names files as: `Video Title [VIDEO_ID].mp4` with a sidecar `Video Title [VIDEO_ID].info.json`.

On startup, YAMP walks `YOUTUBE_DATA_PATH` and builds two in-memory structures:
- **`_video_index`**: `{video_id → info_json_path}` — used to locate `.info.json` files
- **`_video_meta_cache`**: `{video_id → MATCH_FIELDS subset}` — pre-loaded metadata for the rule engine, eliminating disk I/O during collection recompute

When Plex calls the match endpoint with a filename, `extract_video_id()` in `metadata.py` uses a regex to pull the ID from the `[...]` suffix. Three patterns are tried in order: YouTube (exactly 11 chars), Bilibili (`BV` prefix), and a general fallback (5+ alphanumeric chars) that covers other yt-dlp extractors with shorter or differently-shaped IDs.

For files with no bracket-wrapped ID in the filename at all (e.g. non-standard yt-dlp output templates), `build_index` falls back to reading `info["id"]` directly from the JSON. The stem index (`_stem_index`) is then populated with that entry, enabling the Tier-3 stem-based lookup in the match endpoint.

**New video self-registration:** when Plex matches a file not yet in the index, YAMP checks for the sidecar `.info.json` alongside the media file path that Plex provided (`_try_index_from_filename()`). If found, that single entry is added to both `_video_index` and `_video_meta_cache` immediately — no full directory walk required. This means new downloads are picked up on first Plex scan with no delay.

### Plex API Flow

1. **`GET /movies`** — Plex discovers the provider. Returns `MediaProvider` JSON with identifier `tv.plex.agents.custom.yamp`.
2. **`POST /movies/library/metadata/matches`** — Plex sends `{filename, title, year}`. We extract the video ID, find the `.info.json`, and return a match stub.
3. **`GET /movies/library/metadata/{rating_key}`** — Plex fetches full metadata. We read the `.info.json`, run collection matching, and return the full response.
4. **`GET /movies/library/metadata/{rating_key}/images`** — Returns the thumbnail as `coverPoster` via YAMP's proxy. The URL is derived from the incoming request (`request.base_url`) so Plex always loads thumbnails through YAMP — no `YAMP_URL` env var needed. `YAMP_URL` overrides this only for reverse-proxy setups.

`rating_key` format: bare `{video_id}` (e.g. `dQw4w9WgXcQ`).

### Metadata Mapping

| `.info.json` field  | Plex field              |
|---------------------|-------------------------|
| `title`             | `title`                 |
| `description`       | `summary`               |
| `upload_date`       | `originallyAvailableAt` (YYYY-MM-DD), `year` |
| `duration` (sec)    | `duration` (sec × 1000 → ms) |
| `extractor`         | `studio`                |
| `categories`        | `Genre[].tag`           |
| `channel`           | `Director[].tag`        |
| `thumbnail`         | `thumb`                 |
| collection map      | `Collection[].tag`      |

### Collection Map (`_collection_map.json`)

Lives at `YOUTUBE_DATA_PATH/_collection_map.json`. Schema:

```json
{
  "collections": [
    {
      "name": "GoGo Penguin",
      "image": "https://example.com/poster.jpg",
      "rules": [
        { "field": "tags",    "values": ["gogo penguin"], "match": "exact" },
        { "field": "title",   "values": ["gogo penguin"], "match": "in"    },
        { "field": "channel", "values": ["gogo penguin"], "match": "exact" }
      ]
    }
  ],
  "matched_ids":    ["video_id_1"],
  "unmatched_ids":  ["video_id_2"],
  "unmatched_tags": { "jazz": 14, "live": 9 }
}
```

**Match types:**
- `exact` — set intersection (lowercased)
- `in` — substring match (rule value contained in metadata value)

**Tag behaviour:** tags are consumed on match to prevent the same tag matching multiple collections.

**State tracking:** `matched_ids` prevents reprocessing on subsequent Plex scans. `unmatched_tags` surfaces patterns for new collections (sorted by frequency, visible in the UI).

### Collection Artwork

Each collection can have an optional `image` URL. On `PUT /api/collections`, YAMP:

1. Saves the new collection list to disk immediately
2. Runs collection matching (skipped entirely if only image/name changed — rules must differ)
3. When rules changed: schedules a **Plex rescan first**, then **artwork sync** as background tasks (non-blocking). Image-only saves skip the rescan — no new collections to discover.
4. Returns immediately with match counts and a `plex_sync: true` flag (only set when background tasks were actually queued, not just when credentials are present)

The artwork sync itself:
1. Connects to Plex via `plexapi` (`PLEX_URL` + `PLEX_TOKEN`)
2. Finds the YAMP-managed library section (agent == `tv.plex.agents.custom.yamp`)
3. Finds the existing Plex collection by name, or creates it by matching YAMP-tracked videos against the collection rules
4. Calls `plex_col.uploadPoster(url=image)` to set the poster

If the collection isn't found in Plex on the first attempt (e.g. rescan hasn't completed yet), `_sync_collection_artwork_bg` retries once after `_ARTWORK_RETRY_DELAY` seconds (default: 30). Sync failures are logged server-side.

Artwork is only synced for collections where rules or the image URL actually changed (not all collections with images on every save).

The 📷 button in the UI is only shown when a collection has matched videos — this ensures the create-collection path always has items to work with.

**Incremental recompute:** `diff_collections(old, new)` compares collection lists by name and returns the set of changed collections plus a `has_changes` flag. When `has_changes` is false (image/name-only edit), the recompute step is skipped entirely. When rules do change, `recompute_all_collections` uses `_video_meta_cache` instead of reading `.info.json` files from disk.

**Pre-populating from Plex:** `GET /api/collections` fetches existing collection poster paths from Plex (via `_fetch_plex_collection_thumbs()`) and includes a `plex_thumb` field per collection. This is a relative proxy path (`/api/plex-collection-thumb?path=…`) so the Plex token never reaches the browser. In the UI, `plex_thumb` is used as a display-only preview (shown in the card header and image editor preview) but is never written into the URL input field or saved as `collection.image` — only absolute `https://` URLs entered by the user are persisted.

If `PLEX_URL` / `PLEX_TOKEN` are not set, artwork push, Plex rescan, and `plex_thumb` fetch are all skipped silently.

### Thumbnail Proxy

`GET /api/thumbnail/{video_id}` serves thumbnails to both the YAMP UI and (via the images endpoint) to Plex:

1. If a local image file (`.jpg`/`.jpeg`/`.png`/`.webp`) exists alongside the `.info.json`, serve it directly via `FileResponse`.
2. Otherwise, proxy the remote `thumbnail` URL from `info_json` via httpx (useful when Plex can't reach the external CDN directly).

`GET /api/plex-collection-thumb?path=…` proxies Plex collection poster images server-side (keeps the Plex token out of the browser).

**Fix Thumbnails (`POST /api/thumbnails/fix`)** iterates every item in YAMP-managed Plex sections and uploads the correct thumbnail. Smart overwrite logic:
- If YAMP has content (local image file OR YouTube URL in meta_cache) → upload to Plex (always overwrites).
- If YAMP has no content → skip (preserves any existing Plex poster; prevents a 404/empty upload from clearing it).

## Running Locally

A `Makefile` at the repo root wraps all common tasks:

```bash
make test          # run pytest
make lint          # lint + auto-fix Python (ruff) and UI (biome)
make build         # build React UI
make dev           # backend dev server (port 8765, auto-reload)
make dev-ui        # UI dev server (proxies /api → localhost:8765)
make docker-build  # build Docker image
make docker-up     # start containers (detached)
make docker-down   # stop containers
make logs          # tail Docker logs
```

### Manual commands

```bash
# Backend
uv --directory provider run uvicorn app:app --reload --port 8765

# UI
bun run --cwd=provider/ui dev

# Tests
uv --directory provider run pytest

# Docker
docker compose --project-directory provider up -d --build
```

Edit `docker-compose.yml`: set the `device` path under `volumes.youtube-data` and populate `.env` with `PLEX_URL`, `PLEX_TOKEN`, `PLEX_CLAIM`.

## Registering with Plex

1. Start YAMP (`docker compose up` or `uv run uvicorn ...`)
2. In Plex Web: **Settings → Troubleshooting → Metadata Agents → Add Agent**
3. Enter the YAMP URL: `http://<host>:8765/movies`
4. Create or edit a Movie library → set Agent to "YAMP"

## Environment Variables

| Variable             | Default  | Purpose                              |
|----------------------|----------|--------------------------------------|
| `YOUTUBE_DATA_PATH`  | `/data`  | Root of yt-dlp downloads             |
| `PLEX_URL`           | —        | e.g. `http://192.168.1.10:32400`     |
| `PLEX_TOKEN`         | —        | Your X-Plex-Token                    |
| `PORT`               | `8765`   | Port the server listens on           |
| `API_KEY`            | —        | Bearer token for write API endpoints (`PUT /api/collections`, `POST /api/rescan`, `POST /api/thumbnails/fix`, `POST /api/index/rebuild`). If unset, those endpoints are open (backward-compatible). |
| `YAMP_URL`           | —        | Override for YAMP's own base URL. Normally not needed — YAMP derives its URL from the incoming request (Plex already knows it). Set this only if YAMP is behind a reverse proxy that rewrites the `Host` header. |

## Key Files

- `Makefile` — common dev tasks (test, build, dev, docker-*)
- `provider/collection_map.py` — `MATCH_FIELDS`, `diff_collections()`, `match_video()`, `resolve_collections()`, `recompute_all_collections()`, `find_collection_map()`
- `provider/metadata.py` — `extract_video_id()` (YouTube/Bilibili/generic regex), `build_metadata_response()`
- `provider/app.py` — all FastAPI routes; `build_index()` (with info.json ID fallback), `build_meta_cache()`, `_video_id_from_plex_item()`, `_has_local_thumbnail()`, `_do_rescan()`, `_do_rescan_bg()`, `_sync_collection_artwork()`, `_sync_collection_artwork_bg()` (with retry), `_find_matching_plex_items()`, `_fetch_plex_collection_thumbs()`, `_fix_all_thumbnails()`, `_try_index_from_filename()`, thumbnail proxy + Plex collection thumb proxy; `_PLEX_ERRS` tuple (module-level catch-all for Plex/network/XML errors)
- `provider/ui/src/App.jsx` — React root, state management, save/rescan/fix-thumbnails actions
- `provider/ui/src/Collections.jsx` — collection editor (rules, name, poster image); plex_thumb shown as display-only preview
- `provider/ui/src/DiscoverPanel.jsx` — video browser; click any tag to create a collection from it
