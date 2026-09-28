from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fetcher.dataset_collector.schemas import CampaignConfig

# Hugging Face Hub: dataset repo commit cap (free tier; shared across all clients).
HF_HUB_DEFAULT_REPO_HOURLY_COMMIT_LIMIT = 128


@dataclass(frozen=True)
class HfCommitLimits:
    """Effective per-Colab HF commit throttle for one dataset repo."""

    parallel_colab_count: int
    repo_hourly_commit_limit: int
    min_interval_seconds: int
    hourly_limit_per_colab: int
    coord_upload_min_interval_seconds: int


def resolve_parallel_colab_count(config: CampaignConfig) -> int:
    """Colab instances sharing the same HF repos (user-provided)."""
    raw = config.hf_parallel_colab_count
    if raw is None:
        env = (os.getenv("HF_PARALLEL_COLAB_COUNT") or "").strip()
        if env.isdigit():
            raw = int(env)
    return max(int(raw or 1), 1)


def resolve_hf_commit_limits(config: CampaignConfig) -> HfCommitLimits:
    """
    Split the per-repo Hub commit budget across parallel Colab instances.

    When hf_parallel_colab_count > 1, derived limits override the single-Colab
    defaults (100/hour, 37s) unless hf_commit_limits_manual is true.
    """
    parallel = resolve_parallel_colab_count(config)
    repo_limit = max(int(config.hf_repo_hourly_commit_limit or HF_HUB_DEFAULT_REPO_HOURLY_COMMIT_LIMIT), 1)
    reserve = float(config.hf_commit_budget_reserve or 0.9)
    reserve = min(max(reserve, 0.5), 1.0)

    manual = bool(config.hf_commit_limits_manual)
    if manual or parallel <= 1:
        hourly = max(int(config.hf_commit_hourly_limit or 100), 1)
        interval = max(int(config.hf_commit_min_interval_seconds or 37), 0)
    else:
        shared_budget = max(1, int(repo_limit * reserve))
        hourly = max(1, shared_budget // parallel)
        interval = max(37, int(math.ceil(3600.0 / hourly)))

    coord_interval = config.hf_coord_upload_min_interval_seconds
    if coord_interval is None:
        coord_interval = max(120, interval * 2) if parallel > 1 else 60
    coord_interval = max(int(coord_interval), 30)

    return HfCommitLimits(
        parallel_colab_count=parallel,
        repo_hourly_commit_limit=repo_limit,
        min_interval_seconds=interval,
        hourly_limit_per_colab=hourly,
        coord_upload_min_interval_seconds=coord_interval,
    )


def format_hf_commit_limits_summary(limits: HfCommitLimits) -> str:
    return (
        f"HF commits: {limits.parallel_colab_count} Colab(s) share "
        f"{limits.repo_hourly_commit_limit}/h per repo → "
        f"≤{limits.hourly_limit_per_colab}/h here, "
        f"min interval {limits.min_interval_seconds}s, "
        f"coord upload every {limits.coord_upload_min_interval_seconds}s"
    )
