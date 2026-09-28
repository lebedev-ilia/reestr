from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fetcher.dataset_collector.schemas import (
    BalancerConfig,
    BalancerFieldConfig,
    CampaignConfig,
    CollectedVideo,
)
from fetcher.dataset_collector.training_format import iter_metadata_shard_entries


DISCOVERY_FIELDS = {
    "category",
    "channel",
    "comments_available",
    "comment_count",
    "country",
    "duration_seconds",
    "language",
    "like_count",
    "made_for_kids",
    "time_interval",
    "view_count",
}


@dataclass(frozen=True)
class BalancerDecision:
    accepted: bool
    score: float = 1.0
    reason: str = ""
    field_scores: dict[str, float] = field(default_factory=dict)
    field_values: dict[str, str] = field(default_factory=dict)


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bucket_label(value: int, buckets: list[list[int | None]] | None) -> str:
    if not buckets:
        return str(value)
    for idx, bucket in enumerate(buckets, start=1):
        low, high = bucket
        if low is not None and value < low:
            continue
        if high is not None and value > high:
            continue
        return _bucket_label_from_bounds(idx, low, high)
    return "out_of_range"


def _bucket_label_from_bounds(index: int, low: int | None, high: int | None) -> str:
    low_label = str(low) if low is not None else "min"
    high_label = str(high) if high is not None else "max"
    return f"{index:02d} {low_label}-{high_label}"


def _snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("snapshot_0") or {}


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("metadata") or {}


def _field_value_from_record(
    field_name: str,
    record: dict[str, Any],
    field_config: BalancerFieldConfig,
    *,
    category: str | None = None,
) -> str | None:
    metadata = _metadata(record)
    snapshot = _snapshot(record)
    if field_name == "category":
        return str(category or record.get("category") or "unknown")
    if field_name == "time_interval":
        return str(record.get("time_interval") or "unknown")
    if field_name == "language":
        return str(metadata.get("language") or "unknown")
    if field_name == "country":
        return str(metadata.get("country") or "unknown")
    if field_name == "made_for_kids":
        return str(bool(metadata.get("madeForKids"))).lower()
    if field_name == "comments_available":
        comments = snapshot.get("comments")
        if isinstance(comments, list):
            return "yes" if comments else "no"
        count = _to_int(snapshot.get("commentCount") or metadata.get("comment_count"))
        if count is not None:
            return "yes" if count > 0 else "no"
        return "unknown"
    if field_name == "channel":
        channel = (
            metadata.get("channel_id")
            or metadata.get("channelId")
            or metadata.get("channelTitle")
            or record.get("channel_id")
        )
        return str(channel or "unknown")
    if field_name == "view_count":
        value = _to_int(snapshot.get("viewCount") or metadata.get("view_count"))
    elif field_name == "like_count":
        value = _to_int(snapshot.get("likeCount") or metadata.get("like_count"))
    elif field_name == "comment_count":
        value = _to_int(snapshot.get("commentCount") or metadata.get("comment_count"))
    elif field_name == "duration_seconds":
        value = _to_int(metadata.get("duration_seconds") or metadata.get("duration"))
    else:
        raw = metadata.get(field_name) or record.get(field_name)
        if raw is None:
            return None
        return str(raw)
    if value is None:
        return "unknown"
    return _bucket_label(value, field_config.buckets)


def extract_discovery_values(
    video: CollectedVideo,
    fields: dict[str, BalancerFieldConfig],
) -> dict[str, str]:
    record = video.dict()
    values: dict[str, str] = {}
    for field_name, field_config in fields.items():
        if field_name not in DISCOVERY_FIELDS or field_config.coefficient <= 0:
            continue
        value = _field_value_from_record(
            field_name,
            record,
            field_config,
            category=video.category,
        )
        if value is not None:
            values[field_name] = value
    return values


class DatasetBalancer:
    _SAVE_EVERY = 50  # persist snapshot every N accepted videos

    def __init__(
        self,
        config: BalancerConfig | None,
        *,
        state_root: Path | str,
        campaign_config: CampaignConfig | None = None,
    ) -> None:
        self.config = config
        self.campaign_config = campaign_config
        self.state_root = Path(state_root)
        seed = config.random_seed if config else None
        self.random = random.Random(seed)
        self.counts: dict[str, Counter[str]] = {}
        self._accepts_since_save: int = 0
        self._load_existing_counts()

    @property
    def _snapshot_path(self) -> Path:
        return self.state_root / "state" / "balancer_snapshot.json"

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled and self.config.fields)

    def enabled_field_names(self) -> list[str]:
        if not self.config:
            return []
        return [
            name
            for name, field_config in self.config.fields.items()
            if field_config.coefficient > 0 and name in DISCOVERY_FIELDS
        ]

    def decide(self, video: CollectedVideo) -> BalancerDecision:
        if not self.enabled or self.config is None:
            return BalancerDecision(True)

        values = extract_discovery_values(video, self.config.fields)
        if not values:
            return BalancerDecision(self.config.default_action == "accept")

        weighted_penalty = 0.0
        total_weight = 0.0
        field_scores: dict[str, float] = {}
        worst_field = ""
        worst_penalty = -1.0
        for field_name, value in values.items():
            field_config = self.config.fields[field_name]
            coefficient = field_config.coefficient
            penalty = self._field_penalty(field_name, value, field_config)
            field_score = max(0.0, 1.0 - penalty)
            field_scores[field_name] = round(field_score, 4)
            weighted_penalty += coefficient * penalty
            total_weight += coefficient
            if penalty > worst_penalty:
                worst_penalty = penalty
                worst_field = field_name

        score = 1.0 if total_weight <= 0 else max(0.0, 1.0 - (weighted_penalty / total_weight))
        score = round(score, 4)
        strictness = min(1.0, total_weight / max(len(values), 1))

        if self.config.mode == "hard":
            accepted = score >= self.config.min_accept_score
        elif self.config.mode == "soft":
            accepted = self.random.random() <= score
        else:
            if strictness >= 0.75 and score < self.config.min_accept_score:
                accepted = False
            else:
                probability = (1.0 - strictness) + strictness * score
                accepted = self.random.random() <= probability

        reason = "" if accepted else f"balancer_{worst_field or 'score'}"
        return BalancerDecision(
            accepted=accepted,
            score=score,
            reason=reason,
            field_scores=field_scores,
            field_values=values,
        )

    def observe_accept(self, video: CollectedVideo) -> None:
        if not self.enabled or self.config is None:
            return
        for field_name, value in extract_discovery_values(video, self.config.fields).items():
            self.counts.setdefault(field_name, Counter())[value] += 1
        self._accepts_since_save += 1
        if self._accepts_since_save >= self._SAVE_EVERY:
            self._save_snapshot()
            self._accepts_since_save = 0

    def flush_snapshot(self) -> None:
        """Persist balancer state on clean shutdown."""
        if self._accepts_since_save > 0:
            self._save_snapshot()

    def _save_snapshot(self) -> None:
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        data = {field: dict(counter) for field, counter in self.counts.items()}
        tmp = self._snapshot_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            import os
            os.replace(tmp, self._snapshot_path)
        except OSError:
            tmp.unlink(missing_ok=True)

    def bucket_fill_ratio(self, field_name: str, value: str) -> float:
        if not self.config or field_name not in self.config.fields:
            return 0.0
        counts = self.counts.get(field_name, Counter())
        total = sum(counts.values())
        if total <= 0:
            return 0.0
        return counts.get(value, 0) / total

    def _field_penalty(
        self,
        field_name: str,
        value: str,
        field_config: BalancerFieldConfig,
    ) -> float:
        counts = self.counts.setdefault(field_name, Counter())
        total = sum(counts.values())
        current = counts.get(value, 0)
        if total <= 0:
            return 0.0

        if value == "unknown" and field_config.unknown_policy == "separate_cap":
            cap = field_config.unknown_max_share
            if cap is not None and current / total >= cap:
                return 1.0

        if field_config.max_share is not None and current / total >= field_config.max_share:
            return 1.0

        target = self._target_share(field_name, value, field_config)
        projected_share = (current + 1) / (total + 1)
        if projected_share <= target:
            return 0.0
        return min(1.0, (projected_share - target) / max(target, 0.001))

    def _target_share(
        self,
        field_name: str,
        value: str,
        field_config: BalancerFieldConfig,
    ) -> float:
        targets = field_config.targets
        if targets == "campaign_weights" and field_name == "time_interval":
            campaign_targets = self._campaign_time_interval_targets()
            if value in campaign_targets:
                return campaign_targets[value]
        if isinstance(targets, dict):
            raw = targets.get(value)
            if raw is not None:
                try:
                    return max(0.001, float(raw))
                except (TypeError, ValueError):
                    pass

        if isinstance(targets, list):
            values = self._configured_values(field_config)
            if value in values:
                index = values.index(value)
                if index < len(targets):
                    try:
                        return max(0.001, float(targets[index]))
                    except (TypeError, ValueError):
                        pass

        values = self._known_values(field_name, field_config)
        return 1.0 / max(len(values), 1)

    def _configured_values(self, field_config: BalancerFieldConfig) -> list[str]:
        if field_config.buckets:
            return [
                _bucket_label_from_bounds(index, bucket[0], bucket[1])
                for index, bucket in enumerate(field_config.buckets, start=1)
            ]
        return []

    def _known_values(self, field_name: str, field_config: BalancerFieldConfig) -> set[str]:
        configured = set(self._configured_values(field_config))
        observed = set(self.counts.get(field_name, Counter()).keys())
        if configured:
            return configured | observed
        return observed or {"unknown"}

    def _campaign_time_interval_targets(self) -> dict[str, float]:
        if not self.campaign_config:
            return {}
        weights: dict[str, float] = {}
        for item in self.campaign_config.time_interval_buckets:
            name = item.get("name")
            if not name:
                continue
            try:
                weights[str(name)] = float(item.get("weight") or 0)
            except (TypeError, ValueError):
                continue
        total = sum(value for value in weights.values() if value > 0)
        if total <= 0:
            return {}
        return {name: max(0.001, value / total) for name, value in weights.items() if value > 0}

    def _load_existing_counts(self) -> None:
        if not self.config or not self.config.fields:
            return
        # Fast path: load from persisted snapshot (updated every _SAVE_EVERY accepts).
        if self._snapshot_path.exists():
            try:
                data = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
                for field_name, counts in data.items():
                    self.counts[field_name] = Counter({str(k): int(v) for k, v in counts.items()})
                return
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        # Slow fallback: scan all shards (first run or corrupted snapshot).
        self._scan_shards_for_counts()
        self._save_snapshot()

    def _scan_shards_for_counts(self) -> None:
        if not self.config or not self.config.fields:
            return
        metadata_dir = self.state_root / "shards" / "metadata"
        if not metadata_dir.exists():
            return
        for shard_path in metadata_dir.glob("**/part_*.json"):
            try:
                data = json.loads(shard_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            category = self._category_from_path(shard_path)
            for record in iter_metadata_shard_entries(data):
                for field_name, field_config in self.config.fields.items():
                    if field_config.coefficient <= 0 or field_name not in DISCOVERY_FIELDS:
                        continue
                    value = _field_value_from_record(
                        field_name,
                        record,
                        field_config,
                        category=category,
                    )
                    if value is not None:
                        self.counts.setdefault(field_name, Counter())[value] += 1

    @staticmethod
    def _category_from_path(path: Path) -> str:
        for part in path.parts:
            if part.startswith("category="):
                return part.split("=", 1)[1] or "unknown"
        return "unknown"
