from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from fetcher.dataset_collector.age_buckets import DEFAULT_TIME_INTERVAL_BUCKETS
from fetcher.dataset_collector.schemas import BalancerConfig, CampaignConfig, CategoryConfig
from fetcher.dataset_collector.state import jsonable


DEFAULT_CATEGORY_NAMES = [
    "sports",
    "travel",
    "education",
    "science",
    "technology",
    "gaming",
    "music",
    "movies",
    "news",
    "comedy",
    "food",
    "fashion",
    "beauty",
    "fitness",
    "business",
    "finance",
    "cars",
    "pets",
]


def default_campaign_config(
    *,
    name: str = "dataset-100k",
    output_dir: str = "dataset_runs/dataset-100k",
    categories: Iterable[str] | None = None,
) -> CampaignConfig:
    category_names = list(categories or DEFAULT_CATEGORY_NAMES)
    return CampaignConfig(
        name=name,
        output_dir=output_dir,
        baseline_accepted=0,
        categories=[
            CategoryConfig(
                name=category,
                keywords=[category],
                target_count=5500,
                collect_count=6000,
                platform_weights={"youtube": 1.0},
            )
            for category in category_names
        ],
        default_filters={
            "duration_min_seconds": 10,
            "duration_max_seconds": 3600,
            "view_count_max": 100_000_000,
            "channel_video_cap": 100,
            "outlier_policy": "reject",
        },
        platform_weights={"youtube": 0.75, "tiktok": 0.15, "twitch": 0.05, "rutube": 0.05},
        time_interval_buckets=[
            {
                "name": bucket.name,
                "min_age_days": bucket.min_age_days,
                "max_age_days": bucket.max_age_days,
                "weight": bucket.weight,
            }
            for bucket in DEFAULT_TIME_INTERVAL_BUCKETS
        ],
        snapshot_schedule_days=[0, 7, 14, 21, 28],
        hf_upload_enabled=False,
        hf_upload_every_shards=10,
        hf_commit_min_interval_seconds=37,
        hf_shard_upload_batch_files=25,
        hf_video_upload_batch_files=25,
        hf_enrich_upload_batch_files=25,
        hf_shards_path_prefix="shards/metadata",
        hf_videos_path_prefix="videos",
        hf_enrich_path_prefix="enrich",
        youtube_keys_file="fetcher/credentials/youtube_keys.txt",
        tiktok_credentials_file="fetcher/credentials/tiktok.json",
        instagram_credentials_file="fetcher/credentials/instagram.json",
        twitch_credentials_file="fetcher/credentials/twitch.json",
        rutube_credentials_file="fetcher/credentials/rutube.json",
        credentials_dir="fetcher/credentials",
        proxies_file="fetcher/dataset_collector/proxies/proxies.txt",
        cookie_files_dir="fetcher/dataset_collector/cookies",
        cookie_file_glob="*.txt",
        proxy_default_scheme="http",
        include_local_proxies_for_discovery=False,
        use_proxies_for_discovery=True,
    )


def load_campaign_config(path: str | Path) -> CampaignConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    config = CampaignConfig.parse_obj(data)
    if config.balancer_config_file:
        config.balancer_config = load_balancer_config(
            config.balancer_config_file,
            base_dir=config_path.parent,
        )
    from fetcher.dataset_collector.keyword_presets import apply_keyword_presets

    return apply_keyword_presets(config)


def load_balancer_config(path: str | Path, *, base_dir: str | Path | None = None) -> BalancerConfig:
    config_path = Path(path)
    if not config_path.is_absolute() and base_dir is not None:
        candidate = Path(base_dir) / config_path
        if candidate.exists():
            config_path = candidate
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return BalancerConfig.parse_obj(data)


def write_campaign_template(path: str | Path, *, overwrite: bool = False) -> Path:
    config_path = Path(path)
    if config_path.exists() and not overwrite:
        raise FileExistsError(f"Campaign config already exists: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = default_campaign_config()
    from fetcher.dataset_collector.keyword_presets import apply_keyword_presets

    config = apply_keyword_presets(config)
    config_path.write_text(
        json.dumps(jsonable(config.dict()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


def merged_filters(config: CampaignConfig, category: CategoryConfig) -> dict:
    merged = dict(config.default_filters)
    merged.update(category.filters)
    return merged


__all__ = [
    "CampaignConfig",
    "CategoryConfig",
    "BalancerConfig",
    "DEFAULT_CATEGORY_NAMES",
    "default_campaign_config",
    "load_balancer_config",
    "load_campaign_config",
    "merged_filters",
    "write_campaign_template",
]
