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
    assert "Could not read collection map" in resp.json()["detail"]


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
        raise Exception("boom")

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
    assert resp.json() == {"triggered_sections": ["1"], "failed_sections": []}


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
    assert data["failed_sections"] == [{"section_id": "1", "error": "timeout"}]


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


async def test_api_rescan_no_plex_config(monkeypatch):
    """Missing PLEX_URL/PLEX_TOKEN → 400."""
    monkeypatch.setattr(yamp_app, "PLEX_URL", "")
    monkeypatch.setattr(yamp_app, "PLEX_TOKEN", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/rescan")
    assert resp.status_code == 400


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
    """Local thumbnail but no YAMP_URL → falls back to remote thumbnail from info_json."""
    _, info, _ = patched_app
    monkeypatch.setattr(yamp_app, "YAMP_URL", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["type"] == "coverPoster"
    assert images[0]["url"] == info["thumbnail"]


async def test_get_images_no_local_thumb_remote_fallback(patched_app, monkeypatch):
    """No local thumbnail → falls back to remote thumbnail URL from info_json."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    monkeypatch.setattr(yamp_app, "YAMP_URL", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    images = resp.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["url"] == info["thumbnail"]


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
    """No local file and no thumbnail field → empty Image list."""
    _, info, tmp_path = patched_app
    (tmp_path / f"{info['id']}.jpg").unlink()
    info_no_thumb = {k: v for k, v in info.items() if k != "thumbnail"}
    (tmp_path / f"{info['id']}.info.json").write_text(json.dumps(info_no_thumb), encoding="utf-8")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/movies/library/metadata/{info['id']}/images")
    assert resp.status_code == 200
    assert resp.json()["MediaContainer"]["Image"] == []
