from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class FilterDecision:
    accepted: bool
    reason: str = ""


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class VideoFilter:
    # Keep at most this many view_count samples for outlier detection.
    _SAMPLE_CAP = 500

    def __init__(
        self,
        rules: Dict[str, Any] | None = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self.rules = rules or {}
        self.state_path = state_path
        self._channel_counts: Dict[str, int] = {}
        self._view_count_samples: list[int] = []
        self._accepts_since_save: int = 0
        if state_path:
            self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(self, record: Dict[str, Any]) -> FilterDecision:
        metadata = record.get("metadata") or record
        duration = _to_int(metadata.get("duration_seconds") or metadata.get("duration"))
        min_duration = _to_int(self.rules.get("duration_min_seconds"))
        max_duration = _to_int(self.rules.get("duration_max_seconds"))
        if min_duration is not None:
            if duration is None or duration < min_duration:
                return FilterDecision(False, "duration_below_min")
        if max_duration is not None and duration is not None and duration > max_duration:
            return FilterDecision(False, "duration_above_max")

        view_count = _to_int(
            metadata.get("viewCount")
            or metadata.get("view_count")
            or (metadata.get("statistics") or {}).get("viewCount")
        )
        view_max = _to_int(self.rules.get("view_count_max"))
        if view_max is not None and view_count is not None and view_count > view_max:
            return FilterDecision(False, "view_count_above_max")

        # Outlier policy: reject statistical outliers within the running sample.
        outlier_policy = self.rules.get("outlier_policy")
        if outlier_policy == "reject" and view_count is not None:
            if self._is_view_count_outlier(view_count):
                return FilterDecision(False, "view_count_outlier")

        comment_count = _to_int(
            metadata.get("commentCount")
            or metadata.get("comment_count")
            or (metadata.get("statistics") or {}).get("commentCount")
        )
        comment_max = _to_int(self.rules.get("comment_count_max"))
        if comment_max is not None and comment_count is not None and comment_count > comment_max:
            return FilterDecision(False, "comment_count_above_max")

        channel_id = (
            metadata.get("channel_id")
            or metadata.get("channelId")
            or metadata.get("uploader_id")
            or record.get("channel_id")
        )
        channel_cap = _to_int(self.rules.get("channel_video_cap"))
        if channel_cap is not None and channel_id:
            current = self._channel_counts.get(str(channel_id), 0)
            if current >= channel_cap:
                return FilterDecision(False, "channel_cap_exceeded")

        return FilterDecision(True)

    def decide_post_enrich(self, *, info: Dict[str, Any], metadata: Dict[str, Any]) -> FilterDecision:
        """Reject live streams and invalid durations revealed by yt-dlp."""
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            return FilterDecision(False, "live_stream")
        duration = _to_int(info.get("duration"))
        if duration is None:
            duration = _to_int(metadata.get("duration_seconds") or metadata.get("duration"))
        return self.decide({"metadata": {**metadata, "duration_seconds": duration, "duration": duration}})

    def accept(self, record: Dict[str, Any]) -> None:
        metadata = record.get("metadata") or record
        channel_id = metadata.get("channel_id") or metadata.get("channelId") or metadata.get("uploader_id")
        if channel_id:
            key = str(channel_id)
            self._channel_counts[key] = self._channel_counts.get(key, 0) + 1

        view_count = _to_int(
            metadata.get("viewCount")
            or metadata.get("view_count")
            or (metadata.get("statistics") or {}).get("viewCount")
        )
        if view_count is not None:
            self._view_count_samples.append(view_count)
            if len(self._view_count_samples) > self._SAMPLE_CAP:
                self._view_count_samples.pop(0)

        self._accepts_since_save += 1
        if self.state_path and self._accepts_since_save >= 100:
            self._save_state()
            self._accepts_since_save = 0

    def flush_state(self) -> None:
        """Call on clean shutdown to persist any buffered state."""
        if self.state_path and self._accepts_since_save > 0:
            self._save_state()
            self._accepts_since_save = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_view_count_outlier(self, view_count: int) -> bool:
        """True if view_count is a statistical outlier in the running sample (z-score > threshold)."""
        if len(self._view_count_samples) < 20:
            return False
        mean = statistics.mean(self._view_count_samples)
        try:
            std = statistics.stdev(self._view_count_samples)
        except statistics.StatisticsError:
            return False
        if std <= 0:
            return False
        z = (view_count - mean) / std
        threshold = float(self.rules.get("outlier_z_score_threshold", 3.0))
        return z > threshold

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._channel_counts = {str(k): int(v) for k, v in (data.get("channel_counts") or {}).items()}
            raw_samples = data.get("view_count_samples") or []
            self._view_count_samples = [int(v) for v in raw_samples][-self._SAMPLE_CAP :]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def _save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(
                    {
                        "channel_counts": self._channel_counts,
                        "view_count_samples": self._view_count_samples,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            import os
            os.replace(tmp, self.state_path)
        except OSError:
            tmp.unlink(missing_ok=True)


def split_records(
    records: Iterable[Dict[str, Any]],
    video_filter: VideoFilter,
) -> Tuple[list[Dict[str, Any]], list[tuple[Dict[str, Any], str]]]:
    accepted: list[Dict[str, Any]] = []
    rejected: list[tuple[Dict[str, Any], str]] = []
    for record in records:
        decision = video_filter.decide(record)
        if decision.accepted:
            video_filter.accept(record)
            accepted.append(record)
        else:
            rejected.append((record, decision.reason))
    return accepted, rejected
