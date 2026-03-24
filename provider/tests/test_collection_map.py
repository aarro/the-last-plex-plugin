import json
import shutil
from pathlib import Path

import pytest

from collection_map import (
    diff_collections,
    find_collection_map,
    match_video,
    recompute_all_collections,
    resolve_collections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fresh_map(tmp_path: Path) -> tuple[str, str]:
    """Copy fixture files to tmp_path and return (info_json_path, map_path)."""
    yamp_dir = tmp_path / ".yamp"
    yamp_dir.mkdir(exist_ok=True)
    map_path = yamp_dir / "collection_map.json"
    shutil.copy(FIXTURES / "_collection_map.json", map_path)
    return str(FIXTURES / "sample.info.json"), str(map_path)


def _load_info() -> dict:
    with open(FIXTURES / "sample.info.json", encoding="utf-8") as f:
        return json.load(f)


def _load_map(map_path: str) -> dict:
    with open(map_path, encoding="utf-8") as f:
        return json.load(f)


# ── Tag matching ──────────────────────────────────────────────────────────────


def test_exact_tag_match(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    # sample.info.json has tag "gogo penguin" which matches GoGo Penguin collection
    result = resolve_collections(info, map_path)
    assert "GoGo Penguin" in result


def test_matched_id_tracked(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    resolve_collections(info, map_path)
    data = _load_map(map_path)
    assert info["id"] in data["matched_ids"]
    assert info["id"] not in data["unmatched_ids"]


def test_already_matched_returns_collections(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    # First call matches
    first = resolve_collections(info, map_path)
    # Second call should still return the same collections (not skip)
    result = resolve_collections(info, map_path)
    assert result == first
    # Still only one entry in matched_ids
    data = _load_map(map_path)
    assert data["matched_ids"].count(info["id"]) == 1


# ── Title matching ────────────────────────────────────────────────────────────


def test_in_title_match(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    # Remove tags so it falls through to title rule
    info["tags"] = []
    result = resolve_collections(info, map_path)
    # Title contains "GoGo Penguin" → matches via "in" rule
    assert "GoGo Penguin" in result


# ── Channel matching ──────────────────────────────────────────────────────────


def test_exact_channel_match(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    info["tags"] = []
    info["title"] = "Something Unrelated"
    # channel is "GoGo Penguin Music" — not in the exact match list, so no channel match either
    result = resolve_collections(info, map_path)
    assert "GoGo Penguin" not in result

    # Now set channel to an exact match value
    info2 = _load_info()
    info2["tags"] = []
    info2["title"] = "Something Unrelated"
    info2["channel"] = "gogo penguin"
    result2 = resolve_collections(info2, map_path)
    assert "GoGo Penguin" in result2


# ── No match ─────────────────────────────────────────────────────────────────


def test_no_match_adds_to_unmatched(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    info["id"] = "unmatched_video_001"
    info["tags"] = ["ambient", "electronic"]
    info["title"] = "Some Random Video"
    info["channel"] = "RandomChannel"
    result = resolve_collections(info, map_path)
    assert result == []
    data = _load_map(map_path)
    assert "unmatched_video_001" in data["unmatched_ids"]
    assert "unmatched_video_001" not in data["matched_ids"]


def test_unmatched_tags_tracked(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    info["id"] = "unmatched_video_002"
    info["tags"] = ["ambient", "electronic", "downtempo"]
    info["title"] = "No Match Title"
    info["channel"] = "SomeChannel"
    resolve_collections(info, map_path)
    data = _load_map(map_path)
    assert "ambient" in data["unmatched_tags"]
    assert "electronic" in data["unmatched_tags"]


def test_unmatched_tags_sorted_by_frequency(tmp_path):
    _, map_path = _fresh_map(tmp_path)

    for i, extra_tag in enumerate(["ambient", "ambient", "electronic"]):
        info = _load_info()
        info["id"] = f"unmatched_{i}"
        info["tags"] = [extra_tag, "downtempo"]
        info["title"] = "No Match"
        info["channel"] = "SomeChannel"
        resolve_collections(info, map_path)

    data = _load_map(map_path)
    tag_keys = list(data["unmatched_tags"].keys())
    # "ambient" appears most → should come first
    assert tag_keys.index("ambient") < tag_keys.index("electronic")


# ── Deduplication ─────────────────────────────────────────────────────────────


def test_duplicate_collections_deduplicated(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()
    # Tag matches GoGo Penguin; also set title and channel to also match
    info["channel"] = "gogo penguin"
    result = resolve_collections(info, map_path)
    assert result.count("GoGo Penguin") == 1


# ── match_type allowlist ──────────────────────────────────────────────────────


def test_invalid_match_type_still_writes_unmatched_state(tmp_path):
    """When all rules have invalid match types, the map file still tracks the video as unmatched."""
    info = _load_info()
    info["id"] = "wrong_match_type_001"
    info["tags"] = []
    info["title"] = "Something Unrelated"
    info["channel"] = "RandomChannel"

    map_data = {
        "collections": [
            {
                "name": "Bad Rule Collection",
                "rules": [{"field": "channel", "match": "EXACT", "values": ["RandomChannel"]}],
            }
        ],
        "matched_ids": [],
        "unmatched_ids": [],
        "unmatched_tags": {},
    }
    yamp_dir = tmp_path / ".yamp"
    yamp_dir.mkdir(exist_ok=True)
    map_path = yamp_dir / "collection_map.json"
    map_path.write_text(json.dumps(map_data), encoding="utf-8")

    result = resolve_collections(info, str(map_path))
    assert result == []

    saved = json.loads(map_path.read_text(encoding="utf-8"))
    assert info["id"] in saved["unmatched_ids"]
    assert info["id"] not in saved["matched_ids"]


# ── match_video ──────────────────────────────────────────────────────────────


def _collections():
    """Return the fixture collection list (without file I/O)."""
    with open(FIXTURES / "_collection_map.json", encoding="utf-8") as f:
        return json.load(f)["collections"]


def test_match_video_tag_match():
    info = _load_info()
    matches, remaining = match_video(info, _collections())
    assert "GoGo Penguin" in matches


def test_match_video_returns_remaining_tags():
    info = _load_info()
    info["tags"] = ["gogo penguin", "jazz", "live"]
    matches, remaining = match_video(info, _collections())
    assert "GoGo Penguin" in matches
    # "gogo penguin" was consumed; "jazz" and "live" should remain
    assert "gogo penguin" not in remaining
    assert "jazz" in remaining
    assert "live" in remaining


def test_match_video_no_match_returns_all_tags():
    info = _load_info()
    info["tags"] = ["ambient", "electronic"]
    info["title"] = "Some Random Video"
    info["channel"] = "RandomChannel"
    matches, remaining = match_video(info, _collections())
    assert matches == []
    assert "ambient" in remaining
    assert "electronic" in remaining


def test_match_video_in_title():
    info = _load_info()
    info["tags"] = []
    matches, _ = match_video(info, _collections())
    assert "GoGo Penguin" in matches


def test_match_video_pure_does_not_mutate_info():
    info = _load_info()
    original_tags = list(info["tags"])
    match_video(info, _collections())
    assert info["tags"] == original_tags


# ── recompute_all_collections ─────────────────────────────────────────────────


def test_recompute_all_collections_basic(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()

    # Write info.json into tmp_path so recompute can find it
    info_path = tmp_path / f"{info['id']}.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")

    video_index = {info["id"]: str(info_path)}
    stats = recompute_all_collections(video_index, map_path)

    assert stats["matched"] == 1
    assert stats["unmatched"] == 0
    data = _load_map(map_path)
    assert info["id"] in data["matched_ids"]
    assert info["id"] not in data["unmatched_ids"]


def test_recompute_clears_stale_state(tmp_path):
    _, map_path = _fresh_map(tmp_path)
    info = _load_info()

    # Pre-populate with stale state
    data = _load_map(map_path)
    data["matched_ids"] = ["stale_id_1", "stale_id_2"]
    data["unmatched_ids"] = ["stale_id_3"]
    data["unmatched_tags"] = {"stale_tag": 99}
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    info_path = tmp_path / f"{info['id']}.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")

    recompute_all_collections({info["id"]: str(info_path)}, map_path)
    data = _load_map(map_path)

    assert "stale_id_1" not in data["matched_ids"]
    assert "stale_id_3" not in data["unmatched_ids"]
    assert "stale_tag" not in data["unmatched_tags"]


def test_recompute_tracks_unmatched_tags(tmp_path):
    _, map_path = _fresh_map(tmp_path)

    unmatched_info = {
        "id": "unmatched_001",
        "title": "No Match",
        "channel": "SomeChannel",
        "tags": ["ambient", "electronic", "downtempo"],
        "upload_date": "20230101",
    }
    info_path = tmp_path / "unmatched_001.info.json"
    info_path.write_text(json.dumps(unmatched_info), encoding="utf-8")

    recompute_all_collections({"unmatched_001": str(info_path)}, map_path)
    data = _load_map(map_path)

    assert "unmatched_001" in data["unmatched_ids"]
    assert "ambient" in data["unmatched_tags"]
    assert "electronic" in data["unmatched_tags"]


def test_recompute_newly_matched_tag_removed_from_unmatched(tmp_path):
    """Adding a collection should move a previously-unmatched video to matched."""
    _, map_path = _fresh_map(tmp_path)

    info = _load_info()
    info_path = tmp_path / f"{info['id']}.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")

    # First pass: no matching collections → unmatched
    no_collections_map = _load_map(map_path)
    no_collections_map["collections"] = []
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(no_collections_map, f)

    recompute_all_collections({info["id"]: str(info_path)}, map_path)
    data = _load_map(map_path)
    assert info["id"] in data["unmatched_ids"]

    # Restore collections and recompute — video should now be matched
    data["collections"] = _collections()
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    recompute_all_collections({info["id"]: str(info_path)}, map_path)
    data = _load_map(map_path)
    assert info["id"] in data["matched_ids"]
    assert info["id"] not in data["unmatched_ids"]


# ── find_collection_map ───────────────────────────────────────────────────────


def test_find_collection_map_direct(tmp_path):
    yamp_dir = tmp_path / ".yamp"
    yamp_dir.mkdir()
    map_path = yamp_dir / "collection_map.json"
    map_path.write_text("{}", encoding="utf-8")
    found = find_collection_map(str(tmp_path), str(tmp_path))
    assert found == str(map_path)


def test_find_collection_map_walk_up(tmp_path):
    yamp_dir = tmp_path / ".yamp"
    yamp_dir.mkdir()
    map_path = yamp_dir / "collection_map.json"
    map_path.write_text("{}", encoding="utf-8")
    subdir = tmp_path / "channel" / "videos"
    subdir.mkdir(parents=True)
    found = find_collection_map(str(subdir), str(tmp_path))
    assert found == str(map_path)


def test_find_collection_map_not_found(tmp_path):
    found = find_collection_map(str(tmp_path), str(tmp_path))
    assert found is None


# ── match_video edge cases ────────────────────────────────────────────────────


def test_match_video_invalid_match_type_logged_and_skipped(caplog):
    """Rules with wrong-case match type must be skipped (and logged as warning)."""
    import logging

    info = _load_info()
    collections = [
        {
            "name": "Bad Rule Collection",
            "rules": [{"field": "channel", "match": "EXACT", "values": ["GoGo Penguin Music"]}],
        }
    ]
    with caplog.at_level(logging.WARNING, logger="collection_map"):
        result, _ = match_video(info, collections)
    assert result == []
    assert any("unknown match_type" in r.message for r in caplog.records)


def test_match_video_field_not_in_info_json():
    """Rules referencing a field absent from info_json must be skipped silently."""
    info = _load_info()
    collections = [
        {
            "name": "Missing Field Collection",
            "rules": [{"field": "nonexistent_field", "match": "exact", "values": ["anything"]}],
        }
    ]
    result, _ = match_video(info, collections)
    assert result == []


def test_match_video_empty_collections():
    """Passing an empty collections list returns no matches and all tags."""
    info = _load_info()
    info["tags"] = ["jazz", "live"]
    result, remaining = match_video(info, [])
    assert result == []
    assert "jazz" in remaining
    assert "live" in remaining


def test_match_video_dedup_non_tag_rules():
    """A collection matched by both title and channel rules appears exactly once."""
    info = _load_info()
    info["tags"] = []
    info["title"] = "gogo penguin live"
    info["channel"] = "gogo penguin"
    collections = [
        {
            "name": "Dedup Test",
            "rules": [
                {"field": "title", "match": "in", "values": ["gogo penguin"]},
                {"field": "channel", "match": "exact", "values": ["gogo penguin"]},
            ],
        }
    ]
    result, _ = match_video(info, collections)
    assert result.count("Dedup Test") == 1


def test_recompute_skips_corrupt_file(tmp_path):
    """A corrupt .info.json is skipped; valid entries are still processed."""
    _, map_path = _fresh_map(tmp_path)

    good_info = _load_info()
    good_path = tmp_path / f"{good_info['id']}.info.json"
    good_path.write_text(json.dumps(good_info), encoding="utf-8")

    bad_path = tmp_path / "corrupt_video_id.info.json"
    bad_path.write_text("not valid json {{{{", encoding="utf-8")

    video_index = {
        good_info["id"]: str(good_path),
        "corrupt_video_id": str(bad_path),
    }
    stats = recompute_all_collections(video_index, map_path)

    # Only the valid video is counted
    assert stats["matched"] + stats["unmatched"] == 1
    data = _load_map(map_path)
    assert "corrupt_video_id" not in data["matched_ids"]
    assert "corrupt_video_id" not in data["unmatched_ids"]


def test_recompute_empty_index(tmp_path):
    """Empty video index clears all state and returns zeros."""
    _, map_path = _fresh_map(tmp_path)

    # Pre-populate with stale data
    data = _load_map(map_path)
    data["matched_ids"] = ["old_id"]
    data["unmatched_ids"] = ["other_id"]
    data["unmatched_tags"] = {"jazz": 5}
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    stats = recompute_all_collections({}, map_path)
    assert stats == {"matched": 0, "unmatched": 0, "skipped": 0}

    result = _load_map(map_path)
    assert result["matched_ids"] == []
    assert result["unmatched_ids"] == []
    assert result["unmatched_tags"] == {}


# ── diff_collections ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "old, new, expected_changed, expected_has_changes",
    [
        # Both empty
        ([], [], set(), False),
        # Collection added
        ([], [{"name": "Jazz", "rules": [{"field": "tags", "values": ["jazz"], "match": "exact"}]}], {"Jazz"}, True),
        # Collection deleted
        ([{"name": "Jazz", "rules": []}], [], {"Jazz"}, True),
        # Rules modified
        (
            [{"name": "Jazz", "rules": [{"field": "tags", "values": ["jazz"], "match": "exact"}]}],
            [{"name": "Jazz", "rules": [{"field": "tags", "values": ["bebop"], "match": "exact"}]}],
            {"Jazz"},
            True,
        ),
        # Image URL only changed (rules identical) — no recompute needed
        (
            [{"name": "Jazz", "rules": [], "image": "https://old.example.com/a.jpg"}],
            [{"name": "Jazz", "rules": [], "image": "https://new.example.com/b.jpg"}],
            set(),
            False,
        ),
    ],
)
def test_diff_collections(old, new, expected_changed, expected_has_changes):
    rules_changed, has_changes = diff_collections(old, new)
    assert rules_changed == expected_changed
    assert has_changes == expected_has_changes


# ── recompute_all_collections with meta_cache ─────────────────────────────────


def _make_map_with_rules(tmp_path: Path) -> str:
    """Write a collection_map.json with one rule-based collection and return its path."""
    yamp_dir = tmp_path / ".yamp"
    yamp_dir.mkdir(exist_ok=True)
    map_path = yamp_dir / "collection_map.json"
    data = {
        "collections": [
            {
                "name": "TestCol",
                "rules": [{"field": "tags", "values": ["testtag"], "match": "exact"}],
            }
        ],
        "matched_ids": [],
        "unmatched_ids": [],
        "unmatched_tags": {},
    }
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(map_path)


def test_recompute_with_meta_cache_matches_video(tmp_path):
    """meta_cache path: video in cache with matching tag → matched."""
    map_path = _make_map_with_rules(tmp_path)
    # Provide fake index and cache — no real files on disk
    fake_index = {"vid1": "/nonexistent/vid1.info.json"}
    fake_cache = {"vid1": {"tags": ["testtag"], "title": "Test Video"}}
    stats = recompute_all_collections(fake_index, map_path, meta_cache=fake_cache)
    assert stats["matched"] == 1
    assert stats["unmatched"] == 0
    assert stats["skipped"] == 0
    result = _load_map(map_path)
    assert "vid1" in result["matched_ids"]


def test_recompute_with_meta_cache_skips_missing_entry(tmp_path):
    """meta_cache path: video in index but absent from cache → skipped, warning logged."""
    map_path = _make_map_with_rules(tmp_path)
    fake_index = {"vid_missing": "/nonexistent/missing.info.json"}
    # Cache intentionally does not contain vid_missing
    fake_cache: dict = {}
    stats = recompute_all_collections(fake_index, map_path, meta_cache=fake_cache)
    assert stats["skipped"] == 1
    assert stats["matched"] == 0
    result = _load_map(map_path)
    assert result["matched_ids"] == []
