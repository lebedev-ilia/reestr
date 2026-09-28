from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List

from fetcher.dataset_collector.worker_shutdown import (
    request_shutdown,
    reset_shutdown,
    should_stop,
)


def _fetcher_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cli_cmd(args: List[str]) -> List[str]:
    return [sys.executable, "-m", "fetcher.dataset_collector.cli", *args]


def _pass_had_work(result: dict) -> bool:
    """True when the queue pass found and attempted at least one actionable item."""
    return int(result.get("attempted") or 0) > 0


def _idle_sleep(idle_interval_sec: int) -> None:
    """Sleep in small slices so SIGINT/SIGTERM can stop the worker promptly."""
    deadline = time.monotonic() + max(idle_interval_sec, 10)
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(min(5, deadline - time.monotonic()))


def _run_queue_pass(
    kind: str,
    *,
    config_path: str,
    category: str | None,
) -> dict:
    from fetcher.dataset_collector.config import load_campaign_config
    from fetcher.dataset_collector.cookies import CookieRotator
    from fetcher.dataset_collector.downloads import run_download_queue
    from fetcher.dataset_collector.hf_queues import (
        run_hf_enrich_upload_queue,
        run_hf_shard_upload_queue,
        run_hf_video_upload_queue,
        scan_enrich_files_for_hf_upload,
    )
    from fetcher.dataset_collector.metadata_enrichment import run_metadata_enrich_queue
    from fetcher.dataset_collector.state import DatasetState

    config = load_campaign_config(config_path)
    state = DatasetState(config)
    state.initialize()
    from fetcher.dataset_collector.hf_progress import pull_hf_progress

    role = "download" if kind == "download" else "enrich" if kind == "enrich-metadata" else "workers"
    pull_hf_progress(state, config, role=role)
    cookie_rotator = CookieRotator.from_config(config)

    if kind == "download":
        queue_path = state.download_dir / "queue.jsonl"
        result = run_download_queue(
            state,
            config,
            queue_path,
            cookie_rotator=cookie_rotator,
        )
    elif kind == "enrich-metadata":
        result = run_metadata_enrich_queue(
            state,
            config,
            category=category,
            cookie_rotator=cookie_rotator,
        )
    elif kind == "upload-hf-shards":
        result = run_hf_shard_upload_queue(state, config, category=category)
    elif kind == "upload-hf-videos":
        result = run_hf_video_upload_queue(state, config, category=category)
    elif kind == "upload-hf-enrich":
        scan_enrich_files_for_hf_upload(state, category=category)
        result = run_hf_enrich_upload_queue(state, config, category=category)
    else:
        raise ValueError(f"unknown queue worker kind: {kind}")

    if _pass_had_work(result):
        from fetcher.dataset_collector.hf_progress import push_hf_progress

        role = "download" if kind == "download" else "enrich" if kind == "enrich-metadata" else "workers"
        try:
            push_hf_progress(state, config, role=role)
        except Exception:
            pass
    return result


def _queue_worker_daemon(
    *,
    name: str,
    kind: str,
    config_path: str,
    category: str | None,
    idle_interval_sec: int,
    log_dir: Path | str,
    once: bool = False,
) -> None:
    """Long-running queue worker: drain pending work, sleep only when idle."""
    log_dir = Path(log_dir)
    log_path = log_dir / f"{name}.log"
    log_dir.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGTERM, lambda *_: request_shutdown())
    signal.signal(signal.SIGINT, lambda *_: request_shutdown())

    if kind == "download":
        from fetcher.dataset_collector.worker_logging import install_sanitized_worker_stderr

        install_sanitized_worker_stderr()

    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            f"=== {name} worker; idle_interval={idle_interval_sec}s "
            f"(sleep only when queue has no pending work) ===\n"
        )
        log.flush()

        while not should_stop():
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log.write(f"\n--- {stamp} ---\n")
            log.flush()

            try:
                result = _run_queue_pass(kind, config_path=config_path, category=category)
            except (KeyboardInterrupt, SystemExit):
                log.write("stopped by signal\n")
                log.flush()
                break
            except Exception as exc:
                log.write(f"ERROR {type(exc).__name__}: {exc}\n")
                log.flush()
                if once or should_stop():
                    break
                _idle_sleep(idle_interval_sec)
                continue

            log.write(json.dumps(result, ensure_ascii=False) + "\n")
            log.flush()
            try:
                from fetcher.dataset_collector.metrics import record_service_pass

                record_service_pass(kind, result)
            except Exception:
                pass

            if once or should_stop():
                break

            if _pass_had_work(result):
                # More items may have been enqueued while we worked — recheck immediately.
                time.sleep(1)
            else:
                _idle_sleep(idle_interval_sec)


def _worker_loop(
    *,
    name: str,
    args: List[str],
    interval_sec: int,
    log_dir: Path | str,
    once: bool = False,
) -> None:
    """Legacy subprocess loop for long commands such as discover."""
    log_dir = Path(log_dir)
    log_path = log_dir / f"{name}.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"=== {name} worker; interval={interval_sec}s ===\n")
        while True:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log.write(f"\n--- {stamp} ---\n")
            log.flush()
            proc = subprocess.run(
                _cli_cmd(args),
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(_fetcher_root()),
            )
            log.write(f"exit_code={proc.returncode}\n")
            log.flush()
            if once:
                break
            _idle_sleep(interval_sec)


def _start_inventory_metrics_thread(
    *,
    config_path: str,
    metrics_port: int,
    refresh_sec: int = 30,
) -> None:
    from fetcher.dataset_collector.config import load_campaign_config
    from fetcher.dataset_collector.inventory import refresh_summary
    from fetcher.dataset_collector.metrics import (
        start_metrics_server,
        update_inventory_gauges,
        update_run_distribution_gauges,
    )
    from fetcher.dataset_collector.state import DatasetState

    config = load_campaign_config(config_path)
    state = DatasetState(config)
    state.initialize()
    start_metrics_server(metrics_port)
    print(f"Prometheus metrics: http://127.0.0.1:{metrics_port}/metrics", flush=True)

    def _loop() -> None:
        while True:
            try:
                summary = refresh_summary(state)
                update_inventory_gauges(summary)
                update_run_distribution_gauges(state.root)
            except Exception as exc:
                print(f"[metrics] inventory refresh failed: {exc}", flush=True)
            time.sleep(max(refresh_sec, 10))

    thread = threading.Thread(target=_loop, name="inventory-metrics", daemon=True)
    thread.start()


QueueWorkerSpec = tuple[str, str, bool]


def _stop_worker_process(proc: subprocess.Popen, *, name: str, grace_sec: float = 5) -> None:
    """SIGTERM then SIGKILL the worker process group (includes ffmpeg children)."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        proc.kill()
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_sec)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    print(f"killed {name} (pid={proc.pid})", flush=True)


DEFAULT_QUEUE_WORKERS: list[QueueWorkerSpec] = [
    ("enrich-metadata", "enrich-metadata", False),
    ("download", "download", False),
    ("upload-hf-shards", "upload-hf-shards", False),
    ("upload-hf-videos", "upload-hf-videos", False),
    ("upload-hf-enrich", "upload-hf-enrich", False),
]


def run_all_workers(
    *,
    config_path: str,
    category: str | None,
    log_dir: Path,
    interval_sec: int = 120,
    metrics_port: int | None = 9095,
    with_discover: bool = False,
    once: bool = False,
    lease_name: str | None = None,
    lease_owner: str | None = None,
    lease_ttl_sec: int = 600,
    worker_kinds: list[str] | None = None,
) -> None:
    """Run long-lived queue services. Discover is opt-in and normally run separately."""
    reset_shutdown()
    log_dir.mkdir(parents=True, exist_ok=True)
    lease_state = None
    lease_stop = threading.Event()
    if lease_name:
        from fetcher.dataset_collector.config import load_campaign_config
        from fetcher.dataset_collector.state import DatasetState
        from fetcher.dataset_collector.worker_leases import (
            acquire_lease,
            heartbeat_lease,
            release_lease,
        )

        cfg = load_campaign_config(config_path)
        lease_state = DatasetState(cfg)
        lease_state.initialize()
        owner = lease_owner or os.getenv("COLAB_RELEASE_TAG") or f"{os.uname().nodename}:{os.getpid()}"
        acquire_lease(
            lease_state,
            lease_name=lease_name,
            owner=owner,
            ttl_seconds=lease_ttl_sec,
            metadata={
                "category": category,
                "with_discover": with_discover,
                "log_dir": str(log_dir),
            },
        )
        print(f"acquired worker lease {lease_name!r} as {owner!r}", flush=True)

        def _lease_heartbeat() -> None:
            while not lease_stop.wait(max(30, lease_ttl_sec // 3)):
                heartbeat_lease(
                    lease_state,
                    lease_name=lease_name,
                    owner=owner,
                    ttl_seconds=lease_ttl_sec,
                )

        threading.Thread(target=_lease_heartbeat, name="worker-lease-heartbeat", daemon=True).start()
    if metrics_port:
        _start_inventory_metrics_thread(config_path=config_path, metrics_port=metrics_port)

    subprocess_workers: list[tuple[str, list[str], bool]] = []
    queue_workers: list[QueueWorkerSpec] = []

    if with_discover:
        discover_args = ["discover", config_path]
        if category:
            discover_args.extend(["--category", category])
        subprocess_workers.append(("discover", discover_args, True))

    kind_filter = {k.strip() for k in worker_kinds} if worker_kinds else None
    for spec in DEFAULT_QUEUE_WORKERS:
        if kind_filter is None or spec[1] in kind_filter:
            queue_workers.append(spec)
    if kind_filter and not queue_workers:
        raise ValueError(f"no queue workers match --worker-kinds {sorted(kind_filter)}")

    processes: list[tuple[str, subprocess.Popen]] = []

    for name, args, run_once in subprocess_workers:
        log_path = log_dir / f"{name}.log"
        log_file = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from fetcher.dataset_collector.run_workers import _worker_loop; "
                    f"_worker_loop(name={name!r}, args={args!r}, interval_sec={interval_sec}, "
                    f"log_dir={str(log_dir)!r}, once={once or run_once})"
                ),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(_fetcher_root()),
            env=os.environ.copy(),
            start_new_session=True,
        )
        processes.append((name, proc))
        print(f"started {name} (pid={proc.pid}) -> {log_path}", flush=True)

    for name, kind, run_once in queue_workers:
        log_path = log_dir / f"{name}.log"
        log_file = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from fetcher.dataset_collector.run_workers import _queue_worker_daemon; "
                    f"_queue_worker_daemon(name={name!r}, kind={kind!r}, "
                    f"config_path={config_path!r}, category={category!r}, "
                    f"idle_interval_sec={interval_sec}, log_dir={str(log_dir)!r}, "
                    f"once={once or run_once})"
                ),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(_fetcher_root()),
            env=os.environ.copy(),
            start_new_session=True,
        )
        processes.append((name, proc))
        print(f"started {name} (pid={proc.pid}) -> {log_path}", flush=True)

    stop_requested = False

    def _stop_all_workers(*_args) -> None:
        nonlocal stop_requested
        if stop_requested:
            print("\nForce killing workers...", flush=True)
            for name, proc in processes:
                _stop_worker_process(proc, name=name, grace_sec=0)
            raise SystemExit(130)
        stop_requested = True
        request_shutdown()
        print("\nStopping workers...", flush=True)
        for name, proc in processes:
            _stop_worker_process(proc, name=name, grace_sec=8)

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _stop_all_workers)
    signal.signal(signal.SIGTERM, _stop_all_workers)

    try:
        while True:
            if all(proc.poll() is not None for _, proc in processes):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        _stop_all_workers()
    finally:
        lease_stop.set()
        if lease_name and lease_state is not None:
            from fetcher.dataset_collector.worker_leases import release_lease

            release_lease(lease_state, lease_name=lease_name, owner=owner)
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv: List[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run long-lived dataset collector services.")
    parser.add_argument("config", help="Path to dataset_campaign.json")
    parser.add_argument("--category", default=None, help="Optional discover-only category filter.")
    parser.add_argument(
        "--interval",
        type=int,
        default=120,
        help="Seconds to wait when a queue worker is idle (does not interrupt active downloads).",
    )
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--metrics-port", type=int, default=9095)
    parser.add_argument("--with-discover", action="store_true", help="Also run discover; normally discover is launched separately.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lease-name", help="Optional shared-state worker lease name for multi-Colab runs.")
    parser.add_argument("--lease-owner", help="Optional owner label for --lease-name.")
    parser.add_argument("--lease-ttl-sec", type=int, default=600)
    parser.add_argument(
        "--worker-kinds",
        help="Comma-separated queue workers: download, enrich-metadata, upload-hf-shards, upload-hf-videos, upload-hf-enrich.",
    )
    args = parser.parse_args(argv)

    worker_kinds = None
    if args.worker_kinds:
        worker_kinds = [part.strip() for part in args.worker_kinds.split(",") if part.strip()]

    log_dir = args.log_dir
    if log_dir is None:
        from fetcher.dataset_collector.config import load_campaign_config

        cfg = load_campaign_config(args.config)
        log_dir = Path(cfg.output_dir) / "logs" / "workers"

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


if __name__ == "__main__":
    main()
