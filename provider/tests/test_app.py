"""
Endpoint tests for app.py.

Uses httpx.AsyncClient with ASGITransport to exercise routes without
triggering the lifespan (which requires a real DATA_PATH directory).
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

import app as yamp_app
from app import app

FIXTURES = Path(__file__).parent / "fixtures"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data(tmp_path):
    info = json.loads((FIXTURES / "sample.info.json").read_bytes())
    info_path = tmp_path / f"{info['id']}.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    thumb = tmp_path / f"{info['id']}.jpg"
    thumb.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header
    return tmp_path, info


@pytest.fixture
def patched_app(tmp_data, monkeypatch):
    tmp_path, info = tmp_data
    index = {info["id"]: str(tmp_path / f"{info['id']}.info.json")}
    monkeypatch.setattr(yamp_app, "_video_index", index)
    monkeypatch.setattr(yamp_app, "DATA_PATH", str(tmp_path))
    return index, info, tmp_path


# ── /api/thumbnail/{video_id} ─────────────────────────────────────────────────


async def test_thumbnail_happy_path(patched_app):
    _, info, _ = patched_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")


async def test_thumbnail_png_mime_type(patched_app):
    _, info, tmp_path = patched_app
    # Replace the .jpg with a .png
    jpg = tmp_path / f"{info['id']}.jpg"
    jpg.unlink()
    png = tmp_path / f"{info['id']}.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")


async def test_thumbnail_invalid_video_id(patched_app):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/thumbnail/not-an-id")
    assert resp.status_code == 404


async def test_thumbnail_webp_mime_type(patched_app):
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    webp = tmp_path / f"{info['id']}.webp"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/webp")


async def test_thumbnail_no_local_file(patched_app):
    _, info, tmp_path = patched_app
    # Remove the thumbnail
    (tmp_path / f"{info['id']}.jpg").unlink()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 404


async def test_thumbnail_path_containment(patched_app):
    import tempfile

    _, info, data_path = patched_app
    # Create a file in a completely separate temp directory (genuinely outside DATA_PATH)
    with tempfile.TemporaryDirectory() as outside_dir:
        evil_file = Path(outside_dir) / "evil.jpg"
        evil_file.write_bytes(b"\xff\xd8\xff")

        # Replace the real thumbnail with a symlink pointing outside DATA_PATH
        link = data_path / f"{info['id']}.jpg"
        link.unlink()
        link.symlink_to(evil_file)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 404


# ── /api/videos ───────────────────────────────────────────────────────────────


async def test_api_videos_basic(patched_app):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    videos = resp.json()["videos"]
    assert len(videos) >= 1
    v = videos[0]
    for key in ("id", "title", "channel", "thumbnail", "upload_date", "collections", "matched"):
        assert key in v
    assert v["upload_date"] == "2023-10-15"
    assert isinstance(v["matched"], bool)
    assert isinstance(v["collections"], list)


async def test_api_videos_corrupt_file(patched_app):
    index, info, tmp_path = patched_app
    # Inject a corrupt entry alongside the valid one
    bad_path = tmp_path / "bad_id.info.json"
    bad_path.write_text("{{not json}}", encoding="utf-8")
    index["bad_id"] = str(bad_path)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    ids = [v["id"] for v in resp.json()["videos"]]
    assert info["id"] in ids
    assert "bad_id" not in ids


async def test_api_videos_no_map(patched_app):
    """No _collection_map.json → videos still returned, all unmatched."""
    _, info, tmp_path = patched_app
    # Ensure no map file exists
    map_file = tmp_path / "_collection_map.json"
    if map_file.exists():
        map_file.unlink()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    videos = resp.json()["videos"]
    assert any(v["id"] == info["id"] for v in videos)
    for v in videos:
        assert v["matched"] is False


# ── /api/plex/sections ────────────────────────────────────────────────────────


async def test_plex_sections_no_plex_config(monkeypatch):
    monkeypatch.setattr(yamp_app, "PLEX_URL", "")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/plex/sections")
    assert resp.status_code == 400


def _make_plex_mock(side_effect=None, return_value=None):
    """Build a mock httpx.AsyncClient context manager for patching httpx.AsyncClient."""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    if side_effect is not None:
        mock_client.get.side_effect = side_effect
    else:
        mock_client.get.return_value = return_value
    return mock_client


def _make_sections_response(sections):
    """Build a mock httpx response for the Plex /library/sections endpoint."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"MediaContainer": {"Directory": sections}}
    return resp


def _make_rescan_mock(sections, put_side_effect=None):
    """Build a mock httpx.AsyncClient for api_rescan tests (GET sections + PUT refresh)."""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.get.return_value = _make_sections_response(sections)
    if put_side_effect is not None:
        mock_client.put.side_effect = put_side_effect
    else:
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        mock_client.put.return_value = put_resp
    return mock_client


async def test_plex_sections_connect_error(monkeypatch):
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://unreachable.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_client = _make_plex_mock(side_effect=httpx.ConnectError("connection refused"))
    # Create the test client BEFORE the patch so it isn't replaced by the mock
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex/sections")
    assert resp.status_code == 503
    assert "Could not reach Plex" in resp.json()["detail"]


async def test_plex_sections_timeout(monkeypatch):
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://unreachable.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_client = _make_plex_mock(side_effect=httpx.TimeoutException("timed out"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex/sections")
    assert resp.status_code == 504


async def test_plex_sections_bad_json(monkeypatch):
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://unreachable.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    # Use a MagicMock response so raise_for_status() is a no-op and json() raises
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("not JSON", "", 0)
    mock_client = _make_plex_mock(return_value=mock_response)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex/sections")
    assert resp.status_code == 502


# ── PUT /api/collections ─────────────────────────────────────────────────────


async def test_api_put_collections_recompute_failure(patched_app, monkeypatch):
    """Collections are saved but recompute raises → HTTP 500 with descriptive message."""
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )

    def _fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(yamp_app, "recompute_all_collections", _fail)

    body = {"collections": [{"name": "Test", "rules": [{"field": "title", "match": "in", "values": ["test"]}]}]}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json=body)
    assert resp.status_code == 500
    assert "Collections saved but recompute failed" in resp.json()["detail"]


# ── POST /api/rescan ──────────────────────────────────────────────────────────


async def test_api_rescan_happy_path(monkeypatch):
    """Sections fetch returns one YAMP-managed section; refresh is triggered."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "1", "agent": yamp_app.IDENTIFIER, "title": "YouTube Movies"}]
    mock_client = _make_rescan_mock(sections)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    assert resp.json() == {"triggered_sections": ["1"]}


async def test_api_rescan_no_yamp_sections(monkeypatch):
    """Sections with a different agent are skipped; triggered_sections is empty."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "2", "agent": "com.plexapp.agents.imdb", "title": "Movies"}]
    mock_client = _make_rescan_mock(sections)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    assert resp.json() == {"triggered_sections": []}


async def test_api_rescan_per_section_timeout(monkeypatch):
    """Per-section timeout is logged and skipped; the overall request still succeeds."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "1", "agent": yamp_app.IDENTIFIER, "title": "YouTube Movies"}]
    mock_client = _make_rescan_mock(sections, put_side_effect=httpx.TimeoutException("timed out"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    assert resp.json() == {"triggered_sections": []}


async def test_api_rescan_non_numeric_key(monkeypatch):
    """YAMP section with a non-numeric key is warned and skipped."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "abc", "agent": yamp_app.IDENTIFIER, "title": "YouTube Movies"}]
    mock_client = _make_rescan_mock(sections)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    assert resp.json() == {"triggered_sections": []}


# ── Plex provider endpoints ───────────────────────────────────────────────────


async def test_match_happy_path(patched_app):
    """Valid filename with embedded video ID returns a match stub with correct ratingKey."""
    _, info, _ = patched_app
    filename = f"{info['title']} [{info['id']}].mp4"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": filename})
    assert resp.status_code == 200
    results = resp.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["ratingKey"] == f"youtube-{info['id']}"
    assert results[0]["type"] == "movie"
    assert results[0]["title"] == info["title"]


async def test_match_no_video_id(patched_app):
    """Filename with no extractable ID → empty match list, not a 4xx/5xx."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": "random video.mp4"})
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Metadata"] == []


async def test_match_unknown_video_id(patched_app):
    """Valid ID format but not in index → empty match list."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/movies/library/metadata/matches",
            json={"filename": "Some Video [aaaaaaaaaaa].mp4"},
        )
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Metadata"] == []


async def test_get_metadata_happy_path(patched_app):
    """Valid rating_key returns full metadata with correct title and year."""
    _, info, _ = patched_app
    rating_key = f"youtube-{info['id']}"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{rating_key}")
    assert resp.status_code == 200
    meta = resp.json()["MediaContainer"]["Metadata"][0]
    assert meta["title"] == info["title"]
    assert meta["year"] == 2023  # upload_date "20231015"
    assert meta["ratingKey"] == rating_key


async def test_get_metadata_non_youtube_prefix(patched_app):
    """rating_key not starting with 'youtube-' → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/imdb-tt1234567")
    assert resp.status_code == 404


async def test_get_metadata_unknown_id(patched_app):
    """Valid prefix but unknown video ID → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/youtube-aaaaaaaaaaa")
    assert resp.status_code == 404


async def test_get_metadata_resolve_collections_failure(patched_app, monkeypatch):
    """If resolve_collections raises, endpoint still returns 200 with empty collections."""
    _, info, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )

    def _fail(*_args):
        raise OSError("disk error")

    monkeypatch.setattr(yamp_app, "resolve_collections", _fail)

    rating_key = f"youtube-{info['id']}"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{rating_key}")
    assert resp.status_code == 200
    meta = resp.json()["MediaContainer"]["Metadata"][0]
    assert meta["title"] == info["title"]
    assert meta.get("Collection", []) == []
