from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class CategoryConfig(BaseModel):
    name: str
    keywords: List[str] = Field(default_factory=list)
    target_count: int = 5500
    collect_count: int = 6000
    platform_weights: Dict[str, float] = Field(default_factory=lambda: {"youtube": 1.0})
    filters: Dict[str, Any] = Field(default_factory=dict)
    youtube_relevance_languages: List[str] = Field(
        default_factory=list,
        description="Optional YouTube search relevanceLanguage values cycled per keyword.",
    )
    youtube_region_codes: List[str] = Field(
        default_factory=list,
        description="Optional YouTube search regionCode values cycled per keyword.",
    )

    @validator("keywords")
    def require_keywords(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("category must include at least one keyword")
        return value


class BalancerFieldConfig(BaseModel):
    coefficient: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Strictness for this field. 0 disables the field; 1 is strongest balancing.",
    )
    buckets: Optional[List[List[Optional[int]]]] = None
    targets: Any = "uniform"
    unknown_policy: str = "normal"
    unknown_max_share: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_share: Optional[float] = Field(None, ge=0.0, le=1.0)

    @validator("buckets")
    def validate_buckets(
        cls,
        value: Optional[List[List[Optional[int]]]],
    ) -> Optional[List[List[Optional[int]]]]:
        if value is None:
            return value
        for bucket in value:
            if len(bucket) != 2:
                raise ValueError("balancer buckets must be [min, max]")
            low, high = bucket
            if low is not None and high is not None and low > high:
                raise ValueError("balancer bucket min must be <= max")
        return value


class BalancerConfig(BaseModel):
    enabled: bool = False
    mode: str = "mixed"
    default_action: str = "accept"
    min_accept_score: float = Field(0.35, ge=0.0, le=1.0)
    random_seed: Optional[int] = None
    fields: Dict[str, BalancerFieldConfig] = Field(default_factory=dict)
    post_enrich_fields: Dict[str, BalancerFieldConfig] = Field(default_factory=dict)

    @validator("mode")
    def validate_mode(cls, value: str) -> str:
        if value not in {"mixed", "soft", "hard"}:
            raise ValueError("balancer mode must be mixed, soft, or hard")
        return value

    @validator("default_action")
    def validate_default_action(cls, value: str) -> str:
        if value not in {"accept", "reject"}:
            raise ValueError("balancer default_action must be accept or reject")
        return value


class CampaignConfig(BaseModel):
    name: str
    output_dir: str
    campaign_profile: Optional[str] = Field(
        None,
        description="Human-readable collection profile name, e.g. dataset-20k-colab.",
    )
    sampling_policy_version: Optional[str] = Field(
        None,
        description="Version tag for discover/sampling policy used by this run.",
    )
    balancer_policy_version: Optional[str] = Field(
        None,
        description="Version tag for the balancer policy used by this run.",
    )
    baseline_accepted: int = Field(
        0,
        description="Videos already collected in prior datasets (for progress totals and dedup context).",
    )
    categories: List[CategoryConfig]
    snapshot_schedule_days: List[int] = Field(default_factory=lambda: [0, 7, 14, 21])
    snapshot_schedule_hours: Optional[List[int]] = Field(
        None,
        description=(
            "If set, follow-up snapshot due times use hours from snapshot_0 (smoke tests). "
            "Overrides snapshot_schedule_days unless snapshot_schedule_minutes is set."
        ),
    )
    snapshot_schedule_minutes: Optional[List[int]] = Field(
        None,
        description=(
            "If set, follow-up snapshot due times use minutes from snapshot_0 (fast smoke). "
            "Overrides snapshot_schedule_hours and snapshot_schedule_days unless "
            "snapshot_sleep_seconds is set."
        ),
    )
    snapshot_sleep_seconds: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Per-video fixed interval: snapshot index N is due at "
            "snapshot_0.collected_at + N * snapshot_sleep_seconds."
        ),
    )
    snapshot_follow_up_count: Optional[int] = Field(
        None,
        ge=1,
        description="Number of follow-up snapshots after snapshot_0 (indices 1..N).",
    )
    default_filters: Dict[str, Any] = Field(default_factory=dict)
    platform_weights: Dict[str, float] = Field(default_factory=lambda: {"youtube": 1.0})
    time_interval_buckets: List[Dict[str, Any]] = Field(default_factory=list)
    balancer_config_file: Optional[str] = None
    balancer_config: Optional[BalancerConfig] = Field(
        None,
        exclude=True,
        description="Runtime-loaded balancer config from balancer_config_file.",
    )
    shard_size: int = 100
    comments_per_snapshot: int = 100
    min_videos_per_keyword: int = Field(
        20,
        description="Minimum unique accepted videos required per keyword before moving on.",
    )
    keyword_search_multiplier: int = Field(
        10,
        description="Search budget per keyword = min_videos_per_keyword * multiplier (covers dupes/filters).",
    )
    hf_repo_id: Optional[str] = None
    hf_shards_repo_id: Optional[str] = Field(
        None,
        description="HF dataset repo for metadata shards; defaults to hf_repo_id.",
    )
    hf_videos_repo_id: Optional[str] = Field(
        None,
        description="HF dataset repo for video files; defaults to hf_repo_id.",
    )
    hf_enrich_repo_id: Optional[str] = Field(
        None,
        description="HF dataset repo for enrich shards; defaults to hf_repo_id.",
    )
    hf_token_env: str = "HF_TOKEN"
    hf_upload_enabled: bool = False
    hf_upload_every_shards: int = 10
    hf_parallel_colab_count: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "How many Colab instances upload to the same HF repos at once (you set this). "
            "Divides the Hub per-repo commit budget; also via env HF_PARALLEL_COLAB_COUNT."
        ),
    )
    hf_repo_hourly_commit_limit: int = Field(
        128,
        ge=1,
        description="Hugging Face commits/hour cap per dataset repo (Hub default 128).",
    )
    hf_commit_budget_reserve: float = Field(
        0.9,
        gt=0.0,
        le=1.0,
        description="Fraction of hf_repo_hourly_commit_limit to use when splitting across Colabs.",
    )
    hf_commit_limits_manual: bool = Field(
        False,
        description=(
            "If true, keep hf_commit_min_interval_seconds and hf_commit_hourly_limit as written "
            "even when hf_parallel_colab_count > 1."
        ),
    )
    hf_coord_upload_min_interval_seconds: Optional[int] = Field(
        None,
        ge=30,
        description=(
            "Minimum seconds between batched HF uploads of coord claims/done files. "
            "Auto from parallel Colab count if unset."
        ),
    )
    hf_commit_min_interval_seconds: int = Field(
        37,
        description="Minimum delay between commits per HF repo (37s keeps below 100 commits/hour).",
    )
    hf_commit_hourly_limit: int = Field(
        100,
        ge=1,
        description="Rolling per-repo commit cap over the last hour (single Colab default).",
    )
    hf_shard_upload_batch_files: int = 25
    hf_video_upload_batch_files: int = 25
    hf_enrich_upload_batch_files: int = 25
    hf_path_prefix: str = ""
    hf_shards_path_prefix: str = Field(
        "shards/metadata",
        description="Path prefix inside HF repo for metadata shard JSON files.",
    )
    hf_videos_path_prefix: str = Field(
        "videos",
        description="Path prefix inside HF repo for downloaded mp4 files.",
    )
    hf_enrich_path_prefix: str = Field(
        "enrich",
        description="Path prefix inside HF repo for yt-dlp enrich payloads.",
    )
    cookie_files_dir: Optional[str] = None
    cookie_file_glob: str = "*.txt"
    youtube_keys_file: Optional[str] = None
    tiktok_credentials_file: Optional[str] = None
    instagram_credentials_file: Optional[str] = None
    twitch_credentials_file: Optional[str] = None
    rutube_credentials_file: Optional[str] = None
    credentials_dir: Optional[str] = Field(
        None,
        description="Override FETCHER_CREDENTIALS_DIR for dataset collector runs.",
    )
    proxies_file: Optional[str] = None
    proxy_default_scheme: str = "http"
    include_local_proxies_for_discovery: bool = False
    use_proxies_for_discovery: bool = Field(
        True,
        description=(
            "Use proxies from proxies.txt for YouTube Data API (discover) and yt-dlp (enrich). "
            "Does not affect download_only/nodpi lines used only by pytubefix download."
        ),
    )
    youtube_relevance_languages: List[str] = Field(
        default_factory=list,
        description="Global YouTube search relevanceLanguage values cycled per keyword.",
    )
    youtube_region_codes: List[str] = Field(
        default_factory=list,
        description="Global YouTube search regionCode values cycled per keyword.",
    )
    queue_max_attempts: int = Field(
        5,
        ge=1,
        description="Queue item failures before moving to dead letter.",
    )
    queue_retry_backoff_seconds: int = Field(
        900,
        ge=0,
        description="Base retry backoff recorded for failed queue items.",
    )
    download_backend: str = Field(
        "pytubefix",
        description="Video download backend: pytubefix, yt_dlp, or yt_dlp_first.",
    )
    download_ytdlp_format: str = Field(
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        description="yt-dlp format selector for mp4 downloads.",
    )
    download_ytdlp_player_clients: List[str] = Field(
        default_factory=lambda: ["android", "web"],
        description="YouTube player clients passed to yt-dlp extractor args.",
    )
    download_cookie_rotate_successes: int = Field(
        20,
        ge=1,
        description="Rotate to the next cookie file after this many successful downloads.",
    )
    download_pytubefix_clients: List[str] = Field(
        default_factory=lambda: ["ANDROID_VR", "WEB"],
        description="pytubefix clients tried in order; WEB can generate PO tokens via Node.js.",
    )
    download_pause_after_success_seconds: float = Field(
        10.0,
        ge=0.0,
        description="Sleep after a successful download before the next queue item.",
    )
    download_pause_after_fail_seconds: float = Field(
        15.0,
        ge=0.0,
        description="Sleep after a failed download (non-bot, non-unavailable).",
    )
    download_pause_after_unavailable_seconds: float = Field(
        15.0,
        ge=0.0,
        description="Sleep after VideoUnavailable (no retry benefit).",
    )
    download_pause_after_cookie_bot_seconds: float = Field(
        120.0,
        ge=0.0,
        description="Legacy alias; bot waits use download_pause_after_bot_seconds (2 min default).",
    )
    download_pause_after_bot_seconds: float = Field(
        120.0,
        ge=0.0,
        description="Fixed sleep on bot_detection (retry same video until cleared).",
    )
    download_pause_after_bot_max_seconds: float = Field(
        120.0,
        ge=0.0,
        description="Deprecated cap; bot pause is fixed at download_pause_after_bot_seconds.",
    )
    download_pause_bot_backoff_multiplier: float = Field(
        1.0,
        ge=1.0,
        description="Deprecated; bot pause no longer escalates between queue items.",
    )
    download_fast_threshold_seconds: float = Field(
        8.0,
        ge=0.0,
        description="If download finishes faster than this, apply download_pause_after_fast_seconds.",
    )
    download_pause_after_fast_seconds: float = Field(
        15.0,
        ge=0.0,
        description="Sleep after suspiciously fast download or generic fail/unavailable pacing.",
    )
    drive_permanent_delete: Optional[bool] = Field(
        None,
        description=(
            "When output is on Google Drive (e.g. Colab), delete uploaded videos via Drive API "
            "instead of unlink (which sends files to Trash). None = auto when output_dir is on Drive."
        ),
    )
    hf_progress_enabled: bool = Field(
        True,
        description="Sync campaign progress files to hf_repo_id for Colab session resume.",
    )
    hf_progress_repo_id: Optional[str] = Field(
        None,
        description="HF dataset repo for progress files (default: hf_repo_id).",
    )
    hf_progress_path_prefix: str = Field(
        "state/progress",
        description="Remote path prefix for progress bundle inside hf_progress_repo_id.",
    )
    discover_week_days: int = Field(
        0,
        ge=0,
        description=(
            "Snapshot_0 discover: run daily sessions for this many calendar days (0 = no limit). "
            "Each session stops on API quota; progress restored from HF on restart."
        ),
    )
    discover_fair_rotation: bool = Field(
        False,
        description=(
            "Round-robin discover across categories (one keyword batch per category per round) "
            "instead of filling each category sequentially."
        ),
    )
    discover_target_total: Optional[int] = Field(
        None,
        ge=1,
        description="Soft total discover target; used to compute per-category fair cap when rotation is on.",
    )
    discover_fair_category_cap: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Max accepted videos per category during fair rotation "
            "(default: ceil(discover_target_total / num_categories) or collect_count)."
        ),
    )
    hf_coord_repo_id: Optional[str] = Field(
        None,
        description="HF dataset repo for multi-Colab coordination (default: hf_shards_repo_id).",
    )
    hf_coord_enabled: bool = Field(
        False,
        description=(
            "Multi-Colab: sync metadata/claims/done via HF shards repo so download/enrich workers "
            "do not process the same video_id."
        ),
    )
    hf_coord_path_prefix: str = Field(
        "state/coordination",
        description="Path prefix inside hf_shards_repo_id for claims and per-worker done files.",
    )
    hf_coord_claim_ttl_seconds: int = Field(
        7200,
        ge=300,
        description="Active claim TTL; expired claims can be taken by another worker.",
    )
    worker_id: Optional[str] = Field(
        None,
        description="Unique label for this Colab/process (claims file name on HF).",
    )
    worker_shard_index: Optional[int] = Field(
        None,
        ge=0,
        description="Optional static shard index 0..worker_shard_count-1 (hash of video key).",
    )
    worker_shard_count: Optional[int] = Field(
        None,
        ge=1,
        description="Number of parallel download/enrich Colabs; use with worker_shard_index.",
    )

    @validator("hf_token_env")
    def validate_hf_token_env_is_name(cls, value: str) -> str:
        name = (value or "HF_TOKEN").strip()
        if name.startswith("hf_") and len(name) > 20:
            raise ValueError(
                'hf_token_env must be an environment variable name (e.g. "HF_TOKEN"), '
                "not the Hugging Face token value"
            )
        return name

    @validator("snapshot_schedule_days")
    def schedule_starts_with_zero(cls, value: List[int]) -> List[int]:
        if not value or value[0] != 0:
            raise ValueError("snapshot_schedule_days must start with 0")
        return value

    @validator("snapshot_schedule_hours")
    def schedule_hours_starts_with_zero(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is not None and (not value or value[0] != 0):
            raise ValueError("snapshot_schedule_hours must start with 0")
        return value

    @validator("snapshot_schedule_minutes")
    def schedule_minutes_starts_with_zero(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is not None and (not value or value[0] != 0):
            raise ValueError("snapshot_schedule_minutes must start with 0")
        return value


class Snapshot(BaseModel):
    snapshot_index: int
    time_get: str
    collected_at: datetime
    viewCount: Optional[str] = None
    likeCount: Optional[str] = None
    commentCount: Optional[str] = None
    subscriberCount: Optional[int] = None
    videoCount: Optional[int] = None
    viewCount_channel: Optional[int] = None
    comments: List[Dict[str, Any]] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)


def compact_follow_up_snapshot(snapshot: "Snapshot") -> "Snapshot":
    """Follow-up snapshots keep only time-varying metrics (static fields live in snapshot_0)."""
    if snapshot.snapshot_index <= 0:
        return snapshot
    return Snapshot(
        snapshot_index=snapshot.snapshot_index,
        time_get=snapshot.time_get,
        collected_at=snapshot.collected_at,
        viewCount=snapshot.viewCount,
        likeCount=snapshot.likeCount,
        commentCount=snapshot.commentCount,
        subscriberCount=snapshot.subscriberCount,
        videoCount=snapshot.videoCount,
        viewCount_channel=snapshot.viewCount_channel,
        comments=snapshot.comments,
        raw={},
    )


class CollectedVideo(BaseModel):
    platform: str
    video_id: str
    url: str
    category: str
    query: str
    metadata: Dict[str, Any]
    snapshot_0: Snapshot
    time_interval: Optional[str] = None
    discovered_at: datetime
    platform_capabilities: Dict[str, bool] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{self.platform}:{self.video_id}"


class RejectedRecord(BaseModel):
    platform: str
    video_id: str
    category: str
    query: str
    reason: str
    record: Dict[str, Any] = Field(default_factory=dict)
    rejected_at: datetime


class ScheduleEntry(BaseModel):
    platform: str
    video_id: str
    category: str
    url: str
    baseline_collected_at: datetime
    due_at: Dict[str, datetime]
    completed: Dict[str, datetime] = Field(default_factory=dict)
    status: str = "pending"

    @property
    def dedup_key(self) -> str:
        return f"{self.platform}:{self.video_id}"


class CampaignManifest(BaseModel):
    name: str
    created_at: datetime
    updated_at: datetime
    output_dir: str
    campaign_profile: Optional[str] = None
    sampling_policy_version: Optional[str] = None
    balancer_policy_version: Optional[str] = None
    baseline_accepted: int = 0
    legacy_seen_imported: int = 0
    session_started_at: Optional[datetime] = None
    session_counters: Dict[str, int] = Field(default_factory=dict)
    counters: Dict[str, int] = Field(default_factory=dict)
    category_counters: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    shards: List[str] = Field(default_factory=list)
    snapshot_shards: List[str] = Field(default_factory=list)
    rejected_shards: List[str] = Field(default_factory=list)
