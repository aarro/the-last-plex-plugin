#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import string
import urllib2  # type: ignore
from io import open

FILE_NAME = "_collection_map.json"


def Start():
    log_internal("Starting up ...")


def log_internal(msg):
    Log(msg)  # type: ignore


class YoutubeAsMovieAgent(Agent.Movies):  # type: ignore
    name, primary_provider, fallback_agent, contributes_to, languages, accepts_from = (
        "aarros-yt-dlp",
        True,
        False,
        None,
        [Locale.Language.English],  # type: ignore
        None,
    )

    def search(self, results, media, lang, **_):
        results.Append(
            MetadataSearchResult(  # type: ignore
                id="aarros-yt-dlp|{}|{}".format(
                    media.filename, media.openSubtitlesHash
                ),
                name=media.title,
                year=None,
                lang=lang,
                score=100,
            )
        )

        results.Sort("score", descending=True)

    def get_mapping_file_path(self, current_dir) -> str | None:
        """Find the mapping file, if it exists. It should be relative to the videos"""
        try:
            root_dir = os.path.abspath(".").split(os.path.sep)[0] + os.path.sep
            while (
                not os.path.exists(os.path.join(current_dir, FILE_NAME))
                and not current_dir == root_dir
            ):
                current_dir = os.path.dirname(current_dir)

            path = os.path.join(current_dir, FILE_NAME)
            if os.path.exists(path):
                log_internal("Found mapping file at: {}".format(path))
                return path
            else:
                log_internal("Unable to find {}".format(FILE_NAME))
        except Exception as e:
            log_internal("Failure loading collection mapping: {}".format(e))

        return None

    def set_collections(self, current_dir, info_json, metadata) -> None:
        """Load the collection_map, update it and return any collections
        that match.
        """
        mapping_json = ""
        collection_matches = []
        yt_id = info_json["id"]
        path = self.get_mapping_file_path(current_dir)

        with open(path, encoding="utf-8", mode="r") as json_file:
            map_data = json.load(json_file)

            # if we've already matched. skip the work
            if yt_id in map_data["matched_ids"]:
                log_internal("Already processed {}".format(yt_id))
                return

            def to_lower(s):
                return string.lower(s)

            def log_match(id, name, data_values):
                log_internal("Matched {} on {} with {}".format(id, name, data_values))

            tags = {to_lower(t) for t in info_json["tags"]}
            for c in map_data["collections"]:
                name = str(c["name"])
                for r in c["rules"]:
                    dv = info_json[r["field"]]
                    rv = r["values"]
                    msg = "Beginning rule check for {} - {} {}"
                    log_internal(msg.format(name, r["field"], r["match"]))

                    # special handling for tags...if we match a tag we're done
                    if isinstance(dv, list) and r["field"] == "tags":
                        matches = tags & {to_lower(r) for r in rv}
                        if matches:
                            collection_matches.append(name)
                            tags = tags - matches
                            log_match(yt_id, name, dv)
                            break
                    elif (
                        # partial matching list values is a bad idea imo
                        # so if data field values in a list, just exact match each one
                        isinstance(dv, list)
                        and {to_lower(d) for d in dv} & {to_lower(r) for r in rv}
                    ) or (
                        isinstance(dv, str)
                        and (
                            (r["match"] == "exact" and to_lower(rv) == to_lower(dv))
                            or (r["match"] == "in" and to_lower(rv) in to_lower(dv))
                        )
                    ):
                        collection_matches.append(name)
                        log_match(yt_id, name, dv)

            # tags remaining in the list are unused. We want to track those to see
            # patterns on newly imported videos
            for tag in tags:
                if tag not in map_data["unmatched_tags"]:
                    map_data["unmatched_tags"][tag] = 0
                map_data["unmatched_tags"][tag] = (
                    int(map_data["unmatched_tags"][tag]) + 1
                )

            # see /Framework/modelling/attributes.py#SetObject
            collections = list(set(collection_matches))
            if collections:
                map_data["matched_ids"].append(yt_id)
                metadata.collections.clear()
                for c in collections:
                    metadata.collections.add(c)

            log_internal("mapping json as dict {}".format(map_data))
            mapping_json = json.dumps(map_data, indent=2, encoding="utf-8")
            log_internal("mapping json as str {}".format(mapping_json))

        with open(path, encoding="utf-8", mode="w") as f:
            f.write(unicode(mapping_json))  # type: ignore

        log_internal(
            "Finished mapping collections for {} with names {}".format(
                yt_id, collections
            )
        )

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
