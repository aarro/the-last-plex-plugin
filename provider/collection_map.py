"""
Collection map: rule-based matching of yt-dlp videos to Plex collections.

Ported from the legacy Plex .bundle agent, updated to Python 3.
"""

import json
import logging
import operator
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MAPPING_FILE_NAME = "_collection_map.json"

_MAP_LOCK = threading.Lock()


def find_collection_map(start_dir: str, root: str | None = None) -> str | None:
    """Walk up from start_dir to find _collection_map.json, stopping at root."""
    current = Path(start_dir).resolve()
    stop = Path(root).resolve() if root else Path(current.anchor)

    while True:
        candidate = current / MAPPING_FILE_NAME
        if candidate.exists():
            logger.info("Found mapping file at: %s", candidate)
            return str(candidate)
        if current == stop or current == current.parent:
            break
        current = current.parent

    logger.warning("Unable to find %s starting from %s", MAPPING_FILE_NAME, start_dir)
    return None


def load_map(mapping_path: str) -> dict:
    try:
        with open(mapping_path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise OSError(f"Failed to open collection map '{mapping_path}': {e}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Failed to parse collection map '{mapping_path}': {e}") from e


def save_map(mapping_path: str, data: dict) -> None:
    content = json.dumps(data, indent=2, ensure_ascii=False)
    tmp_path = mapping_path + ".tmp"
    try:
        with open(tmp_path, encoding="utf-8", mode="w") as f:
            f.write(content)
        os.replace(tmp_path, mapping_path)
    except OSError:
        # Clean up temp file if it was created
        try:
            os.unlink(tmp_path)
        except OSError as unlink_err:
            logger.warning("save_map: failed to clean up temp file '%s': %s", tmp_path, unlink_err)
        raise


def match_video(info_json: dict, collections: list[dict]) -> tuple[list[str], set[str]]:
    """
    Pure function: apply collection rules to a video.

    Returns (matched_names, remaining_tags) where remaining_tags excludes
    any tags consumed during collection matching.
    """
    tags = {t.lower() for t in info_json.get("tags", [])}
    collection_matches: list[str] = []

    for collection in collections:
        c_name = str(collection.get("name", ""))
        for rule in collection.get("rules", []):
            field_name = rule.get("field")
            match_type = rule.get("match")
            rule_values_raw = rule.get("values")
            if not field_name or not match_type or not rule_values_raw:
                continue
            if match_type not in ("exact", "in"):
                logger.warning("match_video: unknown match_type %r in collection '%s' rule — skipping", match_type, c_name)
                continue
            if field_name not in info_json:
                continue

            rule_values = {v.lower() for v in rule_values_raw}
            raw = info_json[field_name]

            if isinstance(raw, list):
                v_values = [v.lower() for v in raw]
            elif isinstance(raw, str):
                v_values = [raw.lower()]
            else:
                continue

            if field_name == "tags":
                if match_type == "exact":
                    matched = tags & rule_values
                else:  # "in" — substring match against tags
                    matched = {t for t in tags if any(rv in t for rv in rule_values)}
                if matched:
                    collection_matches.append(c_name)
                    tags -= matched
                    break
            elif match_type == "exact" and rule_values & set(v_values):
                collection_matches.append(c_name)
                break
            elif match_type == "in" and any(rv in iv for rv in rule_values for iv in v_values):
                collection_matches.append(c_name)
                break

    return list(set(collection_matches)), tags


def recompute_all_collections(video_index: dict[str, str], mapping_path: str) -> dict:
    """
    Re-run collection matching against all indexed videos.

    Clears and rebuilds matched_ids, unmatched_ids, and unmatched_tags from scratch.
    Returns {"matched": int, "unmatched": int} stats.
    """
    with _MAP_LOCK:
        mapping_data = load_map(mapping_path)
        collections = mapping_data.get("collections", [])

        matched_ids: list[str] = []
        unmatched_ids: list[str] = []
        unmatched_tags: dict[str, int] = {}
        skipped = 0

        for video_id, path in video_index.items():
            try:
                with open(path, encoding="utf-8") as f:
                    info_json = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("recompute: skipping %s: %s", video_id, e)
                skipped += 1
                continue

            c_matches, _ = match_video(info_json, collections)
            if c_matches:
                matched_ids.append(video_id)
            else:
                unmatched_ids.append(video_id)
                for tag in info_json.get("tags", []):
                    t = tag.lower()
                    unmatched_tags[t] = unmatched_tags.get(t, 0) + 1

        mapping_data["matched_ids"] = matched_ids
        mapping_data["unmatched_ids"] = unmatched_ids
        mapping_data["unmatched_tags"] = dict(
            sorted(unmatched_tags.items(), key=operator.itemgetter(1), reverse=True)
        )

        save_map(mapping_path, mapping_data)
        if skipped:
            logger.error("recompute_all_collections: %d video(s) skipped due to read/parse errors", skipped)
        logger.info("recompute_all_collections: %d matched, %d unmatched, %d skipped", len(matched_ids), len(unmatched_ids), skipped)
        return {"matched": len(matched_ids), "unmatched": len(unmatched_ids), "skipped": skipped}


def resolve_collections(info_json: dict, mapping_path: str) -> list[str]:
    """
    Apply collection rules to a video's info_json.

    Updates the mapping file with match state and unmatched tag counts only
    if this video has not been previously tracked (not in matched_ids or
    unmatched_ids). Returns list of matched collection names.
    """
    with _MAP_LOCK:
        v_id = info_json.get("id", "")
        mapping_data = load_map(mapping_path)

        matched_set = set(mapping_data.get("matched_ids", []))
        unmatched_set = set(mapping_data.get("unmatched_ids", []))
        already_tracked = v_id in matched_set or v_id in unmatched_set

        # Always compute collections so Plex gets the right data on every fetch;
        # state updates (file writes) are skipped for already-tracked videos.
        c_matches, remaining_tags = match_video(info_json, mapping_data.get("collections", []))

        logger.info(
            "%s: Collection matching result: %s (remaining tags: %s)",
            v_id, c_matches, remaining_tags,
        )

        # Only update state if this is a new video (not yet tracked in either list)
        if not already_tracked:
            fresh_append = False

            if c_matches:
                if v_id in unmatched_set:
                    mapping_data["unmatched_ids"].remove(v_id)
                matched_ids = mapping_data.setdefault("matched_ids", [])
                if v_id not in matched_set:
                    matched_ids.append(v_id)
                    fresh_append = True
            elif v_id not in unmatched_set:
                mapping_data.setdefault("unmatched_ids", []).append(v_id)
                fresh_append = True

            # Track unused tags only for newly-seen unmatched videos to surface collection patterns
            if fresh_append and not c_matches and remaining_tags:
                unmatched_tags: dict[str, int] = mapping_data.setdefault("unmatched_tags", {})
                for tag in remaining_tags:
                    try:
                        current = int(unmatched_tags.get(tag, 0))
                    except (ValueError, TypeError):
                        logger.warning(
                            "Non-numeric count for tag %r in unmatched_tags (got %r) — treating as 0",
                            tag, unmatched_tags.get(tag),
                        )
                        current = 0
                    unmatched_tags[tag] = current + 1
                mapping_data["unmatched_tags"] = dict(
                    sorted(unmatched_tags.items(), key=operator.itemgetter(1), reverse=True)
                )

            save_map(mapping_path, mapping_data)

        logger.info("%s: Finished collection matching — result: %s", v_id, c_matches)
        return c_matches
