#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import string
import urllib2  # type: ignore
from io import open

MAPPING_FILE_NAME = "_collection_map.json"
SOURCE = "YouTube as Movies"
LANGUAGES = [Locale.Language.NoLanguage, Locale.Language.English]  # type: ignore
CON_AGENTS = ["com.plexapp.agents.none"]
REF_AGENTS = ["com.plexapp.agents.localmedia"]


def Start():
    log_internal("Starting up ...")


def log_internal(msg):
    Log(msg)  # type: ignore


def log_match(id, name, data_values):
    log_internal("Matched {} on {} with {}".format(id, name, data_values))


def to_lower(s):
    return string.lower(s)


class YoutubeAsMovieAgent(Agent.Movies):  # type: ignore
    accepts_from = REF_AGENTS
    contributes_to = CON_AGENTS
    fallback_agent = None
    languages = LANGUAGES
    name = SOURCE
    primary_provider = True

    def get_mapping_file_path(self, current_dir):
        """Find the mapping file, if it exists. It should be relative to the videos"""
        try:
            root_dir = os.path.abspath(".").split(os.path.sep)[0] + os.path.sep
            while (
                not os.path.exists(os.path.join(current_dir, MAPPING_FILE_NAME))
                and not current_dir == root_dir
            ):
                current_dir = os.path.dirname(current_dir)

            path = os.path.join(current_dir, MAPPING_FILE_NAME)
            if os.path.exists(path):
                log_internal("Found mapping file at: {}".format(path))
                return path
            else:
                log_internal("Unable to find {}".format(MAPPING_FILE_NAME))
        except Exception as e:
            log_internal("Failure loading collection mapping: {}".format(e))

        return None

    def set_collections(self, current_dir, info_json, metadata):
        """
        Load the collection_map, update it and return any collections that match.
        """
        collection_mapping_file = self.get_mapping_file_path(current_dir)
        collection_matches = []
        v_id = info_json["id"]
        mapping_json = ""

        with open(collection_mapping_file, encoding="utf-8", mode="r") as json_file:
            mapping_data = json.load(json_file)

            # if we've already matched. skip the work
            if v_id in mapping_data["matched_ids"]:
                log_internal("Already processed {}".format(v_id))
                return

            tags = {to_lower(t) for t in info_json["tags"]}
            for c in mapping_data["collections"]:
                c_name = str(c["name"])
                for r in c["rules"]:
                    field_name = r["field"]
                    match = r["match"]
                    collection_rule_values = {to_lower(r) for r in r["values"]}
                    metadata_field_values = info_json[field_name]

                    v_values = []
                    if isinstance(metadata_field_values, list):
                        v_values = [to_lower(v) for v in metadata_field_values]
                    elif (
                        isinstance(metadata_field_values, str)
                        or isinstance(metadata_field_values, unicode),  # type: ignore
                    ):
                        v_values = [to_lower(metadata_field_values)]
                    else:
                        msg = "Unable to process {}, unknown field type {}"
                        log_internal(msg.format(v_id, type(metadata_field_values)))

                    if v_values:
                        log_internal(
                            "Collection {} start rule check. INFO {} {} RULE {}".format(
                                c_name, v_values, match, collection_rule_values
                            )
                        )

                        if field_name == "tags":
                            # special handling for tags...if we match a tag we're done
                            matches = tags & collection_rule_values
                            if matches:
                                collection_matches.append(c_name)
                                tags = tags - matches
                                log_match(v_id, c_name, v_values)
                                break
                        elif (
                            match == "exact" and collection_rule_values & set(v_values)
                        ) or (
                            match == "in"
                            and [
                                info_value
                                for rule_value in list(collection_rule_values)
                                for info_value in v_values
                                if str(rule_value) in str(info_value)
                            ]
                        ):
                            collection_matches.append(c_name)
                            log_match(v_id, c_name, v_values)
                        else:
                            log_internal("No match found")

            # if we matched on a collection, reset the file's collections to it
            collections = list(set(collection_matches))
            fresh_append = False
            if collections:
                # see /Framework/modelling/attributes.py#SetObject
                metadata.collections.clear()
                for c in collections:
                    metadata.collections.add(c)

                # remove it from the unmatched_ids and ensure that it's in matched_ids
                mapping_data["unmatched_ids"].remove(v_id)
                if v_id not in mapping_data["matched_ids"]:
                    mapping_data["matched_ids"].append(v_id)
                    fresh_append = True
            elif v_id not in mapping_data["unmatched_ids"]:
                mapping_data["unmatched_ids"].append(v_id)
                fresh_append = True

            # tags remaining in the list are unused. We want to track those to see
            # patterns on newly imported videos, but don't want to double-track
            if fresh_append:
                for tag in tags:
                    if tag not in mapping_data["unmatched_tags"]:
                        mapping_data["unmatched_tags"][tag] = 0
                    mapping_data["unmatched_tags"][tag] = (
                        int(mapping_data["unmatched_tags"][tag]) + 1
                    )

            mapping_json = json.dumps(mapping_data, indent=2, encoding="utf-8")

        if mapping_json:
            with open(collection_mapping_file, encoding="utf-8", mode="w") as f:
                f.write(unicode(mapping_json))  # type: ignore

        finished_msg = "Finished mapping collections for {} with names {}"
        log_internal(finished_msg.format(v_id, collections))

    def update(self, metadata, media, lang, **kwargs):
        log_internal("".ljust(157, "="))

        try:
            filename = media.items[0].parts[0].file
            current_dir = os.path.dirname(filename)
            filename = os.path.basename(filename)
            filename = urllib2.unquote(filename)
            info_json_file_path = os.path.join(
                current_dir,
                os.path.splitext(filename)[0] + ".info.json",
            )
            if os.path.exists(info_json_file_path):
                log_internal("info : found {}".format(info_json_file_path))
            else:
                log_internal(
                    "warn : missing {} in {}".format(info_json_file_path, current_dir)
                )
                return

            with open(info_json_file_path, encoding="utf-8") as f:
                info_json = json.load(f)

                date = Datetime.ParseDate(info_json["upload_date"])  # type: ignore

                metadata.duration = info_json["duration"]
                metadata.genres = info_json["categories"]
                metadata.originally_available_at = date.date()
                metadata.studio = info_json["extractor"]
                metadata.summary = info_json["description"]
                metadata.title = info_json["title"]
                metadata.year = date.year

                self.set_collections(current_dir, info_json, metadata)

        except Exception as e:
            log_internal("update - error: filename: {}, e: {}".format(filename, e))

    def search(self, results, media, lang, **_):
        results.Append(
            MetadataSearchResult(  # type: ignore
                id="youtube-as-movies|{}|{}".format(
                    media.filename, media.openSubtitlesHash
                ),
                name=media.title,
                year=None,
                lang=lang,
                score=100,
            )
        )
        results.Sort("score", descending=True)


# ---- unused MetadataModel fields (baseclass)
# audience_rating           : float
# audience_rating_image     : str
# original_title            : str
# rating                    : float
# rating_count              : int
# rating_image              : str
# reviews                   : Review
# tags                      : set[str]
# title_sort                : str

# ---- unused Movie fields
# art                       : MediaProxyContainer
# banners                   : MediaProxyContainer
# chapters                  : set[Chapter]
# content_rating            : str
# content_rating_age        : int
# countries                 : set[str]
# directors                 : Person
# extras                    : enum? Trailer, DeletedScene, BehindTheScenes, Interview,
#                                   SceneOrSample, Featurette, Short, Other
# posters                   : MediaProxyContainer
# producers                 : Person
# quotes                    : str
# roles                     : Person
# similar                   : set[str]
# tagline                   : str
# themes                    : MediaProxyContainer
# trivia                    : str
# writers                   : Person
