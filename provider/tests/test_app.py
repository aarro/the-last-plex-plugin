"""
Endpoint tests for app.py.

Uses httpx.AsyncClient with ASGITransport to exercise routes without
triggering the lifespan (which requires a real DATA_PATH directory).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import requests
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
    stem_index = {info["id"]: info["id"]}  # stem of "{id}.info.json" is "{id}"
    monkeypatch.setattr(yamp_app, "_video_index", index)
    monkeypatch.setattr(yamp_app, "_stem_index", stem_index)
    monkeypatch.setattr(yamp_app, "_video_meta_cache", {})  # empty → falls back to disk reads
    monkeypatch.setattr(yamp_app, "DATA_PATH", str(tmp_path))
    return index, info, tmp_path


# ── build_index ───────────────────────────────────────────────────────────────


def test_build_index_parent_dir_pattern(tmp_path):
    """ID in the containing directory is indexed when the filename has no ID."""
    from app import build_index

    video_id = "DNh1Ynj5ILU"
    # MeTube layout: Channel [CHANNEL_ID]/Title [VIDEO_ID]/Title.info.json
    video_dir = tmp_path / "Two_Another [UCvtf-i26Ecd]" / f"Live at KOKO [{video_id}]"
    video_dir.mkdir(parents=True)
    info_file = video_dir / "Live at KOKO.info.json"
    info_file.write_text(json.dumps({"id": video_id, "title": "Live at KOKO"}), encoding="utf-8")

    index, stem_index = build_index(str(tmp_path))

    assert video_id in index
    assert index[video_id] == str(info_file)
    assert stem_index.get("Live at KOKO") == video_id


def test_build_index_filename_id_takes_priority(tmp_path):
    """When both filename and parent directory have IDs, the filename ID wins."""
    from app import build_index

    file_id = "fileIDabcde"  # 11 chars
    dir_id = "dirIDvwxyz1"  # 11 chars
    video_dir = tmp_path / f"Video [{dir_id}]"
    video_dir.mkdir()
    info_file = video_dir / f"Video [{file_id}].info.json"
    info_file.write_text(json.dumps({"id": file_id, "title": "Video"}), encoding="utf-8")

    index, _ = build_index(str(tmp_path))

    assert file_id in index
    assert dir_id not in index


# ── _video_id_from_plex_item ──────────────────────────────────────────────────


def _make_item(guid: str):
    item = MagicMock()
    item.guid = guid
    return item


def test_video_id_from_plex_item_yamp_guid():
    from app import _video_id_from_plex_item

    item = _make_item("tv.plex.agents.custom.yamp://movie/dQw4w9WgXcQ")
    assert _video_id_from_plex_item(item) == "dQw4w9WgXcQ"


def test_video_id_from_plex_item_unknown_guid_returns_none():
    from app import _video_id_from_plex_item

    item = _make_item("com.plexapp.agents.imdb://tt1234567?lang=en")
    assert _video_id_from_plex_item(item) is None


@pytest.mark.parametrize(
    "guid,stem_index,expected",
    [
        # ID embedded in parent directory name (MeTube layout: "Title [ID]/Title.mp4")
        (
            "com.plexapp.agents.youtube-as-movies://youtube-as-movies|"
            "%2Fdata%2FBroadcast_Special_2023%20%5B9876543210%5D%2FBroadcast_Special_2023%2Emp4"
            "|aabbccdd?lang=en",
            {},
            "9876543210",
        ),
        # ID embedded in parent directory name (8-digit numeric)
        (
            "com.plexapp.agents.youtube-as-movies://youtube-as-movies|"
            "%2Fdata%2FChannel%20%5BUCabc123%5D%2FConcert_Film%20%5B12345678%5D%2FConcert_Film%2Emp4"
            "|aabbccdd?lang=en",
            {},
            "12345678",
        ),
        # ID embedded in parent directory name (short alphanumeric)
        (
            "com.plexapp.agents.youtube-as-movies://youtube-as-movies|"
            "%2Fdata%2FDocumentary_Series%20%5Bab12345%5D%2FDocumentary_Series%2Emp4"
            "|aabbccdd?lang=en",
            {},
            "ab12345",
        ),
        # No brackets anywhere — ID resolved via stem_index fallback
        (
            "com.plexapp.agents.youtube-as-movies://youtube-as-movies|"
            "%2Fdata%2FChannel%2FConcert_Film_-__ab12345_original%2Emp4"
            "|aabbccdd?lang=en",
            {"Concert_Film_-__ab12345_original": "ab12345"},
            "ab12345",
        ),
    ],
)
def test_video_id_from_plex_item_legacy_guid(guid, stem_index, expected):
    from app import _video_id_from_plex_item

    item = _make_item(guid)
    assert _video_id_from_plex_item(item, stem_index) == expected


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
    """When no local file exists, the endpoint proxies the remote thumbnail URL."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    fake_image = b"\xff\xd8\xff\xe0fake"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_image
    mock_resp.headers = {"content-type": "image/jpeg"}
    with patch("app.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 200
    assert resp.content == fake_image


async def test_thumbnail_path_containment(patched_app):
    """A symlink escaping DATA_PATH is blocked; the endpoint falls back to proxying the remote URL."""
    import tempfile

    _, info, data_path = patched_app
    with tempfile.TemporaryDirectory() as outside_dir:
        evil_file = Path(outside_dir) / "evil.jpg"
        evil_file.write_bytes(b"\xff\xd8\xff")

        # Replace the real thumbnail with a symlink pointing outside DATA_PATH
        link = data_path / f"{info['id']}.jpg"
        link.unlink()
        link.symlink_to(evil_file)

        fake_image = b"\xff\xd8\xff\xe0safe"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = fake_image
        mock_resp.headers = {"content-type": "image/jpeg"}
        with patch("app.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"/api/thumbnail/{info['id']}")
        # Must proxy the safe remote URL, not serve the evil symlinked file
        assert resp.status_code == 200
        assert resp.content == fake_image
        assert resp.content != b"\xff\xd8\xff"


async def test_thumbnail_upstream_non_200(patched_app):
    """Upstream returns non-200 → YAMP returns 502."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_client = _make_plex_mock(return_value=mock_resp)
    # Create the test client BEFORE the patch so it isn't replaced by the mock
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 502


async def test_thumbnail_upstream_timeout(patched_app):
    """Upstream timeout → YAMP returns 504."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    mock_client = _make_plex_mock(side_effect=httpx.TimeoutException("timeout"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 504


async def test_thumbnail_upstream_request_error(patched_app):
    """Upstream network error → YAMP returns 502."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    mock_client = _make_plex_mock(side_effect=httpx.RequestError("err"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get(f"/api/thumbnail/{info['id']}")
    assert resp.status_code == 502


# ── /api/videos ───────────────────────────────────────────────────────────────


async def test_api_videos_basic(patched_app):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    videos = resp.json()["videos"]
    assert len(videos) >= 1
    v = videos[0]
    for key in ("id", "title", "channel", "thumbnail", "upload_date", "collections", "matched", "tags"):
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
    data = resp.json()
    ids = [v["id"] for v in data["videos"]]
    assert info["id"] in ids
    assert "bad_id" not in ids
    assert "bad_id" in data.get("skipped_videos", [])


async def test_api_videos_unicode_decode_error(patched_app):
    """info.json with non-UTF-8 bytes → video is skipped, not a 500."""
    index, info, tmp_path = patched_app
    bad_path = tmp_path / "bad_unicode.info.json"
    bad_path.write_bytes(b"\xff\xfe not valid utf-8")
    index["bad_unicode"] = str(bad_path)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    data = resp.json()
    ids = [v["id"] for v in data["videos"]]
    assert info["id"] in ids
    assert "bad_unicode" not in ids
    assert "bad_unicode" in data.get("skipped_videos", [])


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


async def test_api_videos_collections_error_flag(patched_app):
    """Corrupt collection map → videos returned with collections_error flag."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text("{{bad json}}", encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("collections_error") is True
    assert "videos" in data


async def test_api_videos_thumbnail_proxy_url(patched_app, monkeypatch):
    """With YAMP_URL set and local thumbnail, thumbnail uses proxy URL."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "YAMP_URL", "http://yamp.local:8765")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    v = next(vid for vid in resp.json()["videos"] if vid["id"] == info["id"])
    assert v["thumbnail"] == f"http://yamp.local:8765/api/thumbnail/{info['id']}"


async def test_api_videos_thumbnail_relative_url(patched_app, monkeypatch):
    """With local thumbnail but no YAMP_URL, thumbnail is a relative URL."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "YAMP_URL", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/videos")
    assert resp.status_code == 200
    v = next(vid for vid in resp.json()["videos"] if vid["id"] == info["id"])
    assert v["thumbnail"] == f"/api/thumbnail/{info['id']}"


# ── /api/collections (GET) ────────────────────────────────────────────────────


async def test_api_get_collections_no_map(patched_app):
    """No _collection_map.json → empty collections with zero counts."""
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    if map_file.exists():
        map_file.unlink()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["collections"] == []
    assert data["matched_count"] == 0
    assert data["unmatched_count"] == 0


async def test_api_get_collections_happy_path(patched_app):
    """Map exists → returns collections, counts, and unmatched_tags."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps(
            {
                "collections": [{"name": "Alt-J", "rules": []}],
                "matched_ids": ["a", "b"],
                "unmatched_ids": ["c"],
                "unmatched_tags": {"jazz": 3},
            }
        ),
        encoding="utf-8",
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["collections"]) == 1
    assert data["matched_count"] == 2
    assert data["unmatched_count"] == 1
    assert data["unmatched_tags"] == {"jazz": 3}


async def test_api_get_collections_corrupt_map(patched_app):
    """Corrupt map file → HTTP 500."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text("{{bad json}}", encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/collections")
    assert resp.status_code == 500
    assert "_collection_map.json" in resp.json()["detail"]


async def test_api_get_collections_plex_unreachable(patched_app, monkeypatch):
    """Plex configured but _fetch_plex_collection_thumbs raises → 200 with plex_thumb_error, no 500."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps(
            {
                "collections": [{"name": "Alt-J", "rules": []}],
                "matched_ids": [],
                "unmatched_ids": [],
                "unmatched_tags": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    def _raise():
        raise requests.exceptions.RequestException("boom")

    monkeypatch.setattr(yamp_app, "_fetch_plex_collection_thumbs", _raise)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["collections"][0]["plex_thumb"] is None
    assert data.get("plex_thumb_error") is True


# ── /api/plex-collection-thumb ────────────────────────────────────────────────


async def test_plex_collection_thumb_no_plex_config(monkeypatch):
    """PLEX_URL/PLEX_TOKEN not set → 404."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/plex-collection-thumb?path=/library/collections/42/thumb")
    assert resp.status_code == 404


async def test_plex_collection_thumb_valid_path(monkeypatch):
    """Valid path → proxied image content returned."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    fake_img = b"\xff\xd8\xff\xe0plex"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_img
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_client = _make_plex_mock(return_value=mock_resp)
    # Create the test client BEFORE the patch so it isn't replaced by the mock
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex-collection-thumb?path=/library/collections/42/thumb")
    assert resp.status_code == 200
    assert resp.content == fake_img


@pytest.mark.parametrize(
    "path",
    [
        "/library/collections/42/thumb",
        "/library/collections/2348/composite/1730728751",
        "/library/collections/2348/composite/1730728751?width=400&height=600",
        "/library/metadata/2376/thumb/1730839921",
    ],
)
def test_plex_thumb_path_re_valid(path):
    """Regex accepts all Plex-observed thumb path shapes."""
    assert yamp_app._PLEX_THUMB_PATH_RE.match(path)


@pytest.mark.parametrize(
    "path",
    [
        "/library/collections/abc/thumb",  # non-numeric ID
        "/library/collections/42/thumb/extra",  # trailing non-numeric segment
        "/library/collections/../etc/passwd",  # path traversal
        "/library/collections/42/art",  # wrong endpoint
        "",  # empty
    ],
)
async def test_plex_collection_thumb_invalid_paths(monkeypatch, path):
    """Invalid paths → 400."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/plex-collection-thumb?path={path}")
    assert resp.status_code == 400


async def test_plex_collection_thumb_upstream_non_200(monkeypatch):
    """Upstream Plex non-200 → 502."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_client = _make_plex_mock(return_value=mock_resp)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex-collection-thumb?path=/library/collections/42/thumb")
    assert resp.status_code == 502


async def test_plex_collection_thumb_upstream_timeout(monkeypatch):
    """Upstream timeout → 504."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_client = _make_plex_mock(side_effect=httpx.TimeoutException("timeout"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex-collection-thumb?path=/library/collections/42/thumb")
    assert resp.status_code == 504


async def test_plex_collection_thumb_upstream_request_error(monkeypatch):
    """Upstream network error → 502."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_client = _make_plex_mock(side_effect=httpx.RequestError("err"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.httpx.AsyncClient", return_value=mock_client):
            resp = await client.get("/api/plex-collection-thumb?path=/library/collections/42/thumb")
    assert resp.status_code == 502


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


def _make_rescan_mock(sections, refresh_side_effect=None):
    """Build a mock httpx.AsyncClient for api_rescan tests (GET sections + GET refresh)."""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    sections_resp = _make_sections_response(sections)
    if refresh_side_effect is not None:
        mock_client.get.side_effect = [sections_resp, refresh_side_effect]
    else:
        refresh_resp = MagicMock()
        refresh_resp.raise_for_status = MagicMock()
        mock_client.get.side_effect = [sections_resp, refresh_resp]
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


async def test_api_put_collections_no_map(patched_app):
    """No _collection_map.json → 404."""
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    if map_file.exists():
        map_file.unlink()
    body = {"collections": []}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json=body)
    assert resp.status_code == 404


async def test_api_put_collections_duplicate_names(patched_app):
    """Duplicate collection names → 422 validation error."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    body = {
        "collections": [
            {"name": "Dup", "rules": []},
            {"name": "Dup", "rules": []},
        ]
    }
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json=body)
    assert resp.status_code == 422


async def test_api_key_no_token_gets_403(patched_app, monkeypatch):
    """When API_KEY is set, request without Authorization header gets 403."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "API_KEY", "supersecret")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": []})
    assert resp.status_code == 403


async def test_api_key_wrong_token_gets_403(patched_app, monkeypatch):
    """When API_KEY is set, wrong token gets 403."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "API_KEY", "supersecret")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/collections",
            json={"collections": []},
            headers={"Authorization": "Bearer wrongtoken"},
        )
    assert resp.status_code == 403


async def test_api_key_correct_token_accepted(patched_app, monkeypatch):
    """When API_KEY is set, correct Bearer token is accepted."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "API_KEY", "supersecret")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/collections",
            json={"collections": []},
            headers={"Authorization": "Bearer supersecret"},
        )
    assert resp.status_code == 200


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
    assert resp.json() == {"triggered_sections": [{"id": "1", "title": "YouTube Movies"}], "failed_sections": []}


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
    assert resp.json() == {"triggered_sections": [], "failed_sections": []}


async def test_api_rescan_per_section_timeout(monkeypatch):
    """Per-section timeout is logged and skipped; the overall request still succeeds."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "1", "agent": yamp_app.IDENTIFIER, "title": "YouTube Movies"}]
    mock_client = _make_rescan_mock(sections, refresh_side_effect=httpx.TimeoutException("timed out"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["triggered_sections"] == []
    assert data["failed_sections"] == [{"id": "1", "error": "timeout"}]


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
    assert resp.json() == {"triggered_sections": [], "failed_sections": []}


async def test_api_rescan_missing_title_key(monkeypatch):
    """Section without a 'title' key falls back to using the section key as title."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "1", "agent": yamp_app.IDENTIFIER}]  # no "title"
    mock_client = _make_rescan_mock(sections)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    assert resp.json() == {"triggered_sections": [{"id": "1", "title": "Section 1"}], "failed_sections": []}


async def test_api_rescan_no_plex_config(monkeypatch):
    """Missing PLEX_URL/PLEX_TOKEN → 400."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/rescan")
    assert resp.status_code == 400


# ── _sync_collection_artwork_bg ───────────────────────────────────────────────


async def test_artwork_bg_success_no_retry(monkeypatch):
    """First call succeeds → _sync_collection_artwork called once, sleep never called."""
    col = MagicMock()
    col.name = "Test"
    call_count = [0]

    def _sync(_col):
        call_count[0] += 1
        return {"ok": True, "created": False}

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _sync)
    with patch("app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await yamp_app._sync_collection_artwork_bg(col)

    assert call_count[0] == 1
    mock_sleep.assert_not_called()


async def test_artwork_bg_retry_on_not_found_succeeds(monkeypatch):
    """First call returns not_found_in_plex → sleep then retry; retry succeeds."""
    col = MagicMock()
    col.name = "Test"
    results = [
        {"ok": False, "not_found_in_plex": True, "error": "'Test' not in Plex and no matched videos found"},
        {"ok": True, "created": True},
    ]
    call_count = [0]

    def _sync(_col):
        r = results[call_count[0]]
        call_count[0] += 1
        return r

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _sync)
    with patch("app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await yamp_app._sync_collection_artwork_bg(col)

    assert call_count[0] == 2
    mock_sleep.assert_called_once_with(yamp_app._ARTWORK_RETRY_DELAY)


async def test_artwork_bg_retry_also_fails(monkeypatch, caplog):
    """Both calls return not_found_in_plex → retry-failed error is logged."""
    import logging

    col = MagicMock()
    col.name = "Test"
    not_found = {"ok": False, "not_found_in_plex": True, "error": "'Test' not in Plex and no matched videos found"}

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", lambda _col: not_found)
    with patch("app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, caplog.at_level(logging.ERROR):
        await yamp_app._sync_collection_artwork_bg(col)

    mock_sleep.assert_called_once_with(yamp_app._ARTWORK_RETRY_DELAY)
    assert any("still not found in plex after retry" in r.message.lower() for r in caplog.records)


async def test_artwork_bg_non_not_found_failure_no_retry(monkeypatch):
    """First call fails without not_found_in_plex → no sleep, no retry."""
    col = MagicMock()
    col.name = "Test"
    monkeypatch.setattr(
        yamp_app,
        "_sync_collection_artwork",
        lambda _col: {"ok": False, "error": "Plex connection failed"},
    )
    with patch("app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await yamp_app._sync_collection_artwork_bg(col)
    mock_sleep.assert_not_called()


async def test_artwork_bg_exception_is_caught(monkeypatch, caplog):
    """Exception raised inside _sync_collection_artwork is caught; does not propagate."""
    import logging

    col = MagicMock()
    col.name = "Test"

    def _boom(_col):
        raise RuntimeError("unexpected crash")

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _boom)
    with caplog.at_level(logging.ERROR):
        await yamp_app._sync_collection_artwork_bg(col)  # must not raise

    assert any("unhandled exception" in r.message.lower() for r in caplog.records)


async def test_artwork_bg_cancelled_error_propagates(monkeypatch):
    """asyncio.CancelledError is re-raised, not swallowed, so shutdown can proceed."""
    import asyncio

    col = MagicMock()
    col.name = "Test"

    def _cancel(_col):
        raise asyncio.CancelledError()

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _cancel)
    with pytest.raises(asyncio.CancelledError):
        await yamp_app._sync_collection_artwork_bg(col)


async def test_artwork_bg_retry_non_not_found_failure(monkeypatch, caplog):
    """First call returns not_found_in_plex; retry returns a different failure (not not_found_in_plex).
    The else-branch logs 'retry failed' and does not re-raise."""
    import logging

    col = MagicMock()
    col.name = "Test"
    results = [
        {"ok": False, "not_found_in_plex": True, "error": "not found"},
        {"ok": False, "error": "Plex poster upload failed"},
    ]
    call_count = [0]

    def _sync(_col):
        r = results[call_count[0]]
        call_count[0] += 1
        return r

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _sync)
    with patch("app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, caplog.at_level(logging.ERROR):
        await yamp_app._sync_collection_artwork_bg(col)  # must not raise

    assert call_count[0] == 2
    mock_sleep.assert_called_once_with(yamp_app._ARTWORK_RETRY_DELAY)
    assert any("retry failed" in r.message.lower() for r in caplog.records)


def test_sync_collection_artwork_sections_failure(monkeypatch):
    """plex.library.sections() raises → returns ok:False without not_found_in_plex."""
    import requests.exceptions

    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_plex = MagicMock()
    mock_plex.library.sections.side_effect = requests.exceptions.ConnectionError("refused")
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        result = yamp_app._sync_collection_artwork(col)
    assert result["ok"] is False
    assert "not_found_in_plex" not in result
    assert "Could not list Plex sections" in result["error"]


def test_sync_collection_artwork_find_items_plex_error(monkeypatch):
    """_find_matching_plex_items raises _PLEX_ERRS → caught, returns ok:False without raising.

    Ensures transient Plex errors during item listing don't bypass the retry logic in
    _sync_collection_artwork_bg by propagating as an unhandled exception.
    """
    import requests.exceptions
    from plexapi.exceptions import NotFound

    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.collection.side_effect = NotFound("not found")
    # section.all() (called inside _find_matching_plex_items) raises a network error
    mock_section.all.side_effect = requests.exceptions.ConnectionError("network hiccup")
    mock_plex = MagicMock()
    mock_plex.library.sections.return_value = [mock_section]

    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is False
    assert "not_found_in_plex" not in result
    assert "Could not list items" in result["error"]


async def test_put_collections_plex_sync_flag_with_credentials(patched_app, monkeypatch):
    """plex_sync is True only when background tasks are actually queued, not just because credentials are set.

    empty→empty with no image changes: no tasks queued → plex_sync: false.
    """
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": []})
    assert resp.status_code == 200
    # empty→empty: no rule changes, no image changes → no tasks queued → plex_sync: false
    assert resp.json()["plex_sync"] is False
    mock_rescan.assert_not_awaited()


async def test_put_collections_rescan_triggered_on_rule_changes(patched_app, monkeypatch):
    """PUT /api/collections with rule changes → rescan IS triggered."""
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    # Adding a new collection is a rule change
    new_col = [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}], "image": None}]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": new_col})
    assert resp.status_code == 200
    mock_rescan.assert_awaited_once()


async def test_put_collections_rescan_only_on_rule_changes(patched_app, monkeypatch):
    """PUT /api/collections with only an image change → rescan is NOT triggered (no rule changes)."""
    _, _, tmp_path = patched_app
    existing = [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}], "image": None}]
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": existing, "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    mock_artwork = AsyncMock()
    monkeypatch.setattr(yamp_app, "_sync_collection_artwork_bg", mock_artwork)
    # Same rules, only image changed
    updated = [
        {
            "name": "Jazz",
            "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}],
            "image": "https://example.com/jazz.jpg",
        }
    ]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": updated})
    assert resp.status_code == 200
    mock_rescan.assert_not_awaited()
    mock_artwork.assert_awaited_once()


async def test_do_rescan_bg_catches_http_exception(monkeypatch, caplog):
    """HTTPException raised inside _do_rescan is caught and logged; does not propagate."""
    import logging

    from fastapi import HTTPException

    async def _raise():
        raise HTTPException(status_code=503, detail="Plex unreachable")

    monkeypatch.setattr(yamp_app, "_do_rescan", _raise)
    with caplog.at_level(logging.ERROR):
        await yamp_app._do_rescan_bg()  # must not raise

    assert any("Plex unreachable" in r.message for r in caplog.records)


async def test_do_rescan_bg_bare_exception_is_caught(monkeypatch, caplog):
    """RuntimeError raised inside _do_rescan is caught by the broad except clause; does not propagate."""
    import logging

    async def _raise():
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(yamp_app, "_do_rescan", _raise)
    with caplog.at_level(logging.ERROR):
        await yamp_app._do_rescan_bg()  # must not raise

    assert any("unhandled exception" in r.message.lower() for r in caplog.records)


async def test_do_rescan_bg_failed_sections_are_logged(monkeypatch, caplog):
    """failed_sections in _do_rescan result → error is logged."""
    import logging

    async def _return_failures():
        return {"triggered_sections": [], "failed_sections": [{"id": "1", "error": "timeout"}]}

    monkeypatch.setattr(yamp_app, "_do_rescan", _return_failures)
    with caplog.at_level(logging.ERROR):
        await yamp_app._do_rescan_bg()

    assert any("failed sections" in r.message.lower() for r in caplog.records)


def test_sync_collection_artwork_no_image(monkeypatch):
    """All image fields (image, art, logo, square_art) are None → early return with ok:False, no Plex call."""
    col = yamp_app.CollectionModel(name="Test", rules=[], image=None)
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    with patch("plexapi.server.PlexServer") as mock_plex_cls:
        result = yamp_app._sync_collection_artwork(col)
    mock_plex_cls.assert_not_called()
    assert result["ok"] is False
    assert "no image" in result["error"]


def test_sync_collection_artwork_no_yamp_sections(monkeypatch):
    """plex.library.sections() returns no YAMP-managed sections → ok:False, not not_found_in_plex."""
    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_plex = MagicMock()
    # Return sections with a different agent — none are YAMP-managed
    mock_section = MagicMock()
    mock_section.agent = "com.plexapp.agents.imdb"
    mock_plex.library.sections.return_value = [mock_section]
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        result = yamp_app._sync_collection_artwork(col)
    assert result["ok"] is False
    assert "not_found_in_plex" not in result
    assert "No YAMP-managed sections" in result["error"]


def test_sync_collection_artwork_collection_lookup_failure(monkeypatch):
    """Non-NotFound Plex error on section.collection() → structured error, not an exception."""
    from plexapi.exceptions import PlexApiException

    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_plex = MagicMock()
    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.collection.side_effect = PlexApiException("BadRequest")
    mock_plex.library.sections.return_value = [mock_section]
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        result = yamp_app._sync_collection_artwork(col)
    assert result["ok"] is False
    assert "not_found_in_plex" not in result
    assert "Collection lookup failed" in result["error"]


def test_sync_collection_artwork_xml_parse_error(monkeypatch):
    """xml.etree.ElementTree.ParseError on PlexServer() → structured error, not an exception."""
    import xml.etree.ElementTree as ET

    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    with patch("plexapi.server.PlexServer", side_effect=ET.ParseError("malformed XML")):
        result = yamp_app._sync_collection_artwork(col)
    assert result["ok"] is False
    assert "Plex connection failed" in result["error"]


def test_sync_collection_artwork_upload_poster_failure(monkeypatch):
    """uploadPoster raising PlexApiException → ok=False without not_found_in_plex (no spurious retry)."""
    from plexapi.exceptions import PlexApiException

    col = yamp_app.CollectionModel(name="Test", rules=[], image="http://example.com/img.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_plex_col = MagicMock()
    mock_plex_col.uploadPoster.side_effect = PlexApiException("upload failed")
    mock_section = MagicMock()
    mock_section.collection.return_value = mock_plex_col

    mock_server = MagicMock()
    mock_server.library.sections.return_value = [mock_section]
    mock_section.agent = "tv.plex.agents.custom.yamp"

    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is False
    assert "not_found_in_plex" not in result
    assert isinstance(result["error"], dict)
    assert "uploadPoster failed" in result["error"]["image"]


async def test_artwork_bg_retry_success_is_logged(monkeypatch, caplog):
    """Retry succeeds → 'succeeded after retry' is logged at INFO level."""
    import logging

    col = MagicMock()
    col.name = "Test"
    results = [
        {"ok": False, "not_found_in_plex": True, "error": "not found"},
        {"ok": True, "created": True},
    ]
    call_count = [0]

    def _sync(_col):
        r = results[call_count[0]]
        call_count[0] += 1
        return r

    monkeypatch.setattr(yamp_app, "_sync_collection_artwork", _sync)
    with patch("app.asyncio.sleep", new_callable=AsyncMock), caplog.at_level(logging.INFO):
        await yamp_app._sync_collection_artwork_bg(col)

    assert call_count[0] == 2
    assert any("succeeded after retry" in r.message.lower() for r in caplog.records)


def test_build_index_read_errors_are_aggregated(tmp_path, caplog):
    """Unreadable .info.json files without bracket IDs increment the error count and emit an ERROR."""
    import logging

    from app import build_index

    # File with no bracket ID and invalid JSON — forces the fallback read path to fail
    bad_file = tmp_path / "no_bracket_id.info.json"
    bad_file.write_text("not valid json {{{", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        index, _ = build_index(str(tmp_path))

    assert len(index) == 0
    error_msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("failed to read" in m.lower() for m in error_msgs)
    assert any("1" in m for m in error_msgs), "error count should be 1"


# ── POST /api/index/rebuild ───────────────────────────────────────────────────


async def test_api_rebuild_index(patched_app):
    """Rebuild index returns indexed count."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/index/rebuild")
    assert resp.status_code == 200
    data = resp.json()
    assert "indexed" in data
    assert isinstance(data["indexed"], int)


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
    assert results[0]["ratingKey"] == info["id"]
    assert results[0]["key"] == f"/library/metadata/{info['id']}"
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


async def test_match_parent_dir_fallback(patched_app):
    """ID in parent directory name (MeTube per-video folder) → match found."""
    _, info, _ = patched_app
    # Plex sends the relative path; the containing folder has the video ID
    filename = f"Channel/Video Title [{info['id']}]/Video Title.mp4"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": filename})
    assert resp.status_code == 200
    results = resp.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["ratingKey"] == info["id"]


async def test_match_stem_index_fallback(patched_app, monkeypatch):
    """Bare filename with no ID anywhere → stem index resolves to correct video."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "_stem_index", {"My Bare Title": info["id"]})
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": "My Bare Title.mp4"})
    assert resp.status_code == 200
    results = resp.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["ratingKey"] == info["id"]


async def test_match_missing_title(patched_app):
    """info_json missing title → 200 with empty Metadata list."""
    _, info, tmp_path = patched_app
    info_no_title = {k: v for k, v in info.items() if k != "title"}
    (tmp_path / f"{info['id']}.info.json").write_text(json.dumps(info_no_title), encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": f"Video [{info['id']}].mp4"})
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Metadata"] == []


async def test_match_missing_upload_date(patched_app):
    """info_json missing upload_date → 200 with empty Metadata list."""
    _, info, tmp_path = patched_app
    info_no_date = {k: v for k, v in info.items() if k != "upload_date"}
    (tmp_path / f"{info['id']}.info.json").write_text(json.dumps(info_no_date), encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": f"Video [{info['id']}].mp4"})
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Metadata"] == []


async def test_match_unparseable_upload_date(patched_app):
    """info_json with bad upload_date → 200 with empty Metadata list."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.info.json").write_text(
        json.dumps({**info, "upload_date": "not-a-date"}), encoding="utf-8"
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/movies/library/metadata/matches", json={"filename": f"Video [{info['id']}].mp4"})
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Metadata"] == []


async def test_get_metadata_happy_path(patched_app):
    """Valid video ID returns full metadata with correct title and year."""
    _, info, _ = patched_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}")
    assert resp.status_code == 200
    meta = resp.json()["MediaContainer"]["Metadata"][0]
    assert meta["title"] == info["title"]
    assert meta["year"] == 2023  # upload_date "20231015"
    assert meta["ratingKey"] == info["id"]


async def test_get_metadata_invalid_id(patched_app):
    """rating_key that isn't a valid video ID format → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/imdb-tt1234567")
    assert resp.status_code == 404


async def test_get_metadata_unknown_id(patched_app):
    """Valid ID format but not in index → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/aaaaaaaaaaa")
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

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}")
    assert resp.status_code == 200
    meta = resp.json()["MediaContainer"]["Metadata"][0]
    assert meta["title"] == info["title"]
    assert meta.get("Collection", []) == []


async def test_get_metadata_resolve_collections_value_error(patched_app, monkeypatch):
    """If resolve_collections raises ValueError, endpoint still returns 200 with empty collections."""
    _, info, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )

    def _fail(*_args):
        raise ValueError("corrupt data")

    monkeypatch.setattr(yamp_app, "resolve_collections", _fail)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}")
    assert resp.status_code == 200
    meta = resp.json()["MediaContainer"]["Metadata"][0]
    assert meta.get("Collection", []) == []


async def test_get_metadata_build_response_failure(patched_app, monkeypatch):
    """If build_metadata_response raises ValueError → HTTP 500."""
    _, info, _ = patched_app

    def _fail(*_args, **_kwargs):
        raise ValueError("missing required field")

    monkeypatch.setattr(yamp_app, "build_metadata_response", _fail)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}")
    assert resp.status_code == 500


async def test_get_metadata_youtube_prefix_returns_404(patched_app):
    """Plex uses bare video ID — the youtube-{id} prefixed format should 404."""
    _, info, _ = patched_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/youtube-{info['id']}")
    assert resp.status_code == 404


# ── GET /movies (provider discovery) ─────────────────────────────────────────


async def test_get_provider_feature_keys():
    """Feature keys must be relative paths (no /movies prefix) to avoid Plex doubling them."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies")
    assert resp.status_code == 200
    features = {f["type"]: f["key"] for f in resp.json()["MediaProvider"]["Feature"]}
    assert features["match"] == "/library/metadata/matches"
    assert features["metadata"] == "/library/metadata"


# ── /movies/library/metadata/{rating_key}/images ─────────────────────────────


async def test_get_images_invalid_id(patched_app):
    """Non-video-ID format → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/not-an-id/images")
    assert resp.status_code == 404


async def test_get_images_unknown_id(patched_app):
    """Valid ID format but not in index → 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/movies/library/metadata/aaaaaaaaaaa/images")
    assert resp.status_code == 404


async def test_get_images_local_thumb_with_yamp_url(patched_app, monkeypatch):
    """Local thumbnail + YAMP_URL set → coverPoster uses YAMP proxy URL."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "YAMP_URL", "http://yamp.local:8765")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["type"] == "coverPoster"
    assert images[0]["url"] == f"http://yamp.local:8765/api/thumbnail/{info['id']}"


async def test_get_images_local_thumb_without_yamp_url(patched_app, monkeypatch):
    """No YAMP_URL → URL derived from request.base_url (always proxy through YAMP)."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "YAMP_URL", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["type"] == "coverPoster"
    assert images[0]["url"] == f"http://test/api/thumbnail/{info['id']}"


async def test_get_images_no_local_thumb_remote_fallback(patched_app, monkeypatch):
    """No local thumbnail, no YAMP_URL → still returns YAMP proxy URL from request.base_url."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    monkeypatch.setattr(yamp_app, "YAMP_URL", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["url"] == f"http://test/api/thumbnail/{info['id']}"


async def test_get_images_yamp_url_no_local_file(patched_app, monkeypatch):
    """YAMP_URL set but no local thumbnail → still returns YAMP proxy URL (not YouTube URL)."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    monkeypatch.setattr(yamp_app, "YAMP_URL", "http://yamp.local:8765")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["url"] == f"http://yamp.local:8765/api/thumbnail/{info['id']}"


async def test_get_images_no_thumbnail_at_all(patched_app):
    """No local file and no thumbnail field → still returns YAMP proxy URL; proxy handles 404."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    info_no_thumb = {k: v for k, v in info.items() if k != "thumbnail"}
    (tmp_path / f"{info['id']}.info.json").write_text(json.dumps(info_no_thumb), encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["url"] == f"http://test/api/thumbnail/{info['id']}"


# ── POST /api/thumbnails/fix ──────────────────────────────────────────────────


async def test_api_fix_thumbnails_no_plex_config(patched_app, monkeypatch):
    """PLEX_URL/PLEX_TOKEN not set → HTTP 400."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 400
    assert "PLEX_URL and PLEX_TOKEN" in resp.json()["detail"]


async def test_api_fix_thumbnails_plex_connection_failure(patched_app, monkeypatch):
    """PlexServer() raises PlexApiException → HTTP 502 with error detail."""
    from plexapi.exceptions import PlexApiException

    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    with patch("plexapi.server.PlexServer", side_effect=PlexApiException("refused")):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 502
    assert "Plex connection failed" in resp.json()["detail"]


async def test_api_fix_thumbnails_xml_parse_error(patched_app, monkeypatch):
    """PlexServer() raises ET.ParseError (malformed XML) → HTTP 502, not an unhandled exception."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    with patch("plexapi.server.PlexServer", side_effect=ET.ParseError("malformed XML")):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 502
    assert "Plex connection failed" in resp.json()["detail"]


async def test_api_fix_thumbnails_section_listing_failure(patched_app, monkeypatch):
    """plex.library.sections() raises → HTTP 502 with error detail."""
    from plexapi.exceptions import PlexApiException

    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_plex = MagicMock()
    mock_plex.library.sections.side_effect = PlexApiException("network error")
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 502
    assert "Could not list sections" in resp.json()["detail"]


async def test_api_fix_thumbnails_happy_path(patched_app, monkeypatch):
    """Items present with local thumbnail → 200 with fixed >= 1, failed = 0."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_item = MagicMock()
    mock_item.guid = f"{yamp_app.IDENTIFIER}://movie/{info['id']}"
    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.all.return_value = [mock_item]
    mock_plex = MagicMock()
    mock_plex.library.sections.return_value = [mock_section]
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] >= 1
    assert data["failed"] == 0
    assert "skipped" in data


async def test_api_fix_thumbnails_skips_items_without_content(patched_app, monkeypatch):
    """Items with no local thumbnail and no YouTube URL in cache → counted as skipped."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    monkeypatch.setattr(yamp_app, "_video_meta_cache", {})
    mock_item = MagicMock()
    mock_item.guid = f"{yamp_app.IDENTIFIER}://movie/{info['id']}"
    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.all.return_value = [mock_item]
    mock_plex = MagicMock()
    mock_plex.library.sections.return_value = [mock_section]
    with (
        patch("app._has_local_thumbnail", return_value=False),
        patch("plexapi.server.PlexServer", return_value=mock_plex),
    ):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] == 0
    assert data["skipped"] >= 1


async def test_api_fix_thumbnails_upload_failure(patched_app, monkeypatch):
    """uploadPoster raises PlexApiException → item counted in failed, not fixed."""
    from plexapi.exceptions import PlexApiException

    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_item = MagicMock()
    mock_item.guid = f"{yamp_app.IDENTIFIER}://movie/{info['id']}"
    mock_item.uploadPoster.side_effect = PlexApiException("upload failed")
    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.all.return_value = [mock_item]
    mock_plex = MagicMock()
    mock_plex.library.sections.return_value = [mock_section]
    with patch("plexapi.server.PlexServer", return_value=mock_plex):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/thumbnails/fix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["failed"] >= 1
    assert data["fixed"] == 0


# ── _do_rescan error paths ────────────────────────────────────────────────────


async def test_api_rescan_per_section_http_status_error(monkeypatch):
    """Per-section HTTPStatusError → failed_sections entry contains HTTP status code."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "1", "agent": yamp_app.IDENTIFIER, "title": "YouTube Movies"}]
    mock_response = MagicMock()
    mock_response.status_code = 403
    error = httpx.HTTPStatusError("403 Forbidden", request=MagicMock(), response=mock_response)
    mock_client = _make_rescan_mock(sections, refresh_side_effect=error)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["triggered_sections"] == []
    failed = data["failed_sections"]
    assert len(failed) == 1
    assert failed[0]["id"] == "1"
    assert "HTTP" in failed[0]["error"] and "403" in failed[0]["error"]


async def test_api_rescan_per_section_request_error(monkeypatch):
    """Per-section RequestError → section appears in failed_sections with error string."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    sections = [{"key": "2", "agent": yamp_app.IDENTIFIER, "title": "YT Lib"}]
    error = httpx.RequestError("connection reset")
    mock_client = _make_rescan_mock(sections, refresh_side_effect=error)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/api/rescan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["triggered_sections"] == []
    failed = data["failed_sections"]
    assert len(failed) == 1
    assert failed[0]["id"] == "2"
    assert "connection reset" in failed[0]["error"]


# ── _sync_collection_artwork error paths ──────────────────────────────────────


def test_sync_collection_artwork_create_collection_fails(monkeypatch):
    """collection not found in Plex, items found, but createCollection raises → ok=False."""
    from plexapi.exceptions import NotFound, PlexApiException

    from app import CollectionModel, _sync_collection_artwork

    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    col = CollectionModel(name="Test Col", image="https://example.com/img.jpg", rules=[])
    mock_section = MagicMock()
    mock_section.agent = yamp_app.IDENTIFIER
    mock_section.collection.side_effect = NotFound("not found")
    mock_plex = MagicMock()
    mock_plex.library.sections.return_value = [mock_section]
    mock_plex.createCollection.side_effect = PlexApiException("permission denied")
    with (
        patch("app._find_matching_plex_items", return_value=[MagicMock()]),
        patch("plexapi.server.PlexServer", return_value=mock_plex),
    ):
        result = _sync_collection_artwork(col)
    assert result["ok"] is False
    assert "Could not create collection" in result["error"]


# ── build_index UnicodeDecodeError ────────────────────────────────────────────


def test_build_index_unicode_decode_error(tmp_path):
    """info.json with non-UTF-8 bytes and no bracket ID is counted as a read error, not raised."""
    from app import build_index

    valid_id = "validID1234"
    (tmp_path / f"Video [{valid_id}].info.json").write_text(
        json.dumps({"id": valid_id, "title": "Valid"}), encoding="utf-8"
    )
    # No bracket ID in filename, non-UTF-8 bytes → triggers fallback read path → UnicodeDecodeError
    (tmp_path / "BadVideo.info.json").write_bytes(b"\xff\xfe invalid utf-8")

    index, stem_index = build_index(str(tmp_path))

    assert valid_id in index
    assert len(index) == 1  # bad file was silently skipped, not raised


# ── GET /api/channel-art ──────────────────────────────────────────────────────


async def test_api_channel_art_cache_hit(patched_app, monkeypatch):
    """Cache hit: options returned immediately, pending: false."""
    url = "https://www.youtube.com/c/TestChannel"
    art = {"channel": "Test Channel", "avatar_url": "https://img/av.jpg", "banner_url": ""}
    monkeypatch.setitem(yamp_app._channel_art_cache, url, art)
    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _name: [url])

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=TestCollection")

    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] is False
    assert len(data["options"]) == 1
    assert data["options"][0]["uploader_url"] == url
    assert data["options"][0]["channel"] == "Test Channel"


async def test_api_channel_art_cache_miss(patched_app, monkeypatch):
    """Cache miss: returns empty options with pending: true."""
    url = "https://www.youtube.com/c/MissingChannel"
    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _name: [url])
    # Ensure cache is empty for this URL
    yamp_app._channel_art_cache.pop(url, None)
    # Prevent the background prefetch from actually running yt-dlp
    mock_prefetch = AsyncMock()
    monkeypatch.setattr(yamp_app, "_prefetch_channel_art_bg", mock_prefetch)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=TestCollection")

    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] is True
    assert data["options"] == []


async def test_api_channel_art_unknown_collection(patched_app, monkeypatch):
    """Collection not found or has no YouTube URLs: returns empty, not pending."""
    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _name: [])

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=NoSuchCollection")

    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] is False
    assert data["options"] == []


# ── _sync_collection_artwork multi-field ──────────────────────────────────────


def _make_artwork_sync_mocks(agent=None):
    """Return (mock_server, mock_section, mock_plex_col) configured for artwork sync tests."""
    mock_plex_col = MagicMock()
    mock_section = MagicMock()
    mock_section.agent = agent or yamp_app.IDENTIFIER
    mock_section.collection.return_value = mock_plex_col
    mock_server = MagicMock()
    mock_server.library.sections.return_value = [mock_section]
    return mock_server, mock_section, mock_plex_col


def test_sync_collection_artwork_art_only(monkeypatch):
    """Collection with only 'art' set → uploadArt called, uploadPoster not called."""
    col = yamp_app.CollectionModel(name="Test", rules=[], art="https://example.com/bg.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_server, _, mock_plex_col = _make_artwork_sync_mocks()
    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadArt.assert_called_once_with(url="https://example.com/bg.jpg")
    mock_plex_col.uploadPoster.assert_not_called()


def test_sync_collection_artwork_logo_only(monkeypatch):
    """Collection with only 'logo' set → uploadLogo called, other methods not called."""
    col = yamp_app.CollectionModel(name="Test", rules=[], logo="https://example.com/logo.png")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_server, _, mock_plex_col = _make_artwork_sync_mocks()
    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadLogo.assert_called_once_with(url="https://example.com/logo.png")
    mock_plex_col.uploadPoster.assert_not_called()
    mock_plex_col.uploadArt.assert_not_called()
    mock_plex_col.uploadSquareArt.assert_not_called()


def test_sync_collection_artwork_square_art_only(monkeypatch):
    """Collection with only 'square_art' set → uploadSquareArt called, other methods not called."""
    col = yamp_app.CollectionModel(name="Test", rules=[], square_art="https://example.com/square.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_server, _, mock_plex_col = _make_artwork_sync_mocks()
    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadSquareArt.assert_called_once_with(url="https://example.com/square.jpg")
    mock_plex_col.uploadPoster.assert_not_called()
    mock_plex_col.uploadArt.assert_not_called()
    mock_plex_col.uploadLogo.assert_not_called()


def test_sync_collection_artwork_image_and_art(monkeypatch):
    """Collection with both 'image' and 'art' → both upload methods called, ok: True."""
    col = yamp_app.CollectionModel(
        name="Test",
        rules=[],
        image="https://example.com/poster.jpg",
        art="https://example.com/bg.jpg",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_server, _, mock_plex_col = _make_artwork_sync_mocks()
    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadPoster.assert_called_once_with(url="https://example.com/poster.jpg")
    mock_plex_col.uploadArt.assert_called_once_with(url="https://example.com/bg.jpg")


def test_sync_collection_artwork_partial_failure(monkeypatch):
    """First upload (image) succeeds, second (art) fails → ok: False with per-field error dict.

    Returns immediately once the collection is found — does not try subsequent sections.
    """
    from plexapi.exceptions import PlexApiException

    col = yamp_app.CollectionModel(
        name="Test",
        rules=[],
        image="https://example.com/poster.jpg",
        art="https://example.com/bg.jpg",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_server, _, mock_plex_col = _make_artwork_sync_mocks()
    mock_plex_col.uploadArt.side_effect = PlexApiException("art upload rejected")

    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is False
    # poster upload still attempted and succeeded (no exception raised)
    mock_plex_col.uploadPoster.assert_called_once()
    # error is a dict keyed by field name
    assert isinstance(result["error"], dict)
    assert "art" in result["error"]
    assert "image" not in result["error"]


# ── PUT /api/collections — non-image field change detection ───────────────────


async def test_put_collections_art_field_triggers_artwork_sync(patched_app, monkeypatch):
    """Changing only the 'art' field (no rule changes) queues artwork sync but not rescan."""
    _, _, tmp_path = patched_app
    existing = [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}], "art": None}]
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": existing, "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    mock_artwork = AsyncMock()
    monkeypatch.setattr(yamp_app, "_sync_collection_artwork_bg", mock_artwork)

    updated = [
        {
            "name": "Jazz",
            "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}],
            "art": "https://example.com/jazz-bg.jpg",
        }
    ]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": updated})

    assert resp.status_code == 200
    mock_rescan.assert_not_awaited()
    mock_artwork.assert_awaited_once()


# ── _fetch_channel_art ────────────────────────────────────────────────────────


def test_fetch_channel_art_non_youtube_url():
    """Non-YouTube URL returns None immediately (before any yt-dlp check)."""
    from app import _fetch_channel_art

    result = _fetch_channel_art("https://vimeo.com/channels/foo")
    assert result is None


def test_fetch_channel_art_yt_dlp_unavailable(monkeypatch):
    """When _YT_DLP_AVAILABLE is False, returns None without calling yt-dlp."""
    from app import _fetch_channel_art

    monkeypatch.setattr(yamp_app, "_YT_DLP_AVAILABLE", False)
    result = _fetch_channel_art("https://www.youtube.com/@TestChannel")
    assert result is None


def test_fetch_channel_art_exception_returns_none(monkeypatch):
    """Unexpected exception from yt-dlp returns None (logged, not raised)."""
    from app import _fetch_channel_art

    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = RuntimeError("yt-dlp exploded")

    mock_module = MagicMock()
    mock_module.YoutubeDL.return_value = mock_ydl

    monkeypatch.setattr(yamp_app, "_yt_dlp", mock_module)
    monkeypatch.setattr(yamp_app, "_YT_DLP_AVAILABLE", True)
    result = _fetch_channel_art("https://www.youtube.com/@TestChannel")

    assert result is None


# ── _prefetch_channel_art_bg — error handling ────────────────────────────────


async def test_prefetch_channel_art_bg_clears_in_progress_on_exception(monkeypatch):
    """_prefetch_in_progress is cleared even when _fetch_channel_art raises."""
    from unittest.mock import patch

    monkeypatch.setattr(
        yamp_app,
        "_get_channel_urls_for_collection",
        lambda _: ["https://www.youtube.com/@TestChannel"],
    )
    with patch("app._fetch_channel_art", side_effect=RuntimeError("boom")):
        await yamp_app._prefetch_channel_art_bg(["TestCollection"])

    assert "TestCollection" not in yamp_app._prefetch_in_progress


async def test_prefetch_channel_art_bg_caches_error_sentinel(monkeypatch):
    """When _fetch_channel_art returns None, the URL is cached as an error sentinel."""
    from unittest.mock import patch

    url = "https://www.youtube.com/@ErrorChannel"
    # Use a fresh cache so the function actually attempts the fetch (not a cache hit)
    # and monkeypatch restores the original on teardown.
    fresh_cache: dict = {}
    monkeypatch.setattr(yamp_app, "_channel_art_cache", fresh_cache)

    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _: [url])

    with patch("app._fetch_channel_art", return_value=None):
        await yamp_app._prefetch_channel_art_bg(["TestCollection"])

    assert url in fresh_cache
    assert fresh_cache[url] is yamp_app._FETCH_ERROR_SENTINEL


# ── GET /api/channel-art — fetch_error flag ───────────────────────────────────


async def test_api_channel_art_fetch_error_flag(patched_app, monkeypatch):
    """If cache holds a _fetch_error sentinel, response includes fetch_error: true."""
    url = "https://www.youtube.com/@BrokenChannel"
    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _: [url])
    monkeypatch.setitem(yamp_app._channel_art_cache, url, yamp_app._FETCH_ERROR_SENTINEL)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=TestCollection")

    assert resp.status_code == 200
    data = resp.json()
    assert data["fetch_error"] is True
    assert data["options"] == []
    assert data["pending"] is False


# ── _get_channel_urls_for_collection — internal branches ─────────────────────


def test_get_channel_urls_deduplication(tmp_path, monkeypatch):
    """Same uploader_url across multiple matched videos appears only once."""
    from collection_map import save_map

    url = "https://www.youtube.com/@GoGoPenguin"
    ids = ["vid111aaaaa", "vid222bbbbb"]
    for vid in ids:
        info = {
            "id": vid,
            "title": f"Video {vid}",
            "tags": ["jazz"],
            "uploader_url": url,
            "channel": "GoGo Penguin",
        }
        (tmp_path / f"{vid}.info.json").write_text(json.dumps(info), encoding="utf-8")

    col_map = {
        "collections": [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}]}],
        "matched_ids": ids,
        "unmatched_ids": [],
        "unmatched_tags": {},
    }
    save_map(str(tmp_path / "_collection_map.json"), col_map)

    monkeypatch.setattr(yamp_app, "_video_index", {vid: str(tmp_path / f"{vid}.info.json") for vid in ids})
    # meta cache provides the match fields so the function skips disk reads for filtering
    meta = {"tags": ["jazz"], "channel": "GoGo Penguin"}
    monkeypatch.setattr(yamp_app, "_video_meta_cache", {vid: meta for vid in ids})
    monkeypatch.setattr(yamp_app, "DATA_PATH", str(tmp_path))

    result = yamp_app._get_channel_urls_for_collection("Jazz")
    assert result == [url]  # deduplicated to one entry


def test_get_channel_urls_unicode_decode_error_skipped(tmp_path, monkeypatch):
    """A non-UTF-8 info.json is skipped (logged) rather than raising."""
    from collection_map import save_map

    valid_id = "validID12345"
    bad_id = "badIDxxxxxxx"
    valid_url = "https://www.youtube.com/@Valid"

    (tmp_path / f"{valid_id}.info.json").write_text(
        json.dumps({"id": valid_id, "title": "Valid", "tags": ["jazz"], "uploader_url": valid_url, "channel": "Valid"}),
        encoding="utf-8",
    )
    (tmp_path / f"{bad_id}.info.json").write_bytes(b"\xff\xfe bad utf-8")

    col_map = {
        "collections": [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}]}],
        "matched_ids": [valid_id, bad_id],
        "unmatched_ids": [],
        "unmatched_tags": {},
    }
    save_map(str(tmp_path / "_collection_map.json"), col_map)

    monkeypatch.setattr(
        yamp_app,
        "_video_index",
        {
            valid_id: str(tmp_path / f"{valid_id}.info.json"),
            bad_id: str(tmp_path / f"{bad_id}.info.json"),
        },
    )
    # Both IDs match via meta cache; the bad_id then fails on the disk read for uploader_url.
    monkeypatch.setattr(
        yamp_app,
        "_video_meta_cache",
        {
            valid_id: {"tags": ["jazz"], "channel": "Valid"},
            bad_id: {"tags": ["jazz"], "channel": "Valid"},
        },
    )
    monkeypatch.setattr(yamp_app, "DATA_PATH", str(tmp_path))

    result = yamp_app._get_channel_urls_for_collection("Jazz")
    assert result == [valid_url]  # bad_id skipped, valid_id included


# ── _sync_collection_artwork — multi-section ─────────────────────────────────


def test_sync_collection_artwork_second_section_tried_after_first_miss(monkeypatch):
    """When collection is absent from section 1 but present in section 2, sync succeeds."""
    from unittest.mock import patch

    from plexapi.exceptions import NotFound

    col = yamp_app.CollectionModel(name="Jazz", rules=[], image="https://example.com/poster.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_plex_col = MagicMock()
    section1 = MagicMock()
    section1.agent = yamp_app.IDENTIFIER
    section1.collection.side_effect = NotFound("not found")
    section2 = MagicMock()
    section2.agent = yamp_app.IDENTIFIER
    section2.collection.return_value = mock_plex_col

    mock_server = MagicMock()
    mock_server.library.sections.return_value = [section1, section2]

    with (
        patch("app._find_matching_plex_items", return_value=[]),
        patch("plexapi.server.PlexServer", return_value=mock_server),
    ):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadPoster.assert_called_once_with(url="https://example.com/poster.jpg")


def test_sync_collection_artwork_plex_err_on_section1_section2_succeeds(monkeypatch):
    """Section 1 raises a _PLEX_ERRS connection error; section 2 has the collection — ok: True."""
    from unittest.mock import patch

    import requests

    col = yamp_app.CollectionModel(name="Jazz", rules=[], image="https://example.com/poster.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    mock_plex_col = MagicMock()
    section1 = MagicMock()
    section1.agent = yamp_app.IDENTIFIER
    section1.collection.side_effect = requests.exceptions.ConnectionError("refused")
    section2 = MagicMock()
    section2.agent = yamp_app.IDENTIFIER
    section2.collection.return_value = mock_plex_col

    mock_server = MagicMock()
    mock_server.library.sections.return_value = [section1, section2]

    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result["ok"] is True
    mock_plex_col.uploadPoster.assert_called_once_with(url="https://example.com/poster.jpg")


# ── PUT /api/collections — logo / square_art field change detection ────────────


async def test_put_collections_logo_field_triggers_artwork_sync(patched_app, monkeypatch):
    """Changing only the 'logo' field queues artwork sync but not rescan."""
    _, _, tmp_path = patched_app
    existing = [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}], "logo": None}]
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": existing, "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    mock_artwork = AsyncMock()
    monkeypatch.setattr(yamp_app, "_sync_collection_artwork_bg", mock_artwork)

    updated = [
        {
            "name": "Jazz",
            "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}],
            "logo": "https://example.com/jazz-logo.png",
        }
    ]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": updated})

    assert resp.status_code == 200
    mock_rescan.assert_not_awaited()
    mock_artwork.assert_awaited_once()


async def test_put_collections_square_art_field_triggers_artwork_sync(patched_app, monkeypatch):
    """Changing only the 'square_art' field queues artwork sync but not rescan."""
    _, _, tmp_path = patched_app
    rule = {"field": "tags", "match": "exact", "values": ["jazz"]}
    existing = [{"name": "Jazz", "rules": [rule], "square_art": None}]
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": existing, "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    mock_artwork = AsyncMock()
    monkeypatch.setattr(yamp_app, "_sync_collection_artwork_bg", mock_artwork)

    updated = [
        {
            "name": "Jazz",
            "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}],
            "square_art": "https://example.com/jazz-square.jpg",
        }
    ]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": updated})

    assert resp.status_code == 200
    mock_rescan.assert_not_awaited()
    mock_artwork.assert_awaited_once()


async def test_put_collections_rule_change_with_no_image_skips_artwork_sync(patched_app, monkeypatch):
    """Rule change with all image fields None → rescan triggered, artwork sync NOT triggered."""
    _, _, tmp_path = patched_app
    map_file = tmp_path / "_collection_map.json"
    map_file.write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")
    mock_rescan = AsyncMock()
    monkeypatch.setattr(yamp_app, "_do_rescan_bg", mock_rescan)
    mock_artwork = AsyncMock()
    monkeypatch.setattr(yamp_app, "_sync_collection_artwork_bg", mock_artwork)

    # New collection with rules but no image fields
    new_col = [{"name": "Jazz", "rules": [{"field": "tags", "match": "exact", "values": ["jazz"]}]}]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/collections", json={"collections": new_col})

    assert resp.status_code == 200
    mock_rescan.assert_awaited_once()
    mock_artwork.assert_not_awaited()


# ── _prefetch_channel_art_bg — dedup guard ────────────────────────────────────


async def test_prefetch_channel_art_bg_dedup_guard(monkeypatch):
    """A collection name already in _prefetch_in_progress is skipped without calling _get_channel_urls."""
    called = []
    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda name: called.append(name) or [])

    yamp_app._prefetch_in_progress.add("AlreadyRunning")
    try:
        await yamp_app._prefetch_channel_art_bg(["AlreadyRunning"])
    finally:
        yamp_app._prefetch_in_progress.discard("AlreadyRunning")

    assert "AlreadyRunning" not in called


# ── GET /api/channel-art — pending + fetch_error co-occurrence ────────────────


async def test_api_channel_art_pending_and_fetch_error(patched_app, monkeypatch):
    """pending: true and fetch_error: true can both be set when one URL errored and another is missing."""
    errored_url = "https://www.youtube.com/@BrokenChannel"
    missing_url = "https://www.youtube.com/@PendingChannel"

    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", lambda _: [errored_url, missing_url])
    monkeypatch.setitem(yamp_app._channel_art_cache, errored_url, yamp_app._FETCH_ERROR_SENTINEL)
    yamp_app._channel_art_cache.pop(missing_url, None)

    mock_prefetch = AsyncMock()
    monkeypatch.setattr(yamp_app, "_prefetch_channel_art_bg", mock_prefetch)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=TestCollection")

    assert resp.status_code == 200
    data = resp.json()
    assert data["fetch_error"] is True
    assert data["pending"] is True
    assert data["options"] == []


# ── _prefetch_channel_art_bg — URL resolution exception continues ──────────────


async def test_prefetch_channel_art_bg_url_error_continues(monkeypatch):
    """Exception in _get_channel_urls_for_collection for col A → col B still processed."""
    url_b = "https://www.youtube.com/@ChannelB"
    call_count = [0]

    def _urls_or_raise(name):
        call_count[0] += 1
        if name == "CollectionA":
            raise RuntimeError("lookup failed")
        return [url_b]

    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", _urls_or_raise)
    fresh_cache: dict = {}
    monkeypatch.setattr(yamp_app, "_channel_art_cache", fresh_cache)

    from unittest.mock import patch

    with patch("app._fetch_channel_art", return_value={"channel": "B", "avatar_url": "", "banner_url": ""}):
        await yamp_app._prefetch_channel_art_bg(["CollectionA", "CollectionB"])

    assert call_count[0] == 2, "both collections should have been attempted"
    assert fresh_cache.get(url_b) == {
        "channel": "B",
        "avatar_url": "",
        "banner_url": "",
    }


# ── _sync_collection_artwork — all sections exhausted ────────────────────────


def test_sync_collection_artwork_all_sections_fail_plex_errs(monkeypatch):
    """All YAMP sections fail with _PLEX_ERRS → last_section_error returned, not None."""
    import requests

    col = yamp_app.CollectionModel(name="Jazz", rules=[], image="https://example.com/poster.jpg")
    monkeypatch.setattr(yamp_app, "PLEX_URL", "http://plex.invalid")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "tok")

    section1 = MagicMock()
    section1.agent = yamp_app.IDENTIFIER
    section1.collection.side_effect = requests.exceptions.ConnectionError("refused")
    section2 = MagicMock()
    section2.agent = yamp_app.IDENTIFIER
    section2.collection.side_effect = requests.exceptions.ConnectionError("refused")

    mock_server = MagicMock()
    mock_server.library.sections.return_value = [section1, section2]

    with patch("plexapi.server.PlexServer", return_value=mock_server):
        result = yamp_app._sync_collection_artwork(col)

    assert result is not None
    assert result["ok"] is False
    assert isinstance(result.get("error"), str)


# ── _format_sync_error — partial success branch ───────────────────────────────


def test_format_sync_error_partial_success_branch():
    """When some fields succeeded, _format_sync_error returns a 'partial failure' message."""
    col = yamp_app.CollectionModel(
        name="Test",
        rules=[],
        image="https://example.com/poster.jpg",
        art="https://example.com/bg.jpg",
    )
    # image succeeded (not in error dict), art failed
    error = {"art": "uploadArt failed: some error"}
    result = yamp_app._format_sync_error(col, error)
    assert "partial failure" in result
    assert "image" in result  # succeeded field included
    assert "art" in result  # failed field included


# ── GET /api/channel-art — unexpected exception → 500 ────────────────────────


async def test_api_channel_art_get_urls_raises(patched_app, monkeypatch):
    """If _get_channel_urls_for_collection raises, the endpoint returns HTTP 500."""

    def _raise(_name):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(yamp_app, "_get_channel_urls_for_collection", _raise)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/channel-art?collection=TestCollection")

    assert resp.status_code == 500


# ── GET /api/collections — OSError path ──────────────────────────────────────


async def test_api_get_collections_oserror(patched_app, monkeypatch):
    """OSError reading collection map → 500 with 'check file permissions' in detail."""
    _, _, tmp_path = patched_app
    (tmp_path / "_collection_map.json").write_text(
        json.dumps({"collections": [], "matched_ids": [], "unmatched_ids": [], "unmatched_tags": {}}),
        encoding="utf-8",
    )

    def _raise_os_error(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(yamp_app, "load_map", _raise_os_error)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/collections")

    assert resp.status_code == 500
    assert "file permissions" in resp.json()["detail"]
