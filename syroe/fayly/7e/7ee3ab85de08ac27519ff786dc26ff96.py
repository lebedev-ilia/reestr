from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Allow `python scripts/colab_20k_bootstrap.py` without prior `pip install -e .`
_FETCHER_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_FETCHER_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_FETCHER_PKG_ROOT))

CAMPAIGN_PROFILE_TEMPLATES = {
    "20k": "dataset_campaign_20k.json",
    "100k-monthly": "dataset_campaign_100k_monthly.json",
    "snapshot-smoke": "dataset_campaign_snapshot_smoke.json",
}

HF_REPO_BASE_BY_PROFILE = {
    "20k": "dataset_20k_colab",
    "100k-monthly": "dataset_100k_monthly",
    "snapshot-smoke": "dataset_snapshot_smoke",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fetcher_root() -> Path:
    return _FETCHER_PKG_ROOT


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_parallel_colab_hf_tuning(config: dict, parallel: int) -> None:
    """Larger HF batches when several Colabs share the same repos."""
    if parallel <= 1:
        return
    config["hf_parallel_colab_count"] = parallel
    for key, floor in (
        ("hf_shard_upload_batch_files", 50),
        ("hf_video_upload_batch_files", 100),
        ("hf_enrich_upload_batch_files", 100),
    ):
        current = int(config.get(key) or 25)
        config[key] = max(current, floor)


def _resolve_template_path(args: argparse.Namespace, fetcher_root: Path) -> Path:
    template_name = args.template
    if args.campaign_profile:
        template_name = CAMPAIGN_PROFILE_TEMPLATES.get(args.campaign_profile, template_name)
    return fetcher_root / template_name


def _sanitize_hf_token_env(config: dict) -> None:
    """hf_token_env must be the env var name (HF_TOKEN), not the secret value."""
    env_name = (config.get("hf_token_env") or "HF_TOKEN").strip()
    if env_name.startswith("hf_") and len(env_name) > 20:
        config["hf_token_env"] = "HF_TOKEN"
        print(
            "WARN: hf_token_env in campaign JSON looked like a token; "
            'reset to "HF_TOKEN". Store the secret in Colab Secret HF_TOKEN only.',
            flush=True,
        )


def _progress_cli_flags(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    if getattr(args, "skip_hf_progress_pull", False):
        flags.append("--skip-hf-progress-pull")
    if getattr(args, "skip_hf_progress_push", False):
        flags.append("--skip-hf-progress-push")
    return flags


def _platform_cli_flags(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    for platform in ("tiktok", "twitch", "rutube", "instagram"):
        if getattr(args, f"enable_{platform}", False):
            flags.append(f"--enable-{platform}")
    return flags


def build_runtime_config(args: argparse.Namespace) -> Path:
    fetcher_root = _fetcher_root()
    template_path = _resolve_template_path(args, fetcher_root)
    config = _read_json(template_path)
    output_dir = Path(args.output_dir).expanduser()
    config["output_dir"] = str(output_dir)
    config["name"] = args.run_name
    config["campaign_profile"] = config.get("campaign_profile") or "20k-colab-free-popularity-v1"
    if args.hf_repo_prefix:
        prefix = args.hf_repo_prefix.rstrip("/")
        profile = args.campaign_profile or "20k"
        base = HF_REPO_BASE_BY_PROFILE.get(profile, "dataset_20k_colab")
        if "snapshot_smoke" in template_path.name:
            base = "dataset_snapshot_smoke"
        config["hf_repo_id"] = f"{prefix}/{base}"
        config["hf_shards_repo_id"] = f"{prefix}/{base}_shards"
        config["hf_videos_repo_id"] = f"{prefix}/{base}_videos"
        config["hf_enrich_repo_id"] = f"{prefix}/{base}_enrich"
        # Coordination + progress must use an existing HF dataset repo (shards), not the legacy combined id.
        config["hf_coord_repo_id"] = config["hf_shards_repo_id"]
        config["hf_progress_repo_id"] = config["hf_shards_repo_id"]
    if args.youtube_keys_file:
        config["youtube_keys_file"] = args.youtube_keys_file
    if args.cookie_files_dir:
        config["cookie_files_dir"] = args.cookie_files_dir
    if args.proxies_file:
        config["proxies_file"] = args.proxies_file
    config["use_proxies_for_discovery"] = bool(args.use_discovery_proxies)
    if args.disable_hf_upload:
        config["hf_upload_enabled"] = False
    if args.hf_coord or args.role in ("workers-download", "workers-enrich"):
        config["hf_coord_enabled"] = True
    if args.worker_id:
        config["worker_id"] = args.worker_id
    if args.worker_shard_index is not None:
        config["worker_shard_index"] = args.worker_shard_index
    if args.worker_shard_count is not None:
        config["worker_shard_count"] = args.worker_shard_count
    parallel_colab = args.parallel_colab_count
    if parallel_colab is None and args.worker_shard_count is not None:
        parallel_colab = args.worker_shard_count
    if parallel_colab is not None:
        _apply_parallel_colab_hf_tuning(config, max(int(parallel_colab), 1))
    if getattr(args, "snapshot_sleep_seconds", None) is not None:
        config["snapshot_sleep_seconds"] = args.snapshot_sleep_seconds
        follow_up = getattr(args, "snapshot_follow_up_count", None)
        config["snapshot_follow_up_count"] = follow_up if follow_up is not None else config.get(
            "snapshot_follow_up_count", 3
        )
    overrides_path = getattr(args, "config_overrides_json", None)
    if overrides_path:
        overrides = _read_json(Path(overrides_path))
        if not isinstance(overrides, dict):
            raise SystemExit(f"--config-overrides-json must be a JSON object, got {type(overrides).__name__}")
        config.update(overrides)
    _sanitize_hf_token_env(config)
    runtime_config = output_dir / "runtime_dataset_campaign_20k.json"
    _write_json(runtime_config, config)
    _ensure_hf_token(output_dir=output_dir)
    token_path = output_dir / ".dataset_drive_token.pickle"
    if token_path.is_file():
        os.environ.setdefault("DATASET_DRIVE_TOKEN_PATH", str(token_path))
    return runtime_config


def hf_token_file_path(output_dir: str | Path) -> Path:
    return Path(output_dir).expanduser() / ".hf_token"


def _load_hf_token_from_output_dir(output_dir: str | Path | None) -> bool:
    """Colab: notebook sets os.environ in kernel; bootstrap subprocess reads .hf_token from output_dir."""
    if not output_dir:
        return False
    path = hf_token_file_path(output_dir)
    if not path.is_file():
        return False
    token = path.read_text(encoding="utf-8").strip()
    if token.startswith("hf_") and len(token) > 20:
        os.environ["HF_TOKEN"] = token
        return True
    return False


def _persist_hf_token_for_subprocesses(output_dir: str | Path) -> None:
    token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if not token.startswith("hf_"):
        return
    path = hf_token_file_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _ensure_hf_token(*, output_dir: str | Path | None = None) -> None:
    """Make HF token visible to worker subprocesses (notebook env != shell env on Colab)."""
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if (os.environ.get(name) or "").strip():
            if output_dir:
                _persist_hf_token_for_subprocesses(output_dir)
            return
    if output_dir and _load_hf_token_from_output_dir(output_dir):
        return
    try:
        from google.colab import userdata

        token = (userdata.get("HF_TOKEN") or "").strip()
        if token:
            os.environ["HF_TOKEN"] = token
            if output_dir:
                _persist_hf_token_for_subprocesses(output_dir)
    except Exception:
        pass


def _require_hf_token_for_upload(runtime_config: Path) -> None:
    config = _read_json(runtime_config)
    if not config.get("hf_upload_enabled", True):
        return
    output_dir = config.get("output_dir")
    _load_hf_token_from_output_dir(output_dir)
    _ensure_hf_token(output_dir=output_dir)
    _sanitize_hf_token_env(config)
    env_name = (config.get("hf_token_env") or "HF_TOKEN").strip()
    if env_name.startswith("hf_") and len(env_name) > 20:
        raise SystemExit(
            'runtime config has the token in "hf_token_env"; set "hf_token_env": "HF_TOKEN" '
            "and put the secret in env HF_TOKEN or Colab Secret HF_TOKEN."
        )
    _write_json(runtime_config, config)
    if not (os.environ.get(env_name) or "").strip():
        raise SystemExit(
            f"{env_name} is not set for this shell. Before workers run:\n"
            f'  export {env_name}=hf_...\n'
            "or add Colab Secret HF_TOKEN (bootstrap loads it automatically)."
        )


def _output_dir_from_cmd(cmd: list[str]) -> str | None:
    for arg in cmd:
        if arg.endswith(".json") and Path(arg).is_file():
            return _read_json(Path(arg)).get("output_dir")
    return None


def run_command(cmd: list[str], *, output_dir: str | Path | None = None) -> int:
    if output_dir is None:
        output_dir = _output_dir_from_cmd(cmd)
    _load_hf_token_from_output_dir(output_dir)
    _ensure_hf_token(output_dir=output_dir)
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(_fetcher_root()), env=os.environ.copy())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Colab bootstrap for the 20k dataset collector run.")
    parser.add_argument("--template", default="dataset_campaign_20k.json")
    parser.add_argument(
        "--campaign-profile",
        choices=sorted(CAMPAIGN_PROFILE_TEMPLATES),
        help="Shortcut for campaign JSON template (20k or snapshot-smoke).",
    )
    parser.add_argument("--output-dir", default="/content/drive/MyDrive/dataset_runs/20k-test")
    parser.add_argument("--run-name", default="dataset-20k-colab")
    parser.add_argument(
        "--role",
        choices=[
            "discover",
            "workers",
            "workers-download",
            "workers-enrich",
            "snapshot",
            "snapshot-loop",
            "snapshot-poll",
            "status",
            "inventory-rebuild",
        ],
        default="workers",
    )
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--snapshot-index", type=int)
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--metrics-port", type=int, default=0, help="0 disables Prometheus server for Colab.")
    parser.add_argument("--lease-name", help="Shared lease name for multi-Colab coordination.")
    parser.add_argument("--lease-owner", default=os.getenv("COLAB_RELEASE_TAG") or os.getenv("HOSTNAME"))
    parser.add_argument(
        "--worker-id",
        help="Unique worker id for HF coordination (defaults to COLAB_RELEASE_TAG or hostname).",
    )
    parser.add_argument("--worker-shard-index", type=int, help="0..N-1 slot for static hash sharding across Colabs.")
    parser.add_argument("--worker-shard-count", type=int, help="Number of parallel download/enrich Colabs.")
    parser.add_argument(
        "--parallel-colab-count",
        type=int,
        help=(
            "How many Colab instances share HF repos (you set this). "
            "Splits Hub 128 commits/h per repo. Defaults to --worker-shard-count when omitted."
        ),
    )
    parser.add_argument(
        "--hf-coord",
        action="store_true",
        help="Enable HF coordination (auto for workers-download / workers-enrich roles).",
    )
    parser.add_argument("--hf-repo-prefix", help="HF namespace, e.g. Ilialebedev.")
    parser.add_argument("--youtube-keys-file")
    parser.add_argument("--cookie-files-dir")
    parser.add_argument("--proxies-file")
    parser.add_argument(
        "--use-discovery-proxies",
        action="store_true",
        help="Use configured proxies for YouTube Data API discovery/enrich. Default is direct API calls on Colab.",
    )
    parser.add_argument("--disable-hf-upload", action="store_true")
    parser.add_argument("--enable-tiktok", action="store_true", help="Enable TikTok discovery adapter.")
    parser.add_argument("--enable-twitch", action="store_true", help="Enable Twitch discovery (needs FETCHER_TWITCH_* env).")
    parser.add_argument("--enable-rutube", action="store_true", help="Enable Rutube discovery adapter.")
    parser.add_argument("--enable-instagram", action="store_true", help="Enable Instagram discovery adapter.")
    parser.add_argument(
        "--snapshot-sleep-seconds",
        type=int,
        help="Per-video snapshot interval (written to runtime campaign JSON for discover + snapshot-poll).",
    )
    parser.add_argument(
        "--snapshot-follow-up-count",
        type=int,
        help="Follow-up snapshots after snapshot_0 (default 3). Used with --snapshot-sleep-seconds.",
    )
    parser.add_argument(
        "--snapshot-poll-interval-seconds",
        type=int,
        default=30,
        help="For snapshot-poll: fallback sleep when nothing is due yet.",
    )
    parser.add_argument(
        "--skip-hf-progress-pull",
        action="store_true",
        help="Do not download progress bundle from HF before CLI run.",
    )
    parser.add_argument(
        "--skip-hf-progress-push",
        action="store_true",
        help="Do not upload progress bundle to HF after CLI run.",
    )
    parser.add_argument(
        "--config-overrides-json",
        help="Path to JSON object merged into runtime campaign config (notebook tunables without editing template).",
    )
    parser.add_argument(
        "--worker-kinds",
        help="Comma-separated for role=workers: download, enrich-metadata, upload-hf-shards, upload-hf-videos, upload-hf-enrich.",
    )
    args = parser.parse_args(argv)

    runtime_config = build_runtime_config(args)
    if args.role in {"discover", "workers", "workers-download", "workers-enrich"}:
        _require_hf_token_for_upload(runtime_config)
    py = sys.executable
    base = [py, "-m", "fetcher.dataset_collector.cli"]

    if args.role == "discover":
        cmd = [*base, "discover", str(runtime_config)]
        if args.category:
            cmd += ["--category", args.category]
        if args.limit is not None:
            cmd += ["--limit", str(args.limit)]
        if args.metrics_port:
            cmd += ["--metrics-port", str(args.metrics_port)]
        cmd += _platform_cli_flags(args)
        cmd += _progress_cli_flags(args)
        return run_command(cmd, output_dir=args.output_dir)

    if args.role == "snapshot-poll":
        cmd = [
            *base,
            "snapshot-poll",
            str(runtime_config),
            "--poll-interval-seconds",
            str(args.snapshot_poll_interval_seconds),
        ]
        cmd += _platform_cli_flags(args)
        cmd += _progress_cli_flags(args)
        return run_command(cmd, output_dir=args.output_dir)

    if args.role == "snapshot-loop":
        print(
            "WARN: snapshot-loop uses global sleeps between indices; "
            "prefer --role snapshot-poll for per-video due_at scheduling.",
            flush=True,
        )
        cmd = [
            *base,
            "snapshot-poll",
            str(runtime_config),
            "--poll-interval-seconds",
            str(args.snapshot_poll_interval_seconds),
        ]
        cmd += _platform_cli_flags(args)
        cmd += _progress_cli_flags(args)
        return run_command(cmd, output_dir=args.output_dir)

    if args.role in ("workers", "workers-download", "workers-enrich"):
        cmd = [*base, "run-workers", str(runtime_config), "--interval", str(args.interval)]
        if args.category:
            cmd += ["--category", args.category]
        if args.metrics_port:
            cmd += ["--metrics-port", str(args.metrics_port)]
        else:
            cmd += ["--metrics-port", "0"]
        if args.worker_kinds:
            cmd += ["--worker-kinds", args.worker_kinds]
        elif args.role == "workers-download":
            cmd += ["--worker-kinds", "download,upload-hf-videos"]
        elif args.role == "workers-enrich":
            cmd += ["--worker-kinds", "enrich-metadata,upload-hf-enrich"]
        if args.lease_name:
            cmd += ["--lease-name", args.lease_name]
            if args.lease_owner:
                cmd += ["--lease-owner", args.lease_owner]
        cmd += _progress_cli_flags(args)
        return run_command(cmd, output_dir=args.output_dir)

    if args.role == "snapshot":
        if args.snapshot_index is None:
            raise SystemExit("--snapshot-index is required for role=snapshot")
        cmd = [*base, "snapshot", str(runtime_config), "--snapshot-index", str(args.snapshot_index)]
        if args.limit is not None:
            cmd += ["--limit", str(args.limit)]
        cmd += _platform_cli_flags(args)
        cmd += _progress_cli_flags(args)
        return run_command(cmd, output_dir=args.output_dir)

    if args.role == "inventory-rebuild":
        cmd = [*base, "inventory-rebuild", str(runtime_config)]
        if args.category:
            cmd += ["--category", args.category]
        return run_command(cmd, output_dir=args.output_dir)

    return run_command([*base, "status", str(runtime_config)], output_dir=args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
