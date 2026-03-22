"""
Metadata helpers: extract video IDs from yt-dlp filenames and build Plex API responses.
"""

import re
from datetime import date, datetime
from pathlib import Path

# yt-dlp embeds the ID in square brackets before the extension: "Title [VIDEO_ID].mp4"
# YouTube IDs are 11 chars; Bilibili uses BV + alphanumeric; other extractors vary.
_YOUTUBE_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?:\.[^.\s]+)*$")
_BILIBILI_ID_RE = re.compile(r"\[([AB][Vv][A-Za-z0-9]+)\](?:\.[^.\s]+)*$")
_GENERIC_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{5,})\](?:\.[^.\s]+)*$")


def _require_fields(info_json: dict, *fields: str) -> None:
    """Raise ValueError if any of the required fields are missing from info_json."""
    missing = [f for f in fields if f not in info_json]
    if missing:
        raise ValueError(f"info_json is missing required field(s): {', '.join(missing)}")


def extract_video_id(filename: str) -> str | None:
    """Extract video ID from a yt-dlp filename."""
    basename = Path(filename).name
    m = _YOUTUBE_ID_RE.search(basename) or _BILIBILI_ID_RE.search(basename) or _GENERIC_ID_RE.search(basename)
    return m.group(1) if m else None


def parse_upload_date(upload_date: str) -> date:
    """Parse yt-dlp upload_date (YYYYMMDD) to a date object."""
    return datetime.strptime(upload_date, "%Y%m%d").date()


def build_metadata_response(
    info_json: dict,
    collections: list[str],
    rating_key: str,
    identifier: str,
    metadata_key: str,
) -> dict:
    """Build a Plex metadata response dict from a yt-dlp info_json."""
    _require_fields(info_json, "upload_date", "id", "title")
    upload_date = parse_upload_date(info_json["upload_date"])
    video_id = info_json["id"]

    meta: dict = {
        "ratingKey": rating_key,
        "key": f"{metadata_key}/{rating_key}",
        "guid": f"{identifier}://movie/{video_id}",
        "type": "movie",
        "title": info_json["title"],
        "summary": info_json.get("description", ""),
        "year": upload_date.year,
        "originallyAvailableAt": upload_date.isoformat(),
        "duration": int(info_json.get("duration", 0)) * 1000,
        "studio": info_json.get("extractor", ""),
    }

    if categories := info_json.get("categories"):
        meta["Genre"] = [{"tag": c} for c in categories]

    if channel := info_json.get("channel"):
        meta["Director"] = [{"tag": channel}]

    if collections:
        meta["Collection"] = [{"tag": c} for c in collections]

    if thumb := info_json.get("thumbnail"):
        meta["thumb"] = thumb

    return meta
