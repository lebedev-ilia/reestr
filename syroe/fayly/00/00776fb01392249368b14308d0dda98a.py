from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yt_dlp
from yt_dlp.utils import DownloadError

from fetcher.dataset_collector.caption_text import fetch_caption_texts_from_info
from fetcher.dataset_collector.cookies import CookieRotator, apply_cookiefile
from fetcher.dataset_collector.config import merged_filters
from fetcher.dataset_collector.filters import VideoFilter
from fetcher.dataset_collector.proxy import ProxyRotator, configured_proxies
from fetcher.dataset_collector.queue_retries import (
    load_dead_letter_keys,
    queue_item_key,
    record_queue_failure,
)
from fetcher.dataset_collector.schemas import CampaignConfig
from fetcher.dataset_collector.state import DatasetState, atomic_write_json, iter_jsonl
from fetcher.dataset_collector.training_format import (
    compact_training_metadata,
    extract_best_enrich_formats,
    extract_best_ytdlp_thumbnails,
    metadata_captions_are_bloated,
    training_entry_needs_ytdlp_enrichment,
)
from fetcher.dataset_collector.worker_shutdown import should_stop
from fetcher.dataset_collector.worker_logging import (
    count_jsonl_lines,
    log_kv_block,
    log_pass_footer,
    log_pass_header,
    worker_log,
)
from fetcher.dataset_collector.ytdlp_logging import YtdlpEnrichLogger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enriched_marker() -> dict[str, str]:
    return {
        "at": _utcnow().isoformat().replace("+00:00", "Z"),
        "source": "yt_dlp",
    }


def iter_metadata_enrich_queue(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def fetch_ytdlp_info(
    url: str,
    *,
    cookie_rotator: CookieRotator | None,
    proxy_rotator: ProxyRotator | None,
) -> dict[str, Any] | None:
    proxy = proxy_rotator.next() if proxy_rotator else None
    ydl_opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignore_no_formats_error": True,
        # Video file is not downloaded; caption text is fetched from track URLs below.
        "writesubtitles": False,
        "writeautomaticsub": False,
        "subtitleslangs": ["ru", "en"],
        "logger": YtdlpEnrichLogger(),
    }
    if proxy:
        ydl_opts["proxy"] = proxy
    apply_cookiefile(ydl_opts, cookie_rotator)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                manual, auto = fetch_caption_texts_from_info(info, urlopen=ydl.urlopen)
                info["_caption_texts_manual"] = manual
                info["_caption_texts_auto"] = auto
        if proxy_rotator and proxy:
            proxy_rotator.record_success(proxy)
        if not info:
            return None
        return info
    except DownloadError as exc:
        if proxy_rotator and proxy:
            proxy_rotator.record_failure(proxy, exc)
        print(f"[enrich] yt-dlp failed {url}: {exc}", file=sys.stderr, flush=True)
        return None
    except Exception as exc:
        if proxy_rotator and proxy:
            proxy_rotator.record_failure(proxy, exc)
        print(f"[enrich] yt-dlp error {url}: {exc}", file=sys.stderr, flush=True)
        return None


def build_enrich_entry(
    *,
    entry: dict,
    info: dict,
    video_id: str,
    category: str,
    source_shard: str,
    rejected_reason: str | None = None,
) -> dict:
    from fetcher.dataset_collector.caption_text import build_caption_metadata

    subtitles = build_caption_metadata(
        info.get("subtitles"),
        info.get("_caption_texts_manual") or {},
    )
    automatic_captions = build_caption_metadata(
        info.get("automatic_captions"),
        info.get("_caption_texts_auto") or {},
    )
    return {
        "video_id": video_id,
        "source_shard": source_shard,
        "thumbnails_ytdlp": extract_best_ytdlp_thumbnails(info, limit=2),
        "formats": extract_best_enrich_formats(info, download_cap_height=1080),
        "subtitles": subtitles,
        "automatic_captions": automatic_captions,
        "_enriched": _enriched_marker(),
        "rejected": rejected_reason is not None,
        "rejected_reason": rejected_reason,
    }


def write_enrich_entry(
    state: DatasetState,
    *,
    category: str,
    video_id: str,
    payload: dict,
) -> Path:
    return state.append_enrich_record(category=category, video_id=video_id, payload=payload)


def _resolve_category_filters(config: CampaignConfig, category: str) -> dict:
    for cat in config.categories:
        if cat.name == category:
            return merged_filters(config, cat)
    return dict(config.default_filters or {})




def compact_metadata_shards(
    state: DatasetState,
    *,
    category: str | None = None,
) -> dict[str, int]:
    """Rewrite shard metadata to drop bloated caption URLs (ru/en ext list only)."""
    metadata_root = state.shards_dir / "metadata"
    if not metadata_root.exists():
        return {"shards": 0, "entries": 0, "compacted": 0}

    pattern = f"category={category}/part_*.json" if category else "**/part_*.json"
    stats = {"shards": 0, "entries": 0, "compacted": 0}
    for shard_path in sorted(metadata_root.glob(pattern)):
        if shard_path.name.endswith(".tmp"):
            continue
        data = json.loads(shard_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        stats["shards"] += 1
        changed = False
        for video_id, entry in data.items():
            stats["entries"] += 1
            metadata = entry.get("metadata") or {}
            if not metadata_captions_are_bloated(metadata):
                continue
            entry["metadata"] = compact_training_metadata(metadata)
            changed = True
            stats["compacted"] += 1
        if changed:
            atomic_write_json(shard_path, data)
            worker_log("enrich", f"compacted captions in {shard_path.relative_to(state.root)}")
    return stats


def enrich_shard_video(
    state: DatasetState,
    config: CampaignConfig,
    *,
    shard_relpath: str,
    video_id: str,
    url: str,
    category: str,
    cookie_rotator: CookieRotator | None,
    proxy_rotator: ProxyRotator | None,
    shard_cache: dict[str, dict] | None = None,
) -> str:
    """Return 'ok', 'rejected', or 'failed'.

    shard_cache: optional per-pass dict keyed by shard_relpath → shard data.
    Avoids re-reading the same shard file for every video it contains.
    """
    shard_path = state.root / shard_relpath
    if shard_cache is not None:
        if shard_relpath not in shard_cache:
            if not shard_path.exists():
                worker_log("enrich", f"shard missing: {shard_relpath} (discover may not have flushed yet)")
                return "failed"
            try:
                raw = json.loads(shard_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                worker_log("enrich", f"shard read error: {shard_relpath}")
                return "failed"
            if not isinstance(raw, dict):
                worker_log("enrich", f"unsupported shard format: {shard_relpath}")
                return "failed"
            shard_cache[shard_relpath] = raw
        data = shard_cache[shard_relpath]
    else:
        if not shard_path.exists():
            worker_log("enrich", f"shard missing: {shard_relpath} (discover may not have flushed yet)")
            return "failed"
        data = json.loads(shard_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            worker_log("enrich", f"unsupported shard format: {shard_relpath}")
            return "failed"

    entry = data.get(video_id)
    if entry is None:
        worker_log("enrich", f"video {video_id} not in {shard_relpath}")
        return "failed"

    if not training_entry_needs_ytdlp_enrichment(entry):
        metadata = entry.get("metadata") or {}
        if metadata_captions_are_bloated(metadata):
            entry["metadata"] = compact_training_metadata(metadata)
            atomic_write_json(shard_path, data)
            worker_log("enrich", f"compact {video_id} captions in {shard_relpath}")
        else:
            worker_log("enrich", f"skip {video_id}: metadata already has yt-dlp fields")
        return "ok"

    worker_log("enrich", f"yt-dlp fetch {video_id}")
    started_at = time.perf_counter()
    info = fetch_ytdlp_info(url, cookie_rotator=cookie_rotator, proxy_rotator=proxy_rotator)
    if info is None:
        worker_log("enrich", f"FAIL {video_id}: yt-dlp returned no info")
        return "failed"

    rules = _resolve_category_filters(config, category)
    decision = VideoFilter(rules).decide_post_enrich(
        info=info,
        metadata=entry.get("metadata") or {},
    )
    if not decision.accepted:
        reason = decision.reason or "post_enrich_rejected"
        payload = build_enrich_entry(
            entry=entry,
            info=info,
            video_id=video_id,
            category=category,
            source_shard=shard_relpath,
            rejected_reason=reason,
        )
        enrich_path = write_enrich_entry(state, category=category, video_id=video_id, payload=payload)
        state.record_performance_event(
            "enrich",
            {
                "platform": "youtube",
                "video_id": video_id,
                "category": category,
                "seconds": round(time.perf_counter() - started_at, 3),
                "result": "rejected",
            },
        )
        worker_log("enrich", f"REJECT {video_id}: {reason} (enrich payload will be queued for HF on flush)")
        return "rejected"

    payload = build_enrich_entry(
        entry=entry,
        info=info,
        video_id=video_id,
        category=category,
        source_shard=shard_relpath,
    )
    enrich_path = write_enrich_entry(state, category=category, video_id=video_id, payload=payload)
    state.record_performance_event(
        "enrich",
        {
            "platform": "youtube",
            "video_id": video_id,
            "category": category,
            "seconds": round(time.perf_counter() - started_at, 3),
            "result": "ok",
        },
    )
    formats = payload.get("formats") or []
    caption_langs = sorted(
        {
            lang
            for block in (payload.get("subtitles") or {}, payload.get("automatic_captions") or {})
            for lang in block.keys()
        }
    )
    duration = info.get("duration")
    title = str(info.get("title") or "")[:60]
    worker_log(
        "enrich",
        f"OK {video_id} category={category} duration={duration}s formats={len(formats)} "
        f"captions={','.join(caption_langs) or 'none'} title={title!r} "
        f"-> {enrich_path.relative_to(state.root)}",
    )
    return "ok"


def scan_shards_for_enrichment(
    state: DatasetState,
    *,
    category: str | None = None,
    done_keys: set[str] | None = None,
) -> int:
    """Enqueue videos from existing metadata shards that still need yt-dlp fields."""
    done_keys = done_keys or state.load_metadata_enrich_done()
    queued_keys = state.load_metadata_enrich_queued()
    queued = 0
    seen: set[str] = set()
    metadata_root = state.shards_dir / "metadata"
    if not metadata_root.exists():
        return 0

    pattern = f"category={category}/part_*.json" if category else "**/part_*.json"
    for shard_path in sorted(metadata_root.glob(pattern)):
        if shard_path.name.endswith(".tmp"):
            continue
        data = json.loads(shard_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        shard_relpath = str(shard_path.relative_to(state.root))
        category_name = category or shard_path.parent.name.replace("category=", "")
        for video_id, entry in data.items():
            if not training_entry_needs_ytdlp_enrichment(entry):
                continue
            key = f"youtube:{video_id}"
            if key in done_keys or key in seen or key in queued_keys:
                continue
            seen.add(key)
            state.enqueue_metadata_enrichment(
                platform="youtube",
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                category=category_name,
                shard_relpath=shard_relpath,
            )
            queued += 1
    return queued


def run_metadata_enrich_queue(
    state: DatasetState,
    config: CampaignConfig,
    *,
    category: str | None = None,
    limit: int | None = None,
    cookie_rotator: CookieRotator | None = None,
) -> dict[str, int]:
    cookie_rotator = cookie_rotator or CookieRotator.from_config(config)
    enrich_proxies = configured_proxies(config=config, download_only=False)
    if enrich_proxies:
        worker_log("enrich", f"proxies: {', '.join(enrich_proxies)}")
    else:
        worker_log("enrich", "proxies: direct (use_proxies_for_discovery=false)")
    proxy_rotator = ProxyRotator(proxies=enrich_proxies) if enrich_proxies else None
    log_pass_header("enrich", "pass start")
    from fetcher.dataset_collector.hf_coordination import WorkerCoordination, coord_enabled

    coord = WorkerCoordination(state, config)
    if coord_enabled(config):
        coord_stats = coord.sync_from_hf("enrich")
        worker_log("enrich", f"coord_sync worker={coord.worker_id} {coord_stats}")

    done_keys = state.load_metadata_enrich_done()
    if coord_enabled(config):
        done_keys |= coord.global_done_keys
    queued_from_shards = scan_shards_for_enrichment(state, category=category, done_keys=done_keys)
    if queued_from_shards:
        worker_log("enrich", f"queued_from_metadata_shards: {queued_from_shards}")

    hf_enrich_done = state.load_hf_enrich_upload_done()
    hf_enrich_queued = state.load_hf_enrich_upload_queued()
    dead_letter_keys = load_dead_letter_keys(state, service="enrich")
    attempt_counts_cache: dict[str, int] = load_attempt_counts(state, service="enrich")
    shard_cache: dict[str, dict] = {}
    queue_path = state.metadata_enrich_queue_path
    queue_lines = count_jsonl_lines(queue_path)

    log_kv_block(
        "enrich",
        [
            ("queue_file", queue_path),
            ("queue_lines", queue_lines),
            ("already_done", len(done_keys)),
            ("already_on_hf", len(hf_enrich_done)),
            ("pending_hf_upload", len(hf_enrich_queued - hf_enrich_done)),
            ("category_filter", category or "all"),
            ("limit_this_pass", limit if limit is not None else "none"),
        ],
    )

    if queue_lines == 0:
        worker_log("enrich", "queue empty - wait until discover writes a metadata shard")
        result = {"enriched": 0, "failed": 0, "skipped": 0, "rejected": 0, "attempted": 0}
        log_pass_footer("enrich", result)
        return result

    results = {"enriched": 0, "failed": 0, "skipped": 0, "rejected": 0}
    skip_reasons: dict[str, int] = {}
    attempted = 0

    for item in iter_metadata_enrich_queue(queue_path):
        if should_stop():
            worker_log("enrich", "shutdown requested - stopping pass")
            break
        if limit is not None and results["enriched"] + results["failed"] + results["rejected"] >= limit:
            worker_log("enrich", f"limit reached ({limit}), stopping this pass")
            break
        platform = item.get("platform") or "youtube"
        if platform != "youtube":
            results["skipped"] += 1
            skip_reasons["unsupported_platform"] = skip_reasons.get("unsupported_platform", 0) + 1
            continue
        if category and item.get("category") != category:
            results["skipped"] += 1
            skip_reasons["other_category"] = skip_reasons.get("other_category", 0) + 1
            continue

        video_id = item.get("video_id")
        url = item.get("url")
        shard_relpath = item.get("shard")
        if not video_id or not url or not shard_relpath:
            results["skipped"] += 1
            skip_reasons["invalid_row"] = skip_reasons.get("invalid_row", 0) + 1
            continue

        key = f"{platform}:{video_id}"
        retry_key = queue_item_key("enrich", item)
        if retry_key in dead_letter_keys:
            results["skipped"] += 1
            skip_reasons["dead_letter"] = skip_reasons.get("dead_letter", 0) + 1
            continue
        if key in done_keys or key in hf_enrich_done:
            results["skipped"] += 1
            skip_reasons["already_done"] = skip_reasons.get("already_done", 0) + 1
            continue
        if key in hf_enrich_queued:
            results["skipped"] += 1
            skip_reasons["pending_hf_upload"] = skip_reasons.get("pending_hf_upload", 0) + 1
            continue
        coord_skip = coord.skip_reason("enrich", key)
        if coord_skip:
            coord.record_skip("enrich", coord_skip)
            results["skipped"] += 1
            skip_reasons[coord_skip] = skip_reasons.get(coord_skip, 0) + 1
            continue
        if not coord.try_claim("enrich", key):
            coord.record_skip("enrich", "coord_claim_busy")
            results["skipped"] += 1
            skip_reasons["coord_claim_busy"] = skip_reasons.get("coord_claim_busy", 0) + 1
            continue

        attempted += 1
        worker_log(
            "enrich",
            f"({attempted}) {video_id} shard={shard_relpath} "
            f"(pass: +{results['enriched']} ok, {results['failed']} fail)",
        )

        ok = enrich_shard_video(
            state,
            config,
            shard_relpath=shard_relpath,
            video_id=video_id,
            url=url,
            category=item.get("category") or category or "unknown",
            cookie_rotator=cookie_rotator,
            proxy_rotator=proxy_rotator,
            shard_cache=shard_cache,
        )
        if ok == "ok":
            coord.mark_done("enrich", key, video_id=video_id)
            hf_enrich_queued.add(key)
            results["enriched"] += 1
        elif ok == "rejected":
            coord.mark_done("enrich", key, video_id=video_id, result="rejected")
            hf_enrich_queued.add(key)
            results["rejected"] += 1
        else:
            results["failed"] += 1
            if record_queue_failure(
                state,
                service="enrich",
                item=item,
                error="yt-dlp enrich failed",
                dead_letter_cache=dead_letter_keys,
                attempt_cache=attempt_counts_cache,
            ):
                dead_letter_keys.add(retry_key)

    if coord_enabled(config):
        coord.flush_coord_uploads("enrich", force=True)

    written_enrich_shards = state.flush_all_enrich_pending()
    if written_enrich_shards:
        worker_log("enrich", f"flushed enrich shards: {len(written_enrich_shards)}")

    if skip_reasons:
        log_kv_block("enrich", [(f"skipped_{k}", v) for k, v in sorted(skip_reasons.items())])
    if attempted == 0 and results["skipped"] > 0:
        worker_log("enrich", "no new items processed - all queue rows already done or filtered")

    from fetcher.dataset_collector.inventory import refresh_summary

    refresh_summary(state)
    results["attempted"] = attempted
    log_pass_footer("enrich", results)
    return results
