from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fetcher.dataset_collector.hf_commit_budget import (
    format_hf_commit_limits_summary,
    resolve_hf_commit_limits,
)
from fetcher.dataset_collector.hf_upload import (
    get_hf_api,
    resolve_coord_repo_id,
    resolve_shards_repo_id,
    upload_local_file,
    wait_for_commit_slot,
    record_commit,
)
from fetcher.dataset_collector.schemas import CampaignConfig
from fetcher.dataset_collector.state import DatasetState, append_jsonl, file_lock, iter_jsonl
from fetcher.dataset_collector.worker_logging import worker_log

_SAFE_WORKER_ID = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize_worker_id(raw: str) -> str:
    cleaned = _SAFE_WORKER_ID.sub("_", (raw or "").strip())
    return cleaned or "worker"


def resolve_worker_id(config: CampaignConfig) -> str:
    explicit = (config.worker_id or os.getenv("DATASET_WORKER_ID") or "").strip()
    if explicit:
        return _sanitize_worker_id(explicit)
    return _sanitize_worker_id(os.getenv("COLAB_RELEASE_TAG") or os.getenv("HOSTNAME") or f"pid-{os.getpid()}")


def coord_enabled(config: CampaignConfig) -> bool:
    return bool(config.hf_coord_enabled and config.hf_upload_enabled)


def stable_shard_slot(key: str, shard_count: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % max(shard_count, 1)


def key_in_worker_shard(key: str, config: CampaignConfig) -> bool:
    count = config.worker_shard_count
    if not count or count <= 1:
        return True
    index = config.worker_shard_index
    if index is None:
        return True
    return stable_shard_slot(key, count) == int(index) % count


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkerCoordination:
    """HF-backed claims and done sets for multi-Colab download/enrich workers."""

    def __init__(self, state: DatasetState, config: CampaignConfig) -> None:
        self.state = state
        self.config = config
        self.worker_id = resolve_worker_id(config)
        # Coordination blobs live on an existing HF dataset repo (shards by default).
        self.repo_id = resolve_coord_repo_id(config)
        prefix = (config.hf_coord_path_prefix or "state/coordination").strip("/")
        self.remote_prefix = prefix
        self.local_root = state.state_dir / "coordination"
        self.local_root.mkdir(parents=True, exist_ok=True)
        self._active_claims: dict[str, dict] = {}
        self.global_done_keys: set[str] = set()
        self._shard_sync_cache = state.state_dir / "hf_shard_sync.json"
        self._commit_limits = resolve_hf_commit_limits(config)
        self._coord_dirty: set[str] = set()
        self._coord_upload_at: dict[str, float] = {}
        if coord_enabled(config):
            from fetcher.dataset_collector.metrics import record_coord_worker_identity

            record_coord_worker_identity(config, self.worker_id)
            worker_log("coord", format_hf_commit_limits_summary(self._commit_limits))

    def _remote(self, *parts: str) -> str:
        return "/".join([self.remote_prefix, *parts])

    def _local_claims_path(self, service: str) -> Path:
        return self.local_root / "claims" / service / f"{self.worker_id}.jsonl"

    def _local_done_path(self, service: str) -> Path:
        return self.local_root / "done" / service / f"{self.worker_id}.jsonl"

    def _load_jsonl_keys(self, path: Path, *, key_field: str = "key") -> set[str]:
        return {
            str(row.get(key_field))
            for row in iter_jsonl(path)
            if row.get(key_field)
        }

    def _merge_claim_rows(self, rows: Iterable[dict], *, ttl_seconds: int) -> dict[str, dict]:
        now = _utcnow()
        merged: dict[str, dict] = {}
        for row in rows:
            key = str(row.get("key") or "")
            if not key:
                continue
            status = str(row.get("status") or "active")
            if status in {"done", "released"}:
                merged.pop(key, None)
                continue
            expires = _parse_ts(str(row.get("expires_at") or ""))
            if expires and expires < now:
                continue
            owner = str(row.get("owner") or "")
            claimed_at = _parse_ts(str(row.get("claimed_at") or ""))
            prev = merged.get(key)
            if prev is None:
                merged[key] = row
                continue
            prev_at = _parse_ts(str(prev.get("claimed_at") or ""))
            if claimed_at and (prev_at is None or claimed_at >= prev_at):
                merged[key] = row
        return merged

    def sync_from_hf(self, service: str) -> dict[str, int]:
        if not coord_enabled(self.config):
            return {}
        api = get_hf_api(self.config)
        stats = {"claims_files": 0, "done_files": 0, "metadata_shards": 0}
        try:
            repo_files = api.list_repo_files(self.repo_id, repo_type="dataset")
        except Exception as exc:
            worker_log(service, f"coord HF list_repo_files failed: {exc}")
            from fetcher.dataset_collector.metrics import record_coord_sync_error

            record_coord_sync_error(self.worker_id, service)
            return stats

        claims_prefix = f"{self._remote('claims', service)}/"
        done_prefix = f"{self._remote('done', service)}/"
        metadata_prefix = (self.config.hf_shards_path_prefix or "shards/metadata").strip("/")
        if metadata_prefix:
            metadata_prefix = metadata_prefix.rstrip("/") + "/"

        for remote_path in repo_files:
            if remote_path.startswith(claims_prefix) and remote_path.endswith(".jsonl"):
                stats["claims_files"] += 1
                self._download_remote_jsonl(api, remote_path, self.local_root / "remote" / remote_path)
            elif remote_path.startswith(done_prefix) and remote_path.endswith(".jsonl"):
                stats["done_files"] += 1
                local = self.local_root / "remote" / remote_path
                self._download_remote_jsonl(api, remote_path, local)
                self.global_done_keys |= self._load_jsonl_keys(local)

        if service in {"download", "enrich"}:
            stats["metadata_shards"] = self.sync_metadata_shards_from_hf(api, repo_files, metadata_prefix)

        claim_rows: list[dict] = []
        claims_dir = self.local_root / "remote" / self._remote("claims", service)
        if claims_dir.exists():
            for path in sorted(claims_dir.glob("*.jsonl")):
                claim_rows.extend(iter_jsonl(path))
        claim_rows.extend(iter_jsonl(self._local_claims_path(service)))
        self._active_claims = self._merge_claim_rows(
            claim_rows,
            ttl_seconds=self.config.hf_coord_claim_ttl_seconds,
        )
        self.global_done_keys |= self._load_jsonl_keys(self._local_done_path(service))
        from fetcher.dataset_collector.metrics import record_coord_sync

        record_coord_sync(
            self.worker_id,
            service,
            stats,
            active_claims=len(self._active_claims),
            global_done=len(self.global_done_keys),
        )
        return stats

    def _download_remote_jsonl(self, api, remote_path: str, local_path: Path) -> None:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            return
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            downloaded = hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename=remote_path,
                local_dir=str(local_path.parent),
                local_dir_use_symlinks=False,
            )
            target = Path(downloaded)
            if target != local_path and target.exists():
                local_path.write_bytes(target.read_bytes())
        except Exception:
            pass

    def sync_metadata_shards_from_hf(
        self,
        api,
        repo_files: list[str],
        metadata_prefix: str,
    ) -> int:
        known: set[str] = set()
        if self._shard_sync_cache.exists():
            try:
                import json

                data = json.loads(self._shard_sync_cache.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    known = {str(x) for x in data}
            except Exception:
                known = set()

        downloaded = 0
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            return 0

        for remote_path in repo_files:
            if metadata_prefix and not remote_path.startswith(metadata_prefix):
                continue
            if not remote_path.endswith(".json") or remote_path.endswith(".tmp"):
                continue
            if remote_path in known:
                continue
            # Map HF path -> local shards/metadata/...
            if metadata_prefix:
                rel_under_meta = remote_path[len(metadata_prefix) :]
            else:
                rel_under_meta = remote_path
            if rel_under_meta.startswith("shards/metadata/"):
                local_rel = rel_under_meta
            else:
                local_rel = f"shards/metadata/{rel_under_meta}"
            local_path = self.state.root / local_rel
            if local_path.is_file() and local_path.stat().st_size > 0:
                known.add(remote_path)
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                cache_dir = self.local_root / "shard_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                fetched = hf_hub_download(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    filename=remote_path,
                    local_dir=str(cache_dir),
                    local_dir_use_symlinks=False,
                )
                src = Path(fetched)
                if src.is_file():
                    local_path.write_bytes(src.read_bytes())
                downloaded += 1
                known.add(remote_path)
            except Exception as exc:
                worker_log("coord", f"skip shard download {remote_path}: {exc}")
        if downloaded:
            import json

            self._shard_sync_cache.write_text(
                json.dumps(sorted(known), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return downloaded

    def skip_reason(self, service: str, key: str) -> str | None:
        if not coord_enabled(self.config):
            return None
        if key in self.global_done_keys:
            return "coord_done"
        if not key_in_worker_shard(key, self.config):
            return "coord_shard"
        claim = self._active_claims.get(key)
        if not claim:
            return None
        owner = str(claim.get("owner") or "")
        if owner == self.worker_id:
            return None
        return "coord_claimed"

    def record_skip(self, service: str, reason: str) -> None:
        from fetcher.dataset_collector.metrics import record_coord_skip

        record_coord_skip(self.worker_id, service, reason)

    def try_claim(self, service: str, key: str) -> bool:
        if not coord_enabled(self.config):
            return True
        reason = self.skip_reason(service, key)
        if reason:
            from fetcher.dataset_collector.metrics import record_coord_claim

            record_coord_claim(self.worker_id, service, ok=False)
            return False
        now = _utcnow()
        expires = now + timedelta(seconds=max(self.config.hf_coord_claim_ttl_seconds, 300))
        record = {
            "key": key,
            "owner": self.worker_id,
            "service": service,
            "status": "active",
            "claimed_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        path = self._local_claims_path(service)
        with file_lock(self.state.state_dir / f"coord_claim_{service}.lock"):
            append_jsonl(path, record)
        self._active_claims[key] = record
        self._coord_dirty.add(service)
        from fetcher.dataset_collector.metrics import record_coord_claim

        record_coord_claim(self.worker_id, service, ok=True)
        return True

    def mark_done(self, service: str, key: str, **extra: str) -> None:
        if not coord_enabled(self.config):
            return
        now = _utcnow()
        payload = {
            "key": key,
            "owner": self.worker_id,
            "service": service,
            "status": "done",
            "completed_at": now.isoformat(),
            **extra,
        }
        append_jsonl(self._local_done_path(service), payload)
        release = {
            "key": key,
            "owner": self.worker_id,
            "service": service,
            "status": "released",
            "released_at": now.isoformat(),
        }
        append_jsonl(self._local_claims_path(service), release)
        self.global_done_keys.add(key)
        self._active_claims.pop(key, None)
        self._coord_dirty.add(service)

    def flush_coord_uploads(self, service: str | None = None, *, force: bool = False) -> None:
        """Batch HF uploads of claims/done (avoids one commit per video)."""
        if not coord_enabled(self.config):
            return
        targets = [service] if service else sorted(self._coord_dirty)
        now = time.time()
        for svc in targets:
            if svc not in self._coord_dirty and not force:
                continue
            last = self._coord_upload_at.get(svc, 0.0)
            if (
                not force
                and (now - last) < self._commit_limits.coord_upload_min_interval_seconds
            ):
                continue
            self._upload_claims_file(svc)
            self._upload_done_file(svc)
            self._coord_upload_at[svc] = time.time()
            self._coord_dirty.discard(svc)

    def _upload_claims_file(self, service: str) -> None:
        local_path = self._local_claims_path(service)
        if not local_path.is_file():
            return
        remote = self._remote("claims", service, f"{self.worker_id}.jsonl")
        self._upload_coord_file(local_path, remote)

    def _upload_done_file(self, service: str) -> None:
        local_path = self._local_done_path(service)
        if not local_path.is_file():
            return
        remote = self._remote("done", service, f"{self.worker_id}.jsonl")
        self._upload_coord_file(local_path, remote)

    def _upload_coord_file(self, local_path: Path, remote: str) -> None:
        try:
            wait_for_commit_slot(
                state_dir=self.state.state_dir,
                repo_id=self.repo_id,
                min_interval_seconds=self._commit_limits.min_interval_seconds,
                hourly_limit=self._commit_limits.hourly_limit_per_colab,
            )
            upload_local_file(self.config, local_path, repo_id=self.repo_id, path_in_repo=remote)
            record_commit(state_dir=self.state.state_dir, repo_id=self.repo_id, files=1)
        except Exception as exc:
            worker_log("coord", f"upload {remote} failed: {exc}")
