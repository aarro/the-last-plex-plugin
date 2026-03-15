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
        except OSError:
            pass
        raise


def resolve_collections(info_json: dict, mapping_path: str) -> list[str]:
    """
    Apply collection rules to a video's info_json.

    Updates the mapping file with match state and unmatched tag counts.
    Returns list of matched collection names.
    """
    with _MAP_LOCK:
        v_id = info_json.get("id", "")
        mapping_data = load_map(mapping_path)

        already_matched = v_id in mapping_data.get("matched_ids", [])

        # Always compute collections so Plex gets the right data on every fetch
        tags = {t.lower() for t in info_json.get("tags", [])}
        collection_matches: list[str] = []

        for collection in mapping_data.get("collections", []):
            c_name = str(collection.get("name", ""))
            for rule in collection.get("rules", []):
                field_name = rule.get("field")
                match_type = rule.get("match")
                rule_values_raw = rule.get("values")
                if not field_name or not match_type or not rule_values_raw:
                    continue
                if match_type not in ("exact", "in"):
                    logger.warning("%s: Unknown match type %r in collection '%s' — skipping rule", v_id, match_type, c_name)
                    continue

                if field_name not in info_json:
                    logger.info("%s: field '%s' not found in info_json", v_id, field_name)
                    continue

                rule_values = {v.lower() for v in rule_values_raw}
                raw = info_json[field_name]

                if isinstance(raw, list):
                    v_values = [v.lower() for v in raw]
                elif isinstance(raw, str):
                    v_values = [raw.lower()]
                else:
                    logger.info("%s: Unknown field type %s for '%s'", v_id, type(raw), field_name)
                    continue

                logger.info(
                    "%s: Collection '%s' rule check — field=%s match=%s rule=%s info=%s",
                    v_id, c_name, field_name, match_type, rule_values, v_values,
                )

                if field_name == "tags":
                    # Tags: set intersection; consuming matched tags prevents double-counting
                    matched = tags & rule_values
                    if matched:
                        collection_matches.append(c_name)
                        tags -= matched
                        logger.info("%s: Matched '%s' on tags %s", v_id, c_name, matched)
                        break
                elif match_type == "exact" and rule_values & set(v_values):
                    collection_matches.append(c_name)
                    logger.info("%s: Matched '%s' (exact) with %s", v_id, c_name, v_values)
                elif match_type == "in" and any(rv in iv for rv in rule_values for iv in v_values):
                    collection_matches.append(c_name)
                    logger.info("%s: Matched '%s' (in) with %s", v_id, c_name, v_values)
                else:
                    logger.info("%s: No match for collection '%s'", v_id, c_name)

        c_matches = list(set(collection_matches))

        # Only update state if this is a new video (not yet tracked)
        if not already_matched:
            fresh_append = False

            if c_matches:
                if v_id in mapping_data.get("unmatched_ids", []):
                    mapping_data["unmatched_ids"].remove(v_id)
                matched_ids = mapping_data.setdefault("matched_ids", [])
                if v_id not in matched_ids:
                    matched_ids.append(v_id)
                    fresh_append = True
            elif v_id not in mapping_data.get("unmatched_ids", []):
                mapping_data.setdefault("unmatched_ids", []).append(v_id)
                fresh_append = True

            # Track unused tags from newly-seen unmatched videos to surface collection patterns
            if fresh_append and tags:
                unmatched_tags: dict[str, int] = mapping_data.setdefault("unmatched_tags", {})
                for tag in tags:
                    try:
                        current = int(unmatched_tags.get(tag, 0))
                    except (ValueError, TypeError):
                        logger.warning("Non-numeric count for tag %r in unmatched_tags — treating as 0", tag)
                        current = 0
                    unmatched_tags[tag] = current + 1
                mapping_data["unmatched_tags"] = dict(
                    sorted(unmatched_tags.items(), key=operator.itemgetter(1), reverse=True)
                )

            save_map(mapping_path, mapping_data)

        logger.info("%s: Finished collection matching — result: %s", v_id, c_matches)
        return c_matches
