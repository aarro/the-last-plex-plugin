import json
import shutil
from pathlib import Path

import pytest

from collection_map import find_collection_map, resolve_collections

FIXTURES = Path(__file__).parent / "fixtures"


def _fresh_map(tmp_path: Path) -> tuple[str, str]:
    """Copy fixture files to tmp_path and return (info_json_path, map_path)."""
    map_path = tmp_path / "_collection_map.json"
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


def test_wrong_case_match_type_does_not_match(tmp_path):
    """Rules with wrong-case match type (e.g. 'EXACT') must be skipped."""
    info = _load_info()
    info["id"] = "wrong_match_type_001"
    info["tags"] = []
    info["title"] = "Something Unrelated"
    info["channel"] = "RandomChannel"

    # Build a map with a rule that uses "EXACT" (wrong case) — should be rejected
    map_data = {
        "collections": [
            {
                "name": "Bad Rule Collection",
                "rules": [
                    {"field": "channel", "match": "EXACT", "values": ["RandomChannel"]},
                ],
            }
        ],
        "matched_ids": [],
        "unmatched_ids": [],
        "unmatched_tags": {},
    }
    map_path = tmp_path / "_collection_map.json"
    map_path.write_text(json.dumps(map_data), encoding="utf-8")

    result = resolve_collections(info, str(map_path))
    assert result == []


# ── find_collection_map ───────────────────────────────────────────────────────


def test_find_collection_map_direct(tmp_path):
    map_path = tmp_path / "_collection_map.json"
    map_path.write_text("{}", encoding="utf-8")
    found = find_collection_map(str(tmp_path), str(tmp_path))
    assert found == str(map_path)


def test_find_collection_map_walk_up(tmp_path):
    map_path = tmp_path / "_collection_map.json"
    map_path.write_text("{}", encoding="utf-8")
    subdir = tmp_path / "channel" / "videos"
    subdir.mkdir(parents=True)
    found = find_collection_map(str(subdir), str(tmp_path))
    assert found == str(map_path)


def test_find_collection_map_not_found(tmp_path):
    found = find_collection_map(str(tmp_path), str(tmp_path))
    assert found is None
