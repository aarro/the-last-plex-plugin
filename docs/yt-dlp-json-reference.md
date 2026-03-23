# yt-dlp JSON Reference

Reference for the two JSON files YAMP reads from disk: the per-video `*.info.json` and the
per-channel `*.channel.json` saved by `_fetch_channel_art`.

---

## Video — `*.info.json`

Written by yt-dlp alongside each downloaded media file.
Filename pattern: `Video Title [VIDEO_ID].info.json`

```json
{
  "id":            "aG_9T2uOLeM",
  "display_id":    "aG_9T2uOLeM",
  "title":         "Another Sky - Watching Basinski (Live Session)",
  "fulltitle":     "Another Sky - Watching Basinski (Live Session)",
  "description":   "Another Sky - 'Watching Basinski' out now!\n\nListen: https://...",

  "channel":             "Another Sky",
  "channel_id":          "UCXyNhY9FcGAQOVnNIDimrbw",
  "channel_url":         "https://www.youtube.com/channel/UCXyNhY9FcGAQOVnNIDimrbw",
  "channel_follower_count": 5330,
  "channel_is_verified": true,

  "uploader":     "Another Sky",
  "uploader_id":  "@anotherskyvevo2939",
  "uploader_url": "https://www.youtube.com/@anotherskyvevo2939",

  "upload_date":  "20230623",
  "timestamp":    1687492800,

  "duration":         225,
  "duration_string":  "3:45",

  "thumbnail": "https://i.ytimg.com/vi/aG_9T2uOLeM/maxresdefault.jpg",

  "categories": ["Music"],
  "tags":       ["Another Sky", "Fiction", "Alternative"],

  "view_count":    4537,
  "like_count":    126,
  "comment_count": 11,

  "live_status": "not_live",
  "is_live":     false,
  "was_live":    false,
  "media_type":  "video",
  "age_limit":   0,
  "availability": "public",

  "extractor":     "youtube",
  "extractor_key": "Youtube",
  "webpage_url":   "https://www.youtube.com/watch?v=aG_9T2uOLeM",

  "width":        3840,
  "height":       2160,
  "resolution":   "3840x2160",
  "fps":          25,
  "aspect_ratio": 1.78,
  "vcodec":       "vp9",
  "acodec":       "opus",
  "ext":          "webm",
  "format":       "313 - 3840x2160 (2160p)+251 - audio only (medium)",
  "format_id":    "313+251",

  "epoch": 1774279906,

  "formats":             [ "... one entry per available quality, large array, skipped ..." ],
  "thumbnails":          [ "... multiple resolution variants, skipped ..." ],
  "subtitles":           {},
  "automatic_captions":  {},
  "heatmap":             [ "... engagement heatmap, skipped ..." ]
}
```

### Fields YAMP uses

| Field | Where used | Notes |
|---|---|---|
| `id` | `extract_video_id()` fallback; index key | YouTube IDs are exactly 11 chars; Bilibili IDs start with `BV` |
| `title` | Plex `title` | |
| `description` | Plex `summary` | |
| `upload_date` | Plex `originallyAvailableAt`, `year` | Format: `YYYYMMDD` string |
| `duration` | Plex `duration` | Seconds; multiplied ×1000 for Plex (ms) |
| `thumbnail` | Plex `thumb`; thumbnail proxy | Direct CDN URL; YAMP proxies it so Plex can always reach it |
| `categories` | Plex `Genre[].tag` | |
| `channel` | Plex `Director[].tag`; collection matching | |
| `tags` | Collection rule matching (`MATCH_FIELDS`) | Consumed on match to prevent double-matching |
| `extractor` | Plex `studio` | e.g. `"youtube"`, `"bilibili"` |
| `uploader_url` | Channel art prefetch trigger | Used as the key into `_channel_art_cache` |

### Fields used in collection matching (`MATCH_FIELDS` in `collection_map.py`)

`title`, `description`, `channel`, `uploader`, `tags`, `categories`

---

## Channel — `*.channel.json`

Written by `_fetch_channel_art` when it successfully fetches channel metadata via yt-dlp.
Saved as `<sanitized_channel_name>.channel.json` in the channel's subdirectory (matched by
the `[channel_id]` suffix in the dir name — how yt-dlp's default output template names
directories), or at the data root if no matching dir exists.

The `entries` key (the full video listing) is stripped before saving — it can be thousands
of items and is not useful for debugging artwork issues.

```json
{
  "id":           "UC-smeLB9AnOTeypr1YyjJ3A",
  "channel":      "ARTE Concert",
  "channel_id":   "UC-smeLB9AnOTeypr1YyjJ3A",
  "title":        "ARTE Concert",
  "availability": null,

  "channel_follower_count": 1920000,
  "description":  "",
  "tags":         ["ARTE", "concert", "live", "web", "music"],
  "playlist_count": 3,

  "uploader":     "ARTE Concert",
  "uploader_id":  "@arteconcert",
  "uploader_url": "https://www.youtube.com/@arteconcert",
  "channel_url":  "https://www.youtube.com/channel/UC-smeLB9AnOTeypr1YyjJ3A",

  "modified_date": null,
  "view_count":    null,
  "release_year":  null,

  "extractor":      "youtube:tab",
  "extractor_key":  "YoutubeTab",
  "_type":          "playlist",
  "webpage_url":    "https://www.youtube.com/channel/UC-smeLB9AnOTeypr1YyjJ3A",
  "original_url":   "https://www.youtube.com/@arteconcert",
  "epoch":          1774297897,

  "thumbnails": [
    { "id": "0", "preference": -10, "width": 1060, "height": 175, "resolution": "1060x175",
      "url": "https://yt3.googleusercontent.com/...=w1060-fcrop64=1,...-no-nd-rj" },
    { "id": "1", "preference": -10, "width": 1138, "height": 188, "resolution": "1138x188",
      "url": "https://yt3.googleusercontent.com/...=w1138-fcrop64=1,...-no-nd-rj" },
    { "id": "2", "preference": -10, "width": 1707, "height": 283, "resolution": "1707x283",
      "url": "https://yt3.googleusercontent.com/...=w1707-fcrop64=1,...-no-nd-rj" },
    { "id": "3", "preference": -10, "width": 2120, "height": 351, "resolution": "2120x351",
      "url": "https://yt3.googleusercontent.com/...=w2120-fcrop64=1,...-no-nd-rj" },
    { "id": "4", "preference": -10, "width": 2276, "height": 377, "resolution": "2276x377",
      "url": "https://yt3.googleusercontent.com/...=w2276-fcrop64=1,...-no-nd-rj" },
    { "id": "5", "preference": -10, "width": 2560, "height": 424, "resolution": "2560x424",
      "url": "https://yt3.googleusercontent.com/...=w2560-fcrop64=1,...-no-nd-rj" },
    { "id": "banner_uncropped", "preference": -5,
      "url": "https://yt3.googleusercontent.com/...=s0" },
    { "id": "7", "width": 900, "height": 900, "resolution": "900x900",
      "url": "https://yt3.googleusercontent.com/...=s900-c-k-c0x00ffffff-no-rj" },
    { "id": "avatar_uncropped", "preference": 1,
      "url": "https://yt3.googleusercontent.com/...=s0" }
  ]
}
```

### Thumbnail IDs (consistent across all observed channels)

| ID | Type | Size | Preference | Notes |
|---|---|---|---|---|
| `0`–`5` | Banner crop | 1060×175 → 2560×424 | `-10` | Same image, different widths; ~6:1 aspect ratio. On channels with no banner, `0` may be the avatar at 900×900 instead. |
| `banner_uncropped` | Banner full | native (no metadata) | `-5` | Full-resolution banner; no `width`/`height` fields. Used as `art` (Background) in Plex. |
| `7` | Avatar crop | 900×900 | none | Cropped/padded square variant. |
| `avatar_uncropped` | Avatar full | native (no metadata) | `1` | Full-resolution avatar; no `width`/`height` fields. Highest preference value. Used as `image` (Poster) in Plex. |

**Key finding:** match thumbnail IDs by their named `id` string, not by array index — the
array order is stable in practice but the IDs are explicit and safe to rely on. Both
`*_uncropped` variants lack `width`/`height` metadata but are the highest-quality options.

### Fields YAMP uses from channel.json

| Field | Where used |
|---|---|
| `channel` / `uploader` | `_fetch_channel_art` return value; filename for the `.channel.json` itself |
| `channel_id` | Used to find the channel's subdirectory (`[channel_id]` suffix match) |
| `thumbnails[id=avatar_uncropped].url` | Returned as `avatar_url` → suggested as collection `image` (poster) |
| `thumbnails[id=banner_uncropped].url` | Returned as `banner_url` → suggested as collection `art` (background) |
| `thumbnail` (top-level) | Fallback `avatar_url` when `avatar_uncropped` is absent |
