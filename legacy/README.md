# Legacy: YouTube as Movies Agent (Plex Bundle)

This is the original Python-based Plex metadata agent, built using Plex's legacy `.bundle` plugin framework.

## Status: Deprecated

Plex began removing support for legacy Python agents in 2026. As of PMS 1.43.0, legacy agents are hidden from new library creation. Plex plans to completely remove the system from future releases.

**This bundle is preserved for reference. New deployments should use [YAMP](../provider/) instead.**

## How it worked

1. Videos downloaded via yt-dlp land in a folder, each with a `.info.json` sidecar file
2. Plex picks up the video and calls the agent's `search` and `update` methods
3. The agent reads the `.info.json` to populate metadata (title, description, date, studio, genres)
4. It walks up the directory tree to find `_collection_map.json` and applies collection rules
5. Matched/unmatched video IDs and unused tags are written back to the map file

## Installation (legacy, not recommended)

Copy `youtube-as-movies-agent.bundle` to your Plex plugins directory:
- Linux: `$PLEX_HOME/Library/Application Support/Plex Media Server/Plug-ins/`
- macOS: `~/Library/Application Support/Plex Media Server/Plug-ins/`

Restart Plex, then create a Movie library using the "YouTube as Movies" agent.

## Migration

See [../provider/](../provider/) for the YAMP HTTP-based replacement, which supports the same `_collection_map.json` format and adds a web UI for collection management.
