from __future__ import annotations

from datetime import timedelta
from typing import Any

from fetcher.dataset_collector.state import DatasetState, append_jsonl, iter_jsonl, utcnow


def queue_item_key(service: str, item: dict[str, Any]) -> str:
    platform = item.get("platform") or "youtube"
    category = item.get("category") or "unknown"
    video_id = item.get("video_id")
    if video_id:
        return f"{service}:{platform}:{category}:{video_id}"
    shard = item.get("shard") or item.get("local_path")
    if shard:
        return f"{service}:{shard}"
    return f"{service}:unknown"


def load_dead_letter_keys(state: DatasetState, *, service: str | None = None) -> set[str]:
    keys: set[str] = set()
    for row in iter_jsonl(state.queue_dead_letter_path):
        if service and row.get("service") != service:
            continue
        key = row.get("key")
        if key:
            keys.add(str(key))
    return keys


def load_attempt_counts(state: DatasetState, *, service: str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in iter_jsonl(state.queue_failures_path):
        if service and row.get("service") != service:
            continue
        key = row.get("key")
        if not key:
            continue
        counts[str(key)] = max(counts.get(str(key), 0), int(row.get("attempt") or 0))
    return counts


def record_queue_failure(
    state: DatasetState,
    *,
    service: str,
    item: dict[str, Any],
    error: str,
    dead_letter_cache: set[str] | None = None,
    attempt_cache: dict[str, int] | None = None,
) -> bool:
    """Record a retryable queue failure. Returns True once the item is dead-lettered.

    Pass dead_letter_cache / attempt_cache from the caller's session-level cache
    (populated with load_dead_letter_keys / load_attempt_counts at pass start) to
    avoid O(N) full-file reads on every invocation.
    """
    key = queue_item_key(service, item)

    if dead_letter_cache is not None:
        if key in dead_letter_cache:
            return True
    else:
        if key in load_dead_letter_keys(state, service=service):
            return True

    if attempt_cache is not None:
        attempt = attempt_cache.get(key, 0) + 1
        attempt_cache[key] = attempt
    else:
        attempts = load_attempt_counts(state, service=service)
        attempt = attempts.get(key, 0) + 1

    now = utcnow()
    next_retry_at = now + timedelta(seconds=state.config.queue_retry_backoff_seconds * attempt)
    payload = {
        "service": service,
        "key": key,
        "category": item.get("category") or "unknown",
        "video_id": item.get("video_id"),
        "shard": item.get("shard") or item.get("local_path"),
        "attempt": attempt,
        "error": str(error)[:1000],
        "failed_at": now.isoformat(),
        "next_retry_at": next_retry_at.isoformat(),
    }
    append_jsonl(state.queue_failures_path, payload)
    if attempt >= state.config.queue_max_attempts:
        append_jsonl(
            state.queue_dead_letter_path,
            {
                **payload,
                "dead_lettered_at": utcnow().isoformat(),
                "max_attempts": state.config.queue_max_attempts,
            },
        )
        if dead_letter_cache is not None:
            dead_letter_cache.add(key)
        return True
    return False
