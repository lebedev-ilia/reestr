from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class TimeIntervalBucket:
    name: str
    min_age_days: Optional[int]
    max_age_days: Optional[int]
    weight: float

    def published_window(self, now: datetime | None = None) -> tuple[Optional[datetime], Optional[datetime]]:
        current = now or datetime.now(timezone.utc)
        published_after = current - timedelta(days=self.max_age_days) if self.max_age_days is not None else None
        published_before = current - timedelta(days=self.min_age_days) if self.min_age_days is not None else None
        return published_after, published_before

    def contains_published_at(self, published_at: datetime, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        age_days = (current - published_at).total_seconds() / 86400
        if self.min_age_days is not None and age_days < self.min_age_days:
            return False
        if self.max_age_days is not None and age_days >= self.max_age_days:
            return False
        return True


DEFAULT_TIME_INTERVAL_BUCKETS = [
    TimeIntervalBucket("lt_1d", 0, 1, 0.20),
    TimeIntervalBucket("1d_1w", 1, 7, 0.20),
    TimeIntervalBucket("1w_1m", 7, 30, 0.12),
    TimeIntervalBucket("1m_3m", 30, 90, 0.16),
    TimeIntervalBucket("3m_6m", 90, 180, 0.14),
    TimeIntervalBucket("6m_1y", 180, 365, 0.08),
    TimeIntervalBucket("1y_3y", 365, 1095, 0.06),
    TimeIntervalBucket("gt_3y", 1095, None, 0.04),
]


def bucket_from_config(raw: dict) -> TimeIntervalBucket:
    return TimeIntervalBucket(
        name=str(raw["name"]),
        min_age_days=raw.get("min_age_days"),
        max_age_days=raw.get("max_age_days"),
        weight=float(raw.get("weight", 0)),
    )


def allocate_counts(buckets: Iterable[TimeIntervalBucket], target: int) -> dict[str, int]:
    bucket_list = list(buckets)
    total_weight = sum(max(bucket.weight, 0) for bucket in bucket_list) or 1.0
    allocated: dict[str, int] = {}
    remaining = target
    for bucket in bucket_list:
        count = int(target * max(bucket.weight, 0) / total_weight)
        allocated[bucket.name] = count
        remaining -= count
    for bucket in sorted(bucket_list, key=lambda item: item.weight, reverse=True):
        if remaining <= 0:
            break
        allocated[bucket.name] += 1
        remaining -= 1
    return allocated


def infer_time_interval(published_at: datetime | None, buckets: Iterable[TimeIntervalBucket]) -> str | None:
    if published_at is None:
        return None
    for bucket in buckets:
        if bucket.contains_published_at(published_at):
            return bucket.name
    return None
