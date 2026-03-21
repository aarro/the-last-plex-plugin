# CLAUDE.md — YAMP Project Reference

## Repo Layout

```
the-last-plex-plugin/
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

On startup, YAMP walks `YOUTUBE_DATA_PATH` and builds an in-memory index: `{video_id → info_json_path}`.

When Plex calls the match endpoint with a filename, `extract_video_id()` in `metadata.py` uses a regex to pull the ID from the `[...]` suffix.

### Plex API Flow

1. **`GET /movies`** — Plex discovers the provider. Returns `MediaProvider` JSON with identifier `tv.plex.agents.custom.yamp`.
2. **`POST /movies/library/metadata/matches`** — Plex sends `{filename, title, year}`. We extract the video ID, find the `.info.json`, and return a match stub.
3. **`GET /movies/library/metadata/{rating_key}`** — Plex fetches full metadata. We read the `.info.json`, run collection matching, and return the full response.
4. **`GET /movies/library/metadata/{rating_key}/images`** — Returns the thumbnail as `coverPoster`. Prefers a local file (`.jpg`/`.png`/`.webp`) alongside the `.info.json`; falls back to the YouTube URL from `thumbnail`.

`rating_key` format: `youtube-{video_id}`.

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

Each collection can have an optional `image` URL. On `PUT /api/collections`, YAMP pushes that URL as the collection poster in Plex:

1. Connect to Plex via `plexapi` (`PLEX_URL` + `PLEX_TOKEN`)
2. Find the YAMP-managed library section (agent == `tv.plex.agents.custom.yamp`)
3. Find the existing Plex collection by name, or create it by matching YAMP-tracked videos against the collection rules
4. Call `plex_col.uploadPoster(url=image)` to set the poster

The 🖼 button in the UI is only shown when a collection has matched videos — this ensures the create-collection path always has items to work with.

If `PLEX_URL` / `PLEX_TOKEN` are not set, artwork push is skipped silently. Failures are surfaced per-collection in the `PUT /api/collections` response and shown in the UI status bar.

## Running Locally

### Backend (Python)

```bash
cd provider
uv sync
YOUTUBE_DATA_PATH=/path/to/your/youtube/downloads uv run uvicorn app:app --reload --port 8765
```

### UI (dev mode with proxy)

```bash
cd provider/ui
bun install
bun run dev          # proxies /api → localhost:8765
```

### Tests

```bash
uv --directory provider run pytest
```

### Docker (production)

```bash
cd provider
docker compose up -d --build
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
| `API_KEY`            | —        | Bearer token for write API endpoints (`PUT /api/collections`, `POST /api/rescan`, `POST /api/index/rebuild`). If unset, those endpoints are open (backward-compatible). |
| `YAMP_URL`           | —        | Public URL of this YAMP instance (e.g. `http://192.168.1.10:8765`). Required for Plex to load local thumbnails. |

## Key Files

- `provider/collection_map.py` — `match_video()`, `resolve_collections()`, `recompute_all_collections()`, `find_collection_map()`
- `provider/metadata.py` — `extract_video_id()`, `build_metadata_response()`
- `provider/app.py` — all FastAPI routes; `_sync_collection_artwork()`, `_find_matching_plex_items()`
- `provider/ui/src/App.jsx` — React root, state management, save/rescan actions
- `provider/ui/src/Collections.jsx` — collection editor (rules, name, poster image)
- `provider/ui/src/DiscoverPanel.jsx` — video browser; click any tag to create a collection from it
