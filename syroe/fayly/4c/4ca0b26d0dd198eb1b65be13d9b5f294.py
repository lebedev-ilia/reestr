from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict

from fetcher.dataset_collector.collector import DatasetCollector
from fetcher.dataset_collector.metrics import (
    start_metrics_server,
    update_gauges,
    update_inventory_gauges,
    update_run_distribution_gauges,
)
from fetcher.dataset_collector.inventory import rebuild_inventory_from_disk, refresh_summary
from fetcher.dataset_collector.progress import ProgressReporter
from fetcher.dataset_collector.status_report import build_status_report
from fetcher.dataset_collector.config import load_campaign_config, write_campaign_template
from fetcher.dataset_collector.cookies import CookieRotator
from fetcher.dataset_collector.discovery.base import DiscoveryAdapter
from fetcher.dataset_collector.discovery.instagram import InstagramDiscoveryAdapter
from fetcher.dataset_collector.discovery.rutube import RutubeDiscoveryAdapter
from fetcher.dataset_collector.discovery.tiktok import TikTokDiscoveryAdapter
from fetcher.dataset_collector.discovery.twitch import TwitchDiscoveryAdapter
from fetcher.dataset_collector.discovery.youtube import YouTubeDiscoveryAdapter, YouTubeKeyPool
from fetcher.dataset_collector.downloads import run_download_queue
from fetcher.dataset_collector.hf_queues import (
    run_hf_enrich_upload_queue,
    run_hf_shard_upload_queue,
    run_hf_video_upload_queue,
    scan_enrich_files_for_hf_upload,
    scan_downloaded_videos_for_hf_upload,
    scan_shards_for_hf_upload,
)
from fetcher.dataset_collector.metadata_enrichment import (
    run_metadata_enrich_queue,
    scan_shards_for_enrichment,
)
from fetcher.dataset_collector.export import export_legacy_json, validate_export
from fetcher.dataset_collector.hf_progress import (
    discover_week_allows_run,
    discover_week_complete_message,
    push_hf_progress,
    register_discover_daily_session,
    restore_hf_progress_on_startup,
)
from fetcher.dataset_collector.legacy_import import import_seen_ids
from fetcher.dataset_collector.proxy import ProxyRotator, configured_proxies
from fetcher.dataset_collector.snapshots import SnapshotRunner, run_snapshot_poll_loop
from fetcher.dataset_collector.state import DatasetState, jsonable
from fetcher.dataset_collector.timing_log import reset_timing_stats
from fetcher.dataset_collector.hf_upload import upload_paths
from fetcher.config import settings


def _hf_progress_role_for_command(args: argparse.Namespace) -> str | None:
    func = getattr(args, "func", None)
    name = getattr(func, "__name__", "") or ""
    if name == "command_discover":
        return "discover"
    if name == "command_download":
        return "download"
    if name in {"command_snapshot", "command_snapshot_poll"}:
        return "snapshot"
    if name in {
        "command_upload_hf_shards",
        "command_upload_hf_videos",
        "command_upload_hf_enrich",
        "command_run_workers",
        "command_enrich_metadata",
    }:
        return "workers"
    return "workers"


def _pull_hf_progress(state: DatasetState, config, args: argparse.Namespace) -> None:
    if getattr(args, "skip_hf_progress_pull", False):
        return
    restore_hf_progress_on_startup(state, config, role=_hf_progress_role_for_command(args))


def _push_hf_progress(state: DatasetState, config, args: argparse.Namespace) -> None:
    if getattr(args, "skip_hf_progress_push", False):
        return
    try:
        result = push_hf_progress(state, config, role=_hf_progress_role_for_command(args))
        if result.get("uploaded"):
            print(
                f"[hf-progress] выгружено {result['uploaded']} файлов → {result.get('repo')}",
                flush=True,
            )
    except Exception as exc:
        print(f"[hf-progress] WARN: upload failed: {exc}", flush=True)


def load_youtube_keys(path: str | None) -> list[str]:
    if path:
        keys_path = Path(path)
        raw = keys_path.read_text(encoding="utf-8")
        if keys_path.suffix.lower() == ".json":
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item) for item in data if item]
            return [str(item) for item in data.get("keys", []) if item]
        keys = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            keys.extend([token.strip() for token in line.replace(",", " ").split() if token.strip()])
        return list(dict.fromkeys(keys))
    env_value = os.getenv("FETCHER_YOUTUBE_DATA_API_KEYS") or os.getenv("FETCHER_YOUTUBE_DATA_API_KEY")
    return [item.strip() for item in (env_value or "").split(",") if item.strip()]


def build_adapters(
    state: DatasetState,
    args: argparse.Namespace,
    *,
    key_pool: YouTubeKeyPool | None = None,
) -> Dict[str, DiscoveryAdapter]:
    adapters: Dict[str, DiscoveryAdapter] = {}
    config = getattr(args, "_campaign_config", None)
    cookie_rotator = CookieRotator.from_config(config) if config is not None else None
    if key_pool is None:
        keys_path = getattr(args, "youtube_keys", None) or (config.youtube_keys_file if config else None)
        youtube_keys = load_youtube_keys(keys_path)
        if youtube_keys:
            discovery_proxies = configured_proxies(config=config, download_only=False)
            key_pool = YouTubeKeyPool(
                youtube_keys,
                state_path=state.api_keys_path,
                proxy_rotator=ProxyRotator(proxies=discovery_proxies) if discovery_proxies else None,
            )
    if key_pool is not None:
        adapters["youtube"] = YouTubeDiscoveryAdapter(key_pool)
    if getattr(args, "enable_tiktok", False):
        adapters["tiktok"] = TikTokDiscoveryAdapter()
    if getattr(args, "enable_twitch", False):
        adapters["twitch"] = TwitchDiscoveryAdapter()
    if getattr(args, "enable_rutube", False):
        adapters["rutube"] = RutubeDiscoveryAdapter()
    if getattr(args, "enable_instagram", False):
        adapters["instagram"] = InstagramDiscoveryAdapter()
    return adapters


def command_init(args: argparse.Namespace) -> None:
    path = write_campaign_template(args.config, overwrite=args.force)
    print(f"Wrote campaign template: {path}")


def _youtube_key_pool(state: DatasetState, args: argparse.Namespace):
    config = args._campaign_config
    keys_path = getattr(args, "youtube_keys", None) or (config.youtube_keys_file if config else None)
    youtube_keys = load_youtube_keys(keys_path)
    if not youtube_keys:
        return None
    discovery_proxies = configured_proxies(config=config, download_only=False)
    return YouTubeKeyPool(
        youtube_keys,
        state_path=state.api_keys_path,
        proxy_rotator=ProxyRotator(proxies=discovery_proxies) if discovery_proxies else None,
    )


def command_discover(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    args._campaign_config = config
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
        print(f"Prometheus metrics: http://127.0.0.1:{args.metrics_port}/metrics")
    if config.balancer_config and config.balancer_config.enabled:
        fields = ", ".join(
            name
            for name, field_config in config.balancer_config.fields.items()
            if field_config.coefficient > 0
        )
        print(f"Dataset balancer: enabled ({config.balancer_config_file}); fields={fields}")
    state = DatasetState(config)
    state.initialize()
    _pull_hf_progress(state, config, args)
    if config.discover_week_days and not discover_week_allows_run(state, config):
        print(discover_week_complete_message(config), flush=True)
        return
    register_discover_daily_session(state, config)
    if args.metrics_port:
        update_run_distribution_gauges(state.root)
    if args.reset_checkpoint:
        state.clear_checkpoint()
    state.start_session()
    reset_timing_stats()
    key_pool = _youtube_key_pool(state, args)
    progress = ProgressReporter(config, state, key_pool=key_pool)
    update_gauges(progress.snapshot())
    collector = DatasetCollector(
        config,
        state,
        build_adapters(state, args, key_pool=key_pool),
        progress=progress,
    )
    categories = [args.category] if args.category else [category.name for category in config.categories]
    try:
        total = collector.discover_campaign(categories, limit=args.limit)
    except Exception as exc:
        state.flush_all_pending(shard_size=config.shard_size)
        if config.hf_upload_enabled:
            scan_shards_for_hf_upload(state, category=args.category)
            run_hf_shard_upload_queue(state, config, category=args.category)
        checkpoint = state.load_checkpoint()
        _push_hf_progress(state, config, args)
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "checkpoint": jsonable(checkpoint.dict()) if checkpoint else None,
                },
                ensure_ascii=False,
            )
        )
        raise
    if config.hf_upload_enabled:
        scan_shards_for_hf_upload(state, category=args.category)
        run_hf_shard_upload_queue(state, config, category=args.category)
    _push_hf_progress(state, config, args)
    print(json.dumps(total, ensure_ascii=False))


def command_snapshot(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    args._campaign_config = config
    state = DatasetState(config)
    state.initialize()
    _pull_hf_progress(state, config, args)
    key_pool = _youtube_key_pool(state, args)
    runner = SnapshotRunner(
        state,
        build_adapters(state, args, key_pool=key_pool),
        comments_limit=config.comments_per_snapshot,
    )
    result = runner.collect_due(snapshot_index=args.snapshot_index, limit=args.limit)
    _push_hf_progress(state, config, args)
    print(json.dumps({"snapshots": len(result)}, ensure_ascii=False))


def command_snapshot_poll(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    args._campaign_config = config
    state = DatasetState(config)
    state.initialize()
    _pull_hf_progress(state, config, args)
    key_pool = _youtube_key_pool(state, args)
    runner = SnapshotRunner(
        state,
        build_adapters(state, args, key_pool=key_pool),
        comments_limit=config.comments_per_snapshot,
    )
    totals = run_snapshot_poll_loop(
        runner,
        state,
        config,
        poll_interval_seconds=args.poll_interval_seconds,
        verbose=not getattr(args, "quiet", False),
    )
    _push_hf_progress(state, config, args)
    print(json.dumps(totals, ensure_ascii=False))


def _maybe_refresh_inventory_metrics(state: DatasetState, args: argparse.Namespace) -> None:
    if getattr(args, "metrics_port", None):
        summary = refresh_summary(state)
        update_inventory_gauges(summary)


def command_download(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    _pull_hf_progress(state, config, args)
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
    queue_path = state.download_dir / "queue.jsonl"
    result = run_download_queue(
        state,
        config,
        queue_path,
        limit=args.limit,
        cookie_rotator=CookieRotator.from_config(config),
    )
    _maybe_refresh_inventory_metrics(state, args)
    _push_hf_progress(state, config, args)
    print(json.dumps(result, ensure_ascii=False))


def command_upload_hf_shards(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    if args.scan_shards:
        queued = scan_shards_for_hf_upload(state, category=args.category)
        print(json.dumps({"queued_from_shards": queued}, ensure_ascii=False))
        if args.scan_only:
            return
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
    result = run_hf_shard_upload_queue(
        state,
        config,
        category=args.category,
        limit=args.limit,
        repo_id=args.repo_id,
    )
    _maybe_refresh_inventory_metrics(state, args)
    print(json.dumps(result, ensure_ascii=False))


def command_upload_hf_videos(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    if args.scan_downloads:
        queued = scan_downloaded_videos_for_hf_upload(state, category=args.category)
        print(json.dumps({"queued_from_downloads": queued}, ensure_ascii=False))
        if args.scan_only:
            return
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
    result = run_hf_video_upload_queue(
        state,
        config,
        category=args.category,
        limit=args.limit,
        repo_id=args.repo_id,
    )
    _maybe_refresh_inventory_metrics(state, args)
    print(json.dumps(result, ensure_ascii=False))


def command_upload_hf_enrich(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    if args.scan_enrich:
        queued = scan_enrich_files_for_hf_upload(state, category=args.category)
        print(json.dumps({"queued_from_enrich": queued}, ensure_ascii=False))
        if args.scan_only:
            return
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
    result = run_hf_enrich_upload_queue(
        state,
        config,
        category=args.category,
        limit=args.limit,
        repo_id=args.repo_id,
    )
    _maybe_refresh_inventory_metrics(state, args)
    print(json.dumps(result, ensure_ascii=False))


def command_enrich_metadata(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    _pull_hf_progress(state, config, args)
    if args.compact_shards:
        from fetcher.dataset_collector.metadata_enrichment import compact_metadata_shards

        compact_stats = compact_metadata_shards(state, category=args.category)
        print(json.dumps({"compact": compact_stats}, ensure_ascii=False))
        if args.compact_only:
            return
    if args.scan_shards:
        queued = scan_shards_for_enrichment(state, category=args.category)
        print(json.dumps({"queued_from_shards": queued}, ensure_ascii=False))
        if args.scan_only:
            return
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
    result = run_metadata_enrich_queue(
        state,
        config,
        category=args.category,
        limit=args.limit,
        cookie_rotator=CookieRotator.from_config(config),
    )
    _maybe_refresh_inventory_metrics(state, args)
    _push_hf_progress(state, config, args)
    print(json.dumps(result, ensure_ascii=False))


def command_export(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    result = export_legacy_json(config.output_dir, args.output, split_count=args.split_count)
    print(json.dumps(result, ensure_ascii=False))


def command_status(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    args._campaign_config = config
    state = DatasetState(config)
    state.initialize()
    key_pool = _youtube_key_pool(state, args)
    report = build_status_report(config, state, key_pool=key_pool)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def command_inventory_rebuild(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    result = rebuild_inventory_from_disk(state, category=args.category)
    if args.metrics_port:
        start_metrics_server(args.metrics_port)
        update_inventory_gauges(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def command_validate(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    print(json.dumps(validate_export(config.output_dir, required_snapshots=args.required_snapshots), ensure_ascii=False))


def command_import_seen(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    state = DatasetState(config)
    state.initialize()
    imported = import_seen_ids(state, args.input, platform=args.platform, category=args.category)
    print(json.dumps({"imported": imported}, ensure_ascii=False))


def command_upload_hf(args: argparse.Namespace) -> None:
    config = load_campaign_config(args.config)
    paths = [Path(path) for path in args.paths]
    print(json.dumps(upload_paths(config, paths, repo_id=args.repo_id), ensure_ascii=False))


def command_run_workers(args: argparse.Namespace) -> None:
    from fetcher.dataset_collector.config import load_campaign_config
    from fetcher.dataset_collector.run_workers import run_all_workers

    config = load_campaign_config(args.config)
    log_dir = Path(args.log_dir) if args.log_dir else Path(config.output_dir) / "logs" / "workers"
    worker_kinds = None
    if getattr(args, "worker_kinds", None):
        worker_kinds = [part.strip() for part in args.worker_kinds.split(",") if part.strip()]

    run_all_workers(
        config_path=args.config,
        category=args.category,
        log_dir=log_dir,
        interval_sec=args.interval,
        metrics_port=args.metrics_port,
        with_discover=args.with_discover,
        once=args.once,
        lease_name=args.lease_name,
        lease_owner=args.lease_owner,
        lease_ttl_sec=args.lease_ttl_sec,
        worker_kinds=worker_kinds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m fetcher.dataset_collector.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-campaign")
    init_cmd.add_argument("config")
    init_cmd.add_argument("--force", action="store_true")
    init_cmd.set_defaults(func=command_init)

    def add_common(command: argparse.ArgumentParser) -> None:
        command.add_argument("config")
        command.add_argument("--youtube-keys")
        command.add_argument("--enable-tiktok", action="store_true")
        command.add_argument("--enable-twitch", action="store_true")
        command.add_argument("--enable-rutube", action="store_true")
        command.add_argument("--enable-instagram", action="store_true")
        command.add_argument(
            "--skip-hf-progress-pull",
            action="store_true",
            help="Skip downloading progress bundle from HF at startup.",
        )
        command.add_argument(
            "--skip-hf-progress-push",
            action="store_true",
            help="Skip uploading progress bundle to HF at end.",
        )

    discover = sub.add_parser("discover")
    add_common(discover)
    discover.add_argument("--category")
    discover.add_argument("--limit", type=int)
    discover.add_argument("--metrics-port", type=int, default=None, help="Prometheus /metrics port for live Grafana.")
    discover.add_argument("--reset-checkpoint", action="store_true", help="Ignore saved discovery checkpoint.")
    discover.set_defaults(func=command_discover)

    snapshot = sub.add_parser("snapshot")
    add_common(snapshot)
    snapshot.add_argument("--snapshot-index", type=int, required=True)
    snapshot.add_argument("--limit", type=int)
    snapshot.set_defaults(func=command_snapshot)

    snapshot_poll = sub.add_parser(
        "snapshot-poll",
        help="Collect follow-up snapshots per video when each due_at elapses (never early).",
    )
    add_common(snapshot_poll)
    snapshot_poll.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=30,
        help="Fallback sleep when due times are in the past but collection yielded nothing.",
    )
    snapshot_poll.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-pass snapshot-poll status logs.",
    )
    snapshot_poll.set_defaults(func=command_snapshot_poll)

    download = sub.add_parser("download", help="Download mp4 files locally (queue 1).")
    download.add_argument("config")
    download.add_argument("--limit", type=int, help="Max videos to download in this run.")
    download.add_argument("--metrics-port", type=int, default=None)
    download.set_defaults(func=command_download)

    upload_hf_shards = sub.add_parser(
        "upload-hf-shards",
        help="Upload metadata shards to Hugging Face dataset repo.",
    )
    upload_hf_shards.add_argument("config")
    upload_hf_shards.add_argument("--category")
    upload_hf_shards.add_argument("--limit", type=int)
    upload_hf_shards.add_argument("--repo-id", help="Override hf_shards_repo_id / hf_repo_id.")
    upload_hf_shards.add_argument(
        "--scan-shards",
        action="store_true",
        help="Enqueue all local metadata shards not yet uploaded.",
    )
    upload_hf_shards.add_argument(
        "--scan-only",
        action="store_true",
        help="With --scan-shards, only build the queue.",
    )
    upload_hf_shards.add_argument("--metrics-port", type=int, default=None)
    upload_hf_shards.set_defaults(func=command_upload_hf_shards)

    upload_hf_videos = sub.add_parser(
        "upload-hf-videos",
        help="Upload downloaded mp4 files to Hugging Face dataset repo.",
    )
    upload_hf_videos.add_argument("config")
    upload_hf_videos.add_argument("--category")
    upload_hf_videos.add_argument("--limit", type=int)
    upload_hf_videos.add_argument("--repo-id", help="Override hf_videos_repo_id / hf_repo_id.")
    upload_hf_videos.add_argument(
        "--scan-downloads",
        action="store_true",
        help="Enqueue files from downloads/videos/ not yet uploaded.",
    )
    upload_hf_videos.add_argument(
        "--scan-only",
        action="store_true",
        help="With --scan-downloads, only build the queue.",
    )
    upload_hf_videos.add_argument("--metrics-port", type=int, default=None)
    upload_hf_videos.set_defaults(func=command_upload_hf_videos)

    upload_hf_enrich = sub.add_parser(
        "upload-hf-enrich",
        help="Upload yt-dlp enrich payloads to Hugging Face dataset repo.",
    )
    upload_hf_enrich.add_argument("config")
    upload_hf_enrich.add_argument("--category")
    upload_hf_enrich.add_argument("--limit", type=int)
    upload_hf_enrich.add_argument("--repo-id", help="Override hf_enrich_repo_id / hf_repo_id.")
    upload_hf_enrich.add_argument(
        "--scan-enrich",
        action="store_true",
        help="Enqueue local enrich JSON files not yet uploaded.",
    )
    upload_hf_enrich.add_argument(
        "--scan-only",
        action="store_true",
        help="With --scan-enrich, only build the queue.",
    )
    upload_hf_enrich.add_argument("--metrics-port", type=int, default=None)
    upload_hf_enrich.set_defaults(func=command_upload_hf_enrich)

    run_workers = sub.add_parser(
        "run-workers",
        help="Run long-lived queue workers (discover is separate by default).",
    )
    run_workers.add_argument("config")
    run_workers.add_argument("--category", default=None, help="Optional discover-only category filter.")
    run_workers.add_argument("--interval", type=int, default=120, help="Idle poll interval (sec) when queue is empty; active work is not interrupted.")
    run_workers.add_argument("--log-dir", type=Path, default=None)
    run_workers.add_argument("--metrics-port", type=int, default=9095)
    run_workers.add_argument("--with-discover", action="store_true", help="Also run discover; normally run discover separately.")
    run_workers.add_argument("--once", action="store_true", help="One pass per queue worker, then exit.")
    run_workers.add_argument("--lease-name", help="Optional shared-state worker lease name for multi-Colab runs.")
    run_workers.add_argument("--lease-owner", help="Optional owner label for --lease-name.")
    run_workers.add_argument("--lease-ttl-sec", type=int, default=600)
    run_workers.add_argument(
        "--worker-kinds",
        help="Comma-separated: download, enrich-metadata, upload-hf-shards, upload-hf-videos, upload-hf-enrich.",
    )
    run_workers.set_defaults(func=command_run_workers)

    enrich = sub.add_parser(
        "enrich-metadata",
        help="Second queue: fill metadata via yt-dlp (formats, thumbnails_ytdlp, subtitles, …).",
    )
    enrich.add_argument("config")
    enrich.add_argument("--category", help="Only process this category.")
    enrich.add_argument("--limit", type=int, help="Max videos to enrich in this run.")
    enrich.add_argument(
        "--scan-shards",
        action="store_true",
        help="Enqueue videos from existing metadata shards missing yt-dlp fields.",
    )
    enrich.add_argument(
        "--scan-only",
        action="store_true",
        help="With --scan-shards, only build the queue without running yt-dlp.",
    )
    enrich.add_argument(
        "--compact-shards",
        action="store_true",
        help="Strip bloated caption URLs from metadata shards (ru/en ext list only).",
    )
    enrich.add_argument(
        "--compact-only",
        action="store_true",
        help="With --compact-shards, only compact without running yt-dlp.",
    )
    enrich.add_argument("--metrics-port", type=int, default=None)
    enrich.set_defaults(func=command_enrich_metadata)

    inventory_rebuild = sub.add_parser(
        "inventory-rebuild",
        help="Rebuild shard/video inventory index from local metadata shards.",
    )
    inventory_rebuild.add_argument("config")
    inventory_rebuild.add_argument("--category", help="Only rescan this category.")
    inventory_rebuild.add_argument("--metrics-port", type=int, default=None)
    inventory_rebuild.set_defaults(func=command_inventory_rebuild)

    export = sub.add_parser("export")
    export.add_argument("config")
    export.add_argument("output")
    export.add_argument("--split-count", type=int, default=20)
    export.set_defaults(func=command_export)

    status = sub.add_parser("status")
    status.add_argument("config")
    status.set_defaults(func=command_status)

    validate = sub.add_parser("validate")
    validate.add_argument("config")
    validate.add_argument("--required-snapshots", type=int, default=1)
    validate.set_defaults(func=command_validate)

    import_seen = sub.add_parser("import-seen")
    import_seen.add_argument("config")
    import_seen.add_argument("input")
    import_seen.add_argument("--platform", default="youtube")
    import_seen.add_argument("--category", default="legacy")
    import_seen.set_defaults(func=command_import_seen)

    upload_hf = sub.add_parser("upload-hf")
    upload_hf.add_argument("config")
    upload_hf.add_argument("paths", nargs="+")
    upload_hf.add_argument("--repo-id")
    upload_hf.set_defaults(func=command_upload_hf)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
