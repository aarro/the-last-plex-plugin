<p align="center"><img src="assets/wordmark-b.svg" alt="YAMP" height="72"></p>

**YAMP** (Yet Another Media Provider) is a [Plex Custom Metadata Provider](https://developer.plex.tv/pms/) for YouTube videos downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp) or [MeTube](https://github.com/alexta69/metube).

It reads `.info.json` sidecar files to populate Plex with rich metadata — title, description, upload date, channel, genres, thumbnails — and organises videos into Plex collections using a rule-based system you manage through a built-in web UI.

## Why

Plex deprecated their legacy Python plugin framework in 2026. This replaces the original `.bundle` agent (preserved in [`legacy/`](legacy/)) with a proper HTTP-based provider that works with PMS 1.43.0+.

## Features

- **Auto-metadata** from `.info.json`: title, description, upload date, duration, channel (as director), genres, thumbnail as poster art
- **Thumbnail proxy** — YAMP serves all video thumbnails to Plex (proxying YouTube when no local file exists), so Plex doesn't need direct access to YouTube CDN
- **Collection rules** driven by tags, title substrings, or channel name
- **Collection poster images** — set a URL in the UI and YAMP pushes it to Plex as the collection artwork on save; existing Plex posters are pre-loaded when you open the editor
- **Web UI** at `http://localhost:8765` to add/edit/delete collections and rules
- **Discover panel** — browse unmatched (or all) videos, search by title/channel/tag, click any tag to instantly create a collection from it
- **Rescan button** — trigger a Plex metadata refresh directly from the UI
- **Fix Thumbnails button** — backfill YAMP-proxied thumbnails for all existing videos (one-time migration; new downloads are handled automatically)
- **Makefile** for common dev tasks: `make test`, `make build`, `make dev`, `make docker-up`, etc.
- **Docker Compose** setup with Plex + MeTube + YAMP all sharing one volume

## Requirements

- Plex Media Server **1.43.0+**
- Videos downloaded with yt-dlp or MeTube with `writeinfojson: true`
- Docker (recommended) or Python 3.11+ with [uv](https://github.com/astral-sh/uv)

## Quick Start

```bash
git clone https://github.com/aarro/the-last-plex-plugin
cd the-last-plex-plugin

# 1. Edit provider/docker-compose.yml — set the youtube-data volume device path
# 2. Create provider/.env with PLEX_URL, PLEX_TOKEN, PLEX_CLAIM, and YAMP_URL
#    YAMP_URL is the address Plex uses to load thumbnails (all thumbnails are proxied through YAMP)

make docker-up
```

Then in Plex: **Settings → Troubleshooting → Metadata Agents → Add Agent** → enter `http://<host>:8765/movies`.

See [CLAUDE.md](CLAUDE.md) for full documentation.

## Repo Layout

```
the-last-plex-plugin/
├── CLAUDE.md          # Full developer docs
├── provider/          # YAMP HTTP provider (FastAPI + React UI)
└── legacy/            # Original .bundle agent (reference only)
```

## Inspiration

- [ZeroQI's Youtube Agent](https://github.com/ZeroQI/YouTube-Agent.bundle)
- [JordyAlkema's Youtube-DL Agent](https://github.com/JordyAlkema/Youtube-DL-Agent.bundle)
- [TubeArchivist Plex Integration](https://github.com/tubearchivist/tubearchivist-plex)
