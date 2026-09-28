from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from fetcher.dataset_collector.checkpoint import DiscoveryCheckpoint
from fetcher.dataset_collector.hf_upload import (
    HuggingFaceUploadError,
    resolve_hf_token,
    resolve_progress_repo_id,
)
from fetcher.dataset_collector.schemas import CampaignConfig, CampaignManifest
from fetcher.dataset_collector.state import DatasetState, atomic_write_json, iter_jsonl, jsonable, utcnow


@dataclass(frozen=True)
class ProgressFileSpec:
    name: str
    local_path: Callable[[DatasetState], Path]
    merge: str  # replace_if_remote_newer | jsonl_union_key | jsonl_union_rows | checkpoint_newer
    key_field: str | None = None


def progress_enabled(config: CampaignConfig) -> bool:
    if not config.hf_upload_enabled:
        return False
    return bool(getattr(config, "hf_progress_enabled", True))


def progress_remote_prefix(config: CampaignConfig) -> str:
    return (getattr(config, "hf_progress_path_prefix", None) or "state/progress").strip("/")


def progress_remote_path(config: CampaignConfig, filename: str) -> str:
    return f"{progress_remote_prefix(config)}/{filename}".strip("/")


def _progress_specs() -> list[ProgressFileSpec]:
    return [
        ProgressFileSpec(
            "progress_meta.json",
            lambda s: s.state_dir / "progress_meta.json",
            "replace_if_remote_newer",
        ),
        ProgressFileSpec(
            "manifest.json",
            lambda s: s.manifest_path,
            "manifest_merge",
        ),
        ProgressFileSpec(
            "seen_ids.jsonl",
            lambda s: s.seen_path,
            "jsonl_union_key",
            "key",
        ),
        ProgressFileSpec(
            "keyword_progress.jsonl",
            lambda s: s.keyword_progress_path,
            "jsonl_union_rows",
        ),
        ProgressFileSpec(
            "discovery_checkpoint.json",
            lambda s: s.checkpoint_path,
            "checkpoint_newer",
        ),
        ProgressFileSpec(
            "balancer_snapshot.json",
            lambda s: s.balancer_snapshot_path,
            "replace_if_remote_newer",
        ),
        ProgressFileSpec(
            "channel_counts.json",
            lambda s: s.channel_counts_path,
            "replace_if_remote_newer",
        ),
        ProgressFileSpec(
            "download_done.jsonl",
            lambda s: s.download_done_path,
            "jsonl_union_key",
            "key",
        ),
        ProgressFileSpec(
            "download_queue.jsonl",
            lambda s: s.download_dir / "queue.jsonl",
            "jsonl_union_rows",
        ),
        ProgressFileSpec(
            "metadata_enrich_done.jsonl",
            lambda s: s.metadata_enrich_done_path,
            "jsonl_union_key",
            "key",
        ),
        ProgressFileSpec(
            "metadata_enrich_queue.jsonl",
            lambda s: s.metadata_enrich_queue_path,
            "jsonl_union_rows",
        ),
        ProgressFileSpec(
            "snapshot_completion.jsonl",
            lambda s: s.snapshot_completion_path,
            "jsonl_union_rows",
        ),
        ProgressFileSpec(
            "video_schedule.jsonl",
            lambda s: s.schedule_path,
            "jsonl_union_rows",
        ),
        ProgressFileSpec(
            "hf_video_upload_done.jsonl",
            lambda s: s.hf_video_upload_done_path,
            "jsonl_union_key",
            "key",
        ),
        ProgressFileSpec(
            "hf_enrich_upload_done.jsonl",
            lambda s: s.hf_enrich_upload_done_path,
            "jsonl_union_key",
            "key",
        ),
        ProgressFileSpec(
            "hf_snapshot_upload_done.jsonl",
            lambda s: s.hf_snapshot_upload_done_path,
            "jsonl_union_key",
            "shard",
        ),
    ]


ROLE_PROGRESS_FILES: dict[str, set[str]] = {
    "discover": {
        "progress_meta.json",
        "manifest.json",
        "seen_ids.jsonl",
        "keyword_progress.jsonl",
        "discovery_checkpoint.json",
        "balancer_snapshot.json",
        "channel_counts.json",
    },
    "download": {
        "progress_meta.json",
        "manifest.json",
        "download_done.jsonl",
        "download_queue.jsonl",
        "metadata_enrich_queue.jsonl",
        "hf_video_upload_done.jsonl",
    },
    "enrich": {
        "progress_meta.json",
        "manifest.json",
        "metadata_enrich_done.jsonl",
        "metadata_enrich_queue.jsonl",
        "hf_enrich_upload_done.jsonl",
    },
    "snapshot": {
        "progress_meta.json",
        "manifest.json",
        "video_schedule.jsonl",
        "snapshot_completion.jsonl",
    },
    "snapshot": {
        "progress_meta.json",
        "manifest.json",
        "video_schedule.jsonl",
        "snapshot_completion.jsonl",
        "hf_snapshot_upload_done.jsonl",
    },
    "workers": {
        "progress_meta.json",
        "manifest.json",
        "download_done.jsonl",
        "download_queue.jsonl",
        "metadata_enrich_done.jsonl",
        "metadata_enrich_queue.jsonl",
        "snapshot_completion.jsonl",
        "video_schedule.jsonl",
        "hf_video_upload_done.jsonl",
        "hf_enrich_upload_done.jsonl",
        "hf_snapshot_upload_done.jsonl",
    },
}


def _role_file_names(role: str | None) -> set[str]:
    if not role:
        return {spec.name for spec in _progress_specs()}
    mapped = ROLE_PROGRESS_FILES.get(role, ROLE_PROGRESS_FILES["workers"])
    return set(mapped)


def _download_remote_file(config: CampaignConfig, remote: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        resolve_hf_token(config)
        downloaded = hf_hub_download(
            repo_id=resolve_progress_repo_id(config),
            repo_type="dataset",
            filename=remote,
        )
        dest.write_bytes(Path(downloaded).read_bytes())
        return True
    except Exception as exc:
        if "404" in str(exc) or "EntryNotFound" in str(exc) or "not found" in str(exc).lower():
            return False
        raise


def _read_jsonl_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return list(iter_jsonl(path))


def _write_jsonl_rows(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(jsonable(row), ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, path)


def _row_fingerprint(row: dict) -> str:
    return json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True)


def _merge_jsonl_union_key(local: Path, remote: Path, *, key_field: str) -> int:
    rows_by_key: dict[str, dict] = {}
    for row in _read_jsonl_rows(remote):
        key = str(row.get(key_field) or "")
        if key:
            rows_by_key[key] = row
    added = 0
    for row in _read_jsonl_rows(local):
        key = str(row.get(key_field) or "")
        if not key:
            continue
        if key not in rows_by_key:
            added += 1
        rows_by_key[key] = row
    _write_jsonl_rows(local, rows_by_key.values())
    return added


def _merge_jsonl_union_rows(local: Path, remote: Path) -> int:
    seen: set[str] = set()
    merged: list[dict] = []
    for row in _read_jsonl_rows(remote):
        fp = _row_fingerprint(row)
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
    added = 0
    for row in _read_jsonl_rows(local):
        fp = _row_fingerprint(row)
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
        added += 1
    _write_jsonl_rows(local, merged)
    return added


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _merge_checkpoint(local: Path, remote: Path) -> bool:
    if not remote.is_file():
        return False
    remote_cp = DiscoveryCheckpoint.parse_obj(json.loads(remote.read_text(encoding="utf-8")))
    if not local.is_file():
        atomic_write_json(local, jsonable(remote_cp.dict()))
        return True
    local_cp = DiscoveryCheckpoint.parse_obj(json.loads(local.read_text(encoding="utf-8")))
    if remote_cp.updated_at >= local_cp.updated_at:
        atomic_write_json(local, jsonable(remote_cp.dict()))
        return True
    return False


def _merge_manifest(local: Path, remote: Path) -> bool:
    if not remote.is_file():
        return False
    remote_manifest = CampaignManifest.parse_obj(json.loads(remote.read_text(encoding="utf-8")))
    if not local.is_file():
        atomic_write_json(local, jsonable(remote_manifest.dict()))
        return True
    local_manifest = CampaignManifest.parse_obj(json.loads(local.read_text(encoding="utf-8")))

    merged_data = jsonable(local_manifest.dict())
    merged_data["counters"] = {
        "accepted": max(
            int(local_manifest.counters.get("accepted", 0)),
            int(remote_manifest.counters.get("accepted", 0)),
        ),
        "rejected": max(
            int(local_manifest.counters.get("rejected", 0)),
            int(remote_manifest.counters.get("rejected", 0)),
        ),
        "snapshots": max(
            int(local_manifest.counters.get("snapshots", 0)),
            int(remote_manifest.counters.get("snapshots", 0)),
        ),
        "downloads": max(
            int(local_manifest.counters.get("downloads", 0)),
            int(remote_manifest.counters.get("downloads", 0)),
        ),
    }
    category_counters = dict(local_manifest.category_counters or {})
    for category, remote_row in (remote_manifest.category_counters or {}).items():
        local_row = category_counters.setdefault(category, {"accepted": 0, "rejected": 0})
        local_row["accepted"] = max(int(local_row.get("accepted", 0)), int(remote_row.get("accepted", 0)))
        local_row["rejected"] = max(int(local_row.get("rejected", 0)), int(remote_row.get("rejected", 0)))
    merged_data["category_counters"] = category_counters
    merged_data["shards"] = sorted(
        set(local_manifest.shards or []) | set(remote_manifest.shards or [])
    )
    merged_data["snapshot_shards"] = sorted(
        set(local_manifest.snapshot_shards or []) | set(remote_manifest.snapshot_shards or [])
    )
    merged_data["rejected_shards"] = sorted(
        set(local_manifest.rejected_shards or []) | set(remote_manifest.rejected_shards or [])
    )
    merged_data["baseline_accepted"] = max(
        int(local_manifest.baseline_accepted or 0),
        int(remote_manifest.baseline_accepted or 0),
    )
    merged_data["updated_at"] = utcnow().isoformat()
    merged = CampaignManifest.parse_obj(merged_data)
    atomic_write_json(local, jsonable(merged.dict()))
    return True


def _merge_progress_meta(local: Path, remote: Path) -> bool:
    if not remote.is_file():
        return False
    remote_data = json.loads(remote.read_text(encoding="utf-8"))
    if not local.is_file():
        atomic_write_json(local, remote_data)
        return True
    local_data = json.loads(local.read_text(encoding="utf-8"))
    remote_ts = _parse_dt(remote_data.get("updated_at"))
    local_ts = _parse_dt(local_data.get("updated_at"))
    if remote_ts and (local_ts is None or remote_ts >= local_ts):
        merged = {**remote_data, **local_data}
        merged["discover_days_run"] = max(
            int(remote_data.get("discover_days_run") or 0),
            int(local_data.get("discover_days_run") or 0),
        )
        merged["discover_total_accepted"] = max(
            int(remote_data.get("discover_total_accepted") or 0),
            int(local_data.get("discover_total_accepted") or 0),
        )
        merged["updated_at"] = utcnow().isoformat()
        atomic_write_json(local, merged)
        return True
    return False


def _merge_file(spec: ProgressFileSpec, local: Path, remote_cache: Path) -> int | bool:
    if spec.merge == "jsonl_union_key" and spec.key_field:
        return _merge_jsonl_union_key(local, remote_cache, key_field=spec.key_field)
    if spec.merge == "jsonl_union_rows":
        return _merge_jsonl_union_rows(local, remote_cache)
    if spec.merge == "checkpoint_newer":
        return _merge_checkpoint(local, remote_cache)
    if spec.merge == "manifest_merge":
        return _merge_manifest(local, remote_cache)
    if spec.merge == "replace_if_remote_newer":
        return _merge_progress_meta(local, remote_cache)
    return False


def _invalidate_state_caches(state: DatasetState) -> None:
    state._seen = None


def pull_hf_progress(state: DatasetState, config: CampaignConfig, *, role: str | None = None) -> dict:
    if not progress_enabled(config):
        return {"pulled": 0, "skipped": 0, "files": []}
    repo = resolve_progress_repo_id(config)
    cache_root = state.state_dir / "hf_progress_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    allowed = _role_file_names(role)
    pulled: list[str] = []
    for spec in _progress_specs():
        if spec.name not in allowed:
            continue
        remote = progress_remote_path(config, spec.name)
        cache_path = cache_root / spec.name
        if not _download_remote_file(config, remote, cache_path):
            continue
        local = spec.local_path(state)
        try:
            _merge_file(spec, local, cache_path)
        except Exception as exc:
            print(
                f"[hf-progress] WARNING: merge failed for {spec.name}: {type(exc).__name__}: {exc} — skip",
                flush=True,
            )
            continue
        pulled.append(spec.name)
    _invalidate_state_caches(state)
    return {"pulled": len(pulled), "files": pulled, "repo": repo}


def push_hf_progress(state: DatasetState, config: CampaignConfig, *, role: str | None = None) -> dict:
    if not progress_enabled(config):
        return {"uploaded": 0, "files": []}
    repo = resolve_progress_repo_id(config)
    allowed = _role_file_names(role)
    cache_root = state.state_dir / "hf_progress_cache"
    uploads: list[tuple[Path, str]] = []
    for spec in _progress_specs():
        if spec.name not in allowed:
            continue
        local = spec.local_path(state)
        if not local.is_file():
            continue
        remote = progress_remote_path(config, spec.name)
        cache_path = cache_root / spec.name
        if _download_remote_file(config, remote, cache_path):
            _merge_file(spec, local, cache_path)
        uploads.append((local, remote))
    if not uploads:
        return {"uploaded": 0, "files": []}
    from fetcher.dataset_collector.hf_upload import upload_local_files_commit

    upload_local_files_commit(
        config,
        uploads,
        repo_id=repo,
        commit_message=f"Progress sync ({role or 'all'}) {utcnow().isoformat()}",
        state_dir=state.state_dir,
    )
    return {"uploaded": len(uploads), "files": [name for _, name in uploads], "repo": repo}


def restore_hf_progress_on_startup(
    state: DatasetState,
    config: CampaignConfig,
    *,
    role: str | None = None,
) -> dict:
    result = pull_hf_progress(state, config, role=role)
    summary = format_discover_resume_summary(state, config)
    if result.get("pulled"):
        print(
            f"[hf-progress] загружено с {result.get('repo')}: {', '.join(result.get('files', []))}",
            flush=True,
        )
    else:
        print("[hf-progress] на HF прогресса нет (первый запуск или пустой repo)", flush=True)
    if summary:
        print(summary, flush=True)
    return {**result, "summary": summary}


def format_discover_resume_summary(state: DatasetState, config: CampaignConfig) -> str:
    manifest = state.load_manifest()
    lines = ["[hf-progress] resume discover:"]
    complete = []
    pending = None
    for category in config.categories:
        accepted = state.live_category_accepted(category.name)
        if state.is_category_complete(category.name, category.collect_count):
            complete.append(f"{category.name} {accepted}/{category.collect_count}")
        elif pending is None:
            pending = category.name
            lines.append(
                f"  текущая категория: {category.name} ({accepted}/{category.collect_count})"
            )
    if complete:
        lines.append(f"  готово категорий: {len(complete)}")
    checkpoint = state.load_checkpoint()
    if checkpoint:
        lines.append(
            f"  checkpoint: kw#{checkpoint.keyword_index} "
            f"«{checkpoint.keyword or ''}» bucket={checkpoint.bucket_name} platform={checkpoint.platform}"
        )
    seen = len(state.load_seen())
    lines.append(f"  seen_ids: {seen}")
    meta = load_progress_meta(state)
    if meta.get("discover_week_started_at"):
        lines.append(
            f"  неделя discover: день {meta.get('discover_days_run', 0)}/"
            f"{getattr(config, 'discover_week_days', 0) or '∞'}"
        )
    return "\n".join(lines)


def load_progress_meta(state: DatasetState) -> dict:
    path = state.state_dir / "progress_meta.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_progress_meta(state: DatasetState, payload: dict) -> None:
    payload = dict(payload)
    payload["updated_at"] = utcnow().isoformat()
    atomic_write_json(state.state_dir / "progress_meta.json", payload)


def register_discover_daily_session(state: DatasetState, config: CampaignConfig) -> dict:
    meta = load_progress_meta(state)
    now = utcnow()
    today = now.date().isoformat()
    if not meta.get("discover_week_started_at"):
        meta["discover_week_started_at"] = now.isoformat()
        meta["discover_days_run"] = 0
    if meta.get("discover_last_session_date") != today:
        meta["discover_days_run"] = int(meta.get("discover_days_run") or 0) + 1
        meta["discover_last_session_date"] = today
    manifest = state.load_manifest()
    meta["discover_total_accepted"] = int(manifest.counters.get("accepted", 0))
    save_progress_meta(state, meta)
    return meta


def discover_week_allows_run(state: DatasetState, config: CampaignConfig) -> bool:
    days_limit = int(getattr(config, "discover_week_days", 0) or 0)
    if days_limit <= 0:
        return True
    meta = load_progress_meta(state)
    run_days = int(meta.get("discover_days_run") or 0)
    return run_days <= days_limit


def discover_week_complete_message(config: CampaignConfig) -> str:
    days = int(getattr(config, "discover_week_days", 0) or 0)
    return (
        f"Неделя discover ({days} дней) завершена — snapshot_0 больше не собираем. "
        "Запускайте snapshot-poll / workers для follow-up снапшотов."
    )
