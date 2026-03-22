import json
from datetime import date
from pathlib import Path

import pytest

from metadata import build_metadata_response, extract_video_id, parse_upload_date

FIXTURES = Path(__file__).parent / "fixtures"
IDENTIFIER = "tv.plex.agents.custom.yamp"
METADATA_KEY = "/movies/library/metadata"


def _load_info() -> dict:
    with open(FIXTURES / "sample.info.json", encoding="utf-8") as f:
        return json.load(f)


# ── extract_video_id ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("GoGo Penguin - Live [dQw4w9WgXcQ].mp4", "dQw4w9WgXcQ"),
        ("GoGo Penguin - Live [dQw4w9WgXcQ].info.json", "dQw4w9WgXcQ"),
        ("Some_Video_Title [abc123defgh].webm", "abc123defgh"),
        # Bilibili
        ("Some Bilibili Video [BV1464y1s7aG].mp4", "BV1464y1s7aG"),
        # Non-YouTube extractors with shorter numeric/alphanumeric IDs
        ("Broadcast_Special_2023 [9876543210].mp4", "9876543210"),
        ("Concert_Film [12345678].mp4", "12345678"),
        ("Documentary_Series [ab12345].mp4", "ab12345"),
        # No ID → None
        ("plain-filename.mp4", None),
        ("no_brackets_at_all.mp4", None),
    ],
)
def test_extract_video_id(filename, expected):
    assert extract_video_id(filename) == expected


# ── parse_upload_date ─────────────────────────────────────────────────────────


def test_parse_upload_date():
    assert parse_upload_date("20231015") == date(2023, 10, 15)


def test_parse_upload_date_year():
    d = parse_upload_date("20200101")
    assert d.year == 2020


# ── build_metadata_response ───────────────────────────────────────────────────


def test_basic_fields():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert result["title"] == info["title"]
    assert result["summary"] == info["description"]
    assert result["year"] == 2023
    assert result["originallyAvailableAt"] == "2023-10-15"
    assert result["studio"] == "youtube"
    assert result["ratingKey"] == "youtube-dQw4w9WgXcQ"
    assert result["type"] == "movie"


def test_duration_converted_to_milliseconds():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert result["duration"] == info["duration"] * 1000


def test_guid_format():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert result["guid"] == f"{IDENTIFIER}://movie/{info['id']}"


def test_collections_populated():
    info = _load_info()
    result = build_metadata_response(info, ["GoGo Penguin", "Jazz"], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert {"tag": "GoGo Penguin"} in result["Collection"]
    assert {"tag": "Jazz"} in result["Collection"]


def test_empty_collections_omitted():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert "Collection" not in result


def test_categories_become_genres():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert {"tag": "Music"} in result["Genre"]
    assert {"tag": "Live Performance"} in result["Genre"]


def test_missing_categories_no_crash():
    info = _load_info()
    del info["categories"]
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert "Genre" not in result


def test_channel_becomes_director():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert {"tag": "GoGo Penguin Music"} in result["Director"]


def test_thumbnail_as_thumb():
    info = _load_info()
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert result["thumb"] == info["thumbnail"]


def test_missing_thumbnail_no_crash():
    info = _load_info()
    del info["thumbnail"]
    result = build_metadata_response(info, [], "youtube-dQw4w9WgXcQ", IDENTIFIER, METADATA_KEY)
    assert "thumb" not in result
