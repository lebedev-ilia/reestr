from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from fetcher.dataset_collector.hf_commit_budget import resolve_hf_commit_limits
from fetcher.dataset_collector.schemas import CampaignConfig


class HuggingFaceUploadError(RuntimeError):
    pass

_HF_TOKEN_FALLBACK_ENVS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")


def _looks_like_hf_token(value: str) -> bool:
    return value.startswith("hf_") and len(value) > 20


def resolve_hf_token(config: CampaignConfig) -> str:
    """Read HF token from env. hf_token_env is the variable *name*, not the secret."""
    env_name = (config.hf_token_env or "HF_TOKEN").strip()
    if _looks_like_hf_token(env_name):
        raise HuggingFaceUploadError(
            'campaign config "hf_token_env" must be the environment variable name '
            '(e.g. "HF_TOKEN"), not the token. Fix runtime_dataset_campaign_20k.json and '
            "export HF_TOKEN=hf_... in the same shell that starts workers."
        )

    candidates: list[str] = []
    for name in (env_name, *_HF_TOKEN_FALLBACK_ENVS):
        if name and name not in candidates:
            candidates.append(name)

    for name in candidates:
        token = (os.getenv(name) or "").strip()
        if token:
            return token

    checked = ", ".join(candidates)
    raise HuggingFaceUploadError(
        f"Hugging Face token is not set (checked env: {checked}). "
        "In Colab: export HF_TOKEN=hf_... in the terminal before bootstrap, "
        "or set a Colab Secret named HF_TOKEN and load it in the notebook."
    )


def resolve_shards_repo_id(config: CampaignConfig, *, repo_id: str | None = None) -> str:
    target = repo_id or config.hf_shards_repo_id or config.hf_repo_id
    if not target:
        raise HuggingFaceUploadError("hf_shards_repo_id / hf_repo_id is not configured")
    return target


def resolve_videos_repo_id(config: CampaignConfig, *, repo_id: str | None = None) -> str:
    target = repo_id or config.hf_videos_repo_id or config.hf_repo_id
    if not target:
        raise HuggingFaceUploadError("hf_videos_repo_id / hf_repo_id is not configured")
    return target


def resolve_enrich_repo_id(config: CampaignConfig, *, repo_id: str | None = None) -> str:
    target = repo_id or config.hf_enrich_repo_id or config.hf_repo_id
    if not target:
        raise HuggingFaceUploadError("hf_enrich_repo_id / hf_repo_id is not configured")
    return target


def resolve_coord_repo_id(config: CampaignConfig) -> str:
    """HF repo for multi-Colab coordination blobs (must exist on Hub)."""
    explicit = getattr(config, "hf_coord_repo_id", None)
    if explicit:
        return explicit
    return resolve_shards_repo_id(config)


def resolve_progress_repo_id(config: CampaignConfig) -> str:
    """HF repo for session progress bundle (defaults to shards repo, not legacy hf_repo_id)."""
    explicit = getattr(config, "hf_progress_repo_id", None)
    if explicit:
        return explicit
    return resolve_shards_repo_id(config)


def is_allowed_metadata_shard_relpath(shard_relpath: str) -> bool:
    """Only metadata shard JSON belongs in the shards HF dataset repo."""
    rel = shard_relpath.lstrip("/").replace("\\", "/")
    if rel.startswith("state/") or "/coordination/" in rel:
        return False
    return rel.startswith("shards/metadata/")


def remote_shard_path(config: CampaignConfig, shard_relpath: str) -> str:
    if not is_allowed_metadata_shard_relpath(shard_relpath):
        raise HuggingFaceUploadError(f"refusing to upload non-metadata path to shards repo: {shard_relpath}")
    prefix = (config.hf_shards_path_prefix or "").strip("/")
    rel = shard_relpath.lstrip("/")
    return f"{prefix}/{rel}" if prefix else rel


def remote_video_path(config: CampaignConfig, *, category: str, video_id: str) -> str:
    prefix = (config.hf_videos_path_prefix or "videos").strip("/")
    return f"{prefix}/{category}/{video_id}.mp4"


def remote_enrich_path(config: CampaignConfig, enrich_relpath: str) -> str:
    prefix = (config.hf_enrich_path_prefix or "enrich").strip("/")
    rel = enrich_relpath.lstrip("/")
    if rel.startswith("shards/enrich/"):
        rel = rel.removeprefix("shards/enrich/")
    return f"{prefix}/{rel}" if prefix else rel


def get_hf_api(config: CampaignConfig):
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise HuggingFaceUploadError("Install huggingface_hub to enable HF uploads") from exc
    return HfApi(token=resolve_hf_token(config))


def wait_for_commit_slot(
    *,
    state_dir: Path,
    repo_id: str,
    min_interval_seconds: int,
    hourly_limit: int = 100,
) -> None:
    """Throttle commits per HF repo using both min interval and rolling-hour cap."""
    if min_interval_seconds <= 0 and hourly_limit <= 0:
        return
    log_path = state_dir / "hf_commit_log.jsonl"
    last_ts: float | None = None
    recent: list[float] = []
    now = time.time()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("repo_id") == repo_id:
                ts = float(row.get("ts") or 0)
                last_ts = ts
                if ts > now - 3600:
                    recent.append(ts)
    if last_ts:
        wait = min_interval_seconds - (now - last_ts)
        if wait > 0:
            time.sleep(wait)
            now = time.time()
    if hourly_limit > 0:
        recent = sorted(ts for ts in recent if ts > now - 3600)
        if len(recent) >= hourly_limit:
            wait = 3600 - (now - recent[0]) + 1
            if wait > 0:
                time.sleep(wait)


def record_commit(
    *,
    state_dir: Path,
    repo_id: str,
    files: int,
) -> None:
    log_path = state_dir / "hf_commit_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_id": repo_id,
        "files": files,
        "ts": time.time(),
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False))
        fh.write("\n")


def upload_local_file(
    config: CampaignConfig,
    local_path: Path,
    *,
    repo_id: str,
    path_in_repo: str,
) -> None:
    if not local_path.is_file():
        raise HuggingFaceUploadError(f"Local file not found: {local_path}")
    api = get_hf_api(config)
    api.upload_file(
        path_or_fileobj=str(local_path),
        path_in_repo=path_in_repo.lstrip("/"),
        repo_id=repo_id,
        repo_type="dataset",
    )


def upload_local_files_commit(
    config: CampaignConfig,
    files: Iterable[tuple[Path, str]],
    *,
    repo_id: str,
    commit_message: str,
    state_dir: Path | None = None,
) -> None:
    items = [(path, remote.lstrip("/")) for path, remote in files if path.is_file()]
    if not items:
        return
    if state_dir is not None:
        limits = resolve_hf_commit_limits(config)
        wait_for_commit_slot(
            state_dir=state_dir,
            repo_id=repo_id,
            min_interval_seconds=limits.min_interval_seconds,
            hourly_limit=limits.hourly_limit_per_colab,
        )
    api = get_hf_api(config)
    if len(items) == 1:
        local_path, remote = items[0]
        api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=remote,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=commit_message,
        )
    else:
        try:
            from huggingface_hub import CommitOperationAdd
        except ImportError as exc:
            raise HuggingFaceUploadError("Install huggingface_hub to enable batch commits") from exc
        operations = [
            CommitOperationAdd(path_in_repo=remote, path_or_fileobj=str(local_path))
            for local_path, remote in items
        ]
        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=commit_message,
        )
    if state_dir is not None:
        record_commit(state_dir=state_dir, repo_id=repo_id, files=len(items))


def upload_paths(
    config: CampaignConfig,
    paths: Iterable[Path],
    *,
    repo_id: str | None = None,
    path_builder: Callable[[Path], str] | None = None,
) -> dict[str, int]:
    target_repo = resolve_shards_repo_id(config, repo_id=repo_id)
    uploaded = 0
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        if path_builder is not None:
            remote = path_builder(path)
        else:
            prefix = (config.hf_path_prefix or config.hf_shards_path_prefix or "").strip("/")
            remote = f"{prefix}/{path.name}".strip("/") if prefix else path.name
        upload_local_file(config, path, repo_id=target_repo, path_in_repo=remote)
        uploaded += 1
    return {"uploaded": uploaded}


def maybe_upload_recent_shards(config: CampaignConfig, root: Path, shard_paths: list[Path]) -> dict[str, int]:
    """Legacy inline upload during discover; prefer hf_shard_upload_queue + upload-hf-shards."""
    if not config.hf_upload_enabled or not shard_paths:
        return {"uploaded": 0}
    every = max(config.hf_upload_every_shards, 1)
    if len(shard_paths) % every != 0:
        return {"uploaded": 0}
    return upload_paths(config, shard_paths[-every:])
