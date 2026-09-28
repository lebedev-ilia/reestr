from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator

from fetcher.dataset_collector.schemas import CollectedVideo

# Preferred caption languages in shards (ext list only; no timedtext URLs).
CAPTION_LANG_PREFERENCE = ("ru", "en")


def _jsonable(payload: Any) -> Any:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))

# Internal bucket names (collector) -> legacy training dataset labels (main_ready / data_00).
LEGACY_TIME_INTERVAL: Dict[str, str] = {
    "lt_1d": "less-1day",
    "1d_1w": "1day-1week",
    "1w_1m": "1week-1month",
    "1m_3m": "1month-3month",
    "3m_6m": "3month-6month",
    "6m_1y": "6month-1year",
    "1y_3y": "1year-3year",
    "gt_3y": "3year-more",
}


def legacy_time_interval(value: str | None) -> str | None:
    if value is None:
        return None
    return LEGACY_TIME_INTERVAL.get(value, value)


def normalize_iso_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("+00:00"):
        return f"{value[:-6]}Z"
    return value


def format_training_comment(comment: dict) -> dict:
    return {
        "text": comment.get("text") or "",
        "likeCount": int(comment.get("likeCount") or 0),
        "repliesCount": int(comment.get("repliesCount", comment.get("replyCount")) or 0),
        "publishedAt": normalize_iso_timestamp(comment.get("publishedAt")),
        "authorName": comment.get("authorName") or "",
    }


def format_training_snapshot(snapshot: dict) -> dict:
    payload: Dict[str, Any] = {
        "viewCount": snapshot.get("viewCount"),
        "likeCount": snapshot.get("likeCount"),
        "commentCount": snapshot.get("commentCount"),
        "comments": [format_training_comment(item) for item in (snapshot.get("comments") or [])],
        "time_get": snapshot.get("time_get"),
    }
    for key in ("subscriberCount", "videoCount", "viewCount_channel"):
        if snapshot.get(key) is not None:
            payload[key] = snapshot.get(key)
    return payload


def extract_ytdlp_formats(info: dict) -> list[dict]:
    seen: set[tuple[Any, Any]] = set()
    formats: list[dict] = []
    for fmt in info.get("formats") or []:
        if fmt.get("vcodec") in (None, "none"):
            continue
        resolution = fmt.get("resolution")
        if not resolution and fmt.get("width") and fmt.get("height"):
            resolution = f"{fmt['width']}x{fmt['height']}"
        fps = fmt.get("fps")
        key = (fps, resolution)
        if key in seen:
            continue
        seen.add(key)
        entry: Dict[str, Any] = {}
        if fps is not None:
            entry["fps"] = fps
        if resolution:
            entry["resolution"] = resolution
        if entry:
            formats.append(entry)
    return formats


def _resolution_height(value: str | None) -> int:
    if not value:
        return 0
    if "x" in value:
        try:
            return int(value.rsplit("x", 1)[1])
        except ValueError:
            return 0
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else 0


def extract_best_enrich_formats(info: dict, *, download_cap_height: int = 1080) -> list[dict]:
    """Keep global max format and max format download can fetch (<=1080 by default)."""
    formats = extract_ytdlp_formats(info)
    if not formats:
        return []
    ordered = sorted(formats, key=lambda item: _resolution_height(item.get("resolution")))
    best_overall = ordered[-1]
    downloadable = [fmt for fmt in ordered if _resolution_height(fmt.get("resolution")) <= download_cap_height]
    selected = [best_overall]
    if downloadable:
        selected.append(downloadable[-1])
    out: list[dict] = []
    seen: set[tuple[Any, Any]] = set()
    for fmt in selected:
        key = (fmt.get("fps"), fmt.get("resolution"))
        if key not in seen:
            out.append(fmt)
            seen.add(key)
    return out


def normalize_caption_lang(lang: str) -> str | None:
    """Map yt-dlp language codes to shard keys: ru or en."""
    normalized = str(lang).replace("_", "-").lower()
    if normalized == "ru" or normalized.startswith("ru-"):
        return "ru"
    if normalized == "en" or normalized.startswith("en"):
        return "en"
    return None


def slim_caption_tracks(tracks: dict | None) -> dict:
    """Keep ru/en caption tracks (merge en-US/en-GB → en); ext list only, no URLs."""
    if not tracks or not isinstance(tracks, dict):
        return {}
    exts_by_lang: Dict[str, list[str]] = {}
    for lang, entries in tracks.items():
        canon = normalize_caption_lang(str(lang))
        if not canon:
            continue
        bucket = exts_by_lang.setdefault(canon, [])
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            ext = entry.get("ext")
            if ext and str(ext) not in bucket:
                bucket.append(str(ext))
    slim: Dict[str, Any] = {}
    for lang in CAPTION_LANG_PREFERENCE:
        if lang in exts_by_lang:
            slim[lang] = [{"ext": ext} for ext in exts_by_lang[lang]]
    return slim


def metadata_captions_are_bloated(metadata: dict) -> bool:
    """True when caption entries still carry timedtext URLs (not plain text bodies)."""
    for key in ("automatic_captions", "subtitles"):
        block = metadata.get(key) or {}
        if not isinstance(block, dict):
            continue
        for entries in block.values():
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("url"):
                        return True
            elif isinstance(entries, dict) and entries.get("url"):
                return True
    return False


def compact_training_metadata(metadata: dict) -> dict:
    """Strip caption URLs; keep ext+text payloads from enrich."""
    merged = dict(metadata)

    def _compact_block(block: dict | None) -> dict:
        if not block or not isinstance(block, dict):
            return {}
        out: Dict[str, Any] = {}
        for lang, val in block.items():
            canon = normalize_caption_lang(str(lang))
            if not canon:
                continue
            if isinstance(val, dict) and val.get("text"):
                entry = {
                    "language": canon,
                    "ext": val.get("ext") or "vtt",
                    "text": val.get("text"),
                }
                if val.get("cues"):
                    entry["cues"] = val.get("cues")
                out[canon] = entry
            elif isinstance(val, list):
                slim = slim_caption_tracks({lang: val})
                if canon in slim:
                    out[canon] = slim[canon][0] if slim[canon] else {}
        return out

    merged["automatic_captions"] = _compact_block(merged.get("automatic_captions"))
    merged["subtitles"] = _compact_block(merged.get("subtitles"))
    return merged


def extract_ytdlp_thumbnails(info: dict) -> list[dict]:
    thumbnails: list[dict] = []
    for thumb in info.get("thumbnails") or []:
        url = thumb.get("url")
        if not url:
            continue
        entry: Dict[str, Any] = {"url": url}
        for key in ("preference", "id", "height", "width", "resolution"):
            if thumb.get(key) is not None:
                entry[key] = thumb.get(key)
        thumbnails.append(entry)
    return thumbnails


def extract_best_ytdlp_thumbnails(info: dict, *, limit: int = 2) -> list[dict]:
    thumbnails = extract_ytdlp_thumbnails(info)
    return sorted(
        thumbnails,
        key=lambda item: (
            item.get("preference") if item.get("preference") is not None else -999,
            item.get("height") or 0,
            item.get("width") or 0,
        ),
        reverse=True,
    )[:limit]


def merge_ytdlp_into_training_metadata(metadata: dict, info: dict) -> dict:
    """Fill training metadata fields that YouTube Data API often omits."""
    merged = dict(metadata)
    if info.get("title") and not merged.get("title"):
        merged["title"] = info["title"]
    if info.get("description"):
        merged["description"] = info["description"]
    if info.get("tags"):
        merged["tags"] = info["tags"]
    if info.get("language"):
        merged["language"] = info["language"]
    duration = info.get("duration")
    if duration is not None:
        try:
            duration_int = int(duration)
            merged["duration"] = duration_int
            merged["duration_seconds"] = duration_int
        except (TypeError, ValueError):
            pass
    if info.get("channel"):
        merged["channelTitle"] = merged.get("channelTitle") or info.get("channel")
    formats = extract_ytdlp_formats(info)
    if formats:
        merged["formats"] = formats
    thumbnails_ytdlp = extract_ytdlp_thumbnails(info)
    if thumbnails_ytdlp:
        merged["thumbnails_ytdlp"] = thumbnails_ytdlp
    from fetcher.dataset_collector.caption_text import build_caption_metadata

    caption_manual = info.get("_caption_texts_manual") or {}
    caption_auto = info.get("_caption_texts_auto") or {}
    merged["subtitles"] = build_caption_metadata(info.get("subtitles"), caption_manual)
    merged["automatic_captions"] = build_caption_metadata(
        info.get("automatic_captions"), caption_auto
    )
    if info.get("chapters") is not None:
        merged["chapters"] = info.get("chapters")
    return compact_training_metadata(merged)


def training_entry_needs_ytdlp_enrichment(entry: dict) -> bool:
    from fetcher.dataset_collector.caption_text import captions_need_text_download

    metadata = entry.get("metadata") or {}
    enriched = entry.get("_enriched") or {}
    if enriched.get("source") == "yt_dlp":
        return captions_need_text_download(metadata)
    has_core = bool(metadata.get("formats") or metadata.get("thumbnails_ytdlp"))
    if has_core and not captions_need_text_download(metadata):
        return False
    return True


def format_training_metadata(record: dict) -> dict:
    metadata = record.get("metadata") or {}
    raw = metadata.get("raw") or {}
    snippet = raw.get("snippet") or {}
    status = raw.get("status") or {}
    channel_raw = metadata.get("channel") or {}
    channel_snippet = channel_raw.get("snippet") or {}
    thumbnails = snippet.get("thumbnails") or {}
    standard_thumb = (
        thumbnails.get("standard") or thumbnails.get("high") or thumbnails.get("medium") or {}
    )

    duration_seconds = metadata.get("duration_seconds")
    if duration_seconds is not None:
        try:
            duration_seconds = int(duration_seconds)
        except (TypeError, ValueError):
            duration_seconds = None

    return {
        "title": metadata.get("title") or snippet.get("title") or "",
        "description": metadata.get("description") or snippet.get("description") or "",
        "tags": snippet.get("tags") or [],
        "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        "madeForKids": bool(status.get("madeForKids", False)),
        "duration": duration_seconds,
        "publishedAt": normalize_iso_timestamp(snippet.get("publishedAt") or metadata.get("publishedAt")),
        "channelTitle": metadata.get("channelTitle") or snippet.get("channelTitle") or "",
        "country": channel_snippet.get("country"),
        "thumbnails": {"standard": standard_thumb} if standard_thumb else {},
        "subtitles": {},
        "automatic_captions": {},
        "chapters": None,
        "formats": [],
        "thumbnails_ytdlp": [],
        "duration_seconds": duration_seconds,
    }


def format_training_record(record: dict) -> tuple[str, dict]:
    """Convert internal collector record to one training JSON entry (data_XX style)."""
    video_id = record["video_id"]
    payload: Dict[str, Any] = {
        "query": record.get("query") or "",
        "time_interval": legacy_time_interval(record.get("time_interval")),
        "metadata": format_training_metadata(record),
        "snapshot_0": format_training_snapshot(record.get("snapshot_0") or {}),
    }
    return video_id, payload


def format_training_shard(records: Iterable[CollectedVideo | dict]) -> Dict[str, dict]:
    shard: Dict[str, dict] = {}
    for record in records:
        if isinstance(record, CollectedVideo):
            internal = _jsonable(record.dict())
        else:
            internal = record
        video_id, payload = format_training_record(internal)
        shard[video_id] = payload
    return shard


def iter_metadata_shard_entries(data: Any) -> Iterator[dict]:
    """Yield normalized internal-style records from a metadata shard (list or dict)."""
    if isinstance(data, dict):
        for video_id, payload in data.items():
            yield {
                "platform": "youtube",
                "video_id": video_id,
                **payload,
            }
        return
    if isinstance(data, list):
        for record in data:
            yield record


def load_metadata_records(root: Path) -> Dict[str, dict]:
    records: Dict[str, dict] = {}
    metadata_root = root / "shards" / "metadata"
    if not metadata_root.exists():
        return records
    for path in sorted(metadata_root.glob("**/part_*.json")):
        if path.name.endswith(".tmp"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in iter_metadata_shard_entries(data):
            platform = record.get("platform") or "youtube"
            video_id = record.get("video_id")
            if not video_id:
                continue
            key = f"{platform}:{video_id}"
            records[key] = record
    return records
