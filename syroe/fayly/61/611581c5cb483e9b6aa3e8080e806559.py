from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from fetcher.dataset_collector.age_buckets import allocate_counts, bucket_from_config
from fetcher.dataset_collector.balancer import DatasetBalancer
from fetcher.dataset_collector.checkpoint import DiscoveryCheckpoint
from fetcher.dataset_collector.keyword_progress import KeywordProgressEntry
from fetcher.dataset_collector.config import merged_filters
from fetcher.dataset_collector.discovery.base import DiscoveryAdapter
from fetcher.dataset_collector.filters import VideoFilter
from fetcher.dataset_collector.metrics import (
    dataset_collector_videos_accepted_total,
    dataset_collector_videos_rejected_total,
    observe_video_metrics,
    record_balancer_decision,
    record_reject_reason,
    update_gauges,
)
from fetcher.dataset_collector.progress import ProgressReporter
from fetcher.dataset_collector.schemas import CampaignConfig, CategoryConfig, CollectedVideo, RejectedRecord
from fetcher.dataset_collector.snapshots import build_schedule_entry
from fetcher.dataset_collector.state import DatasetState, jsonable, utcnow
from fetcher.services.youtube_data_client import QuotaExceededError, is_comments_disabled_error


class DatasetCollector:
    def __init__(
        self,
        config: CampaignConfig,
        state: DatasetState,
        adapters: Dict[str, DiscoveryAdapter],
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.adapters = adapters
        self.progress = progress
        self.balancer = DatasetBalancer(
            config.balancer_config,
            state_root=state.root,
            campaign_config=config,
        )

    def discover_campaign(
        self,
        category_names: List[str],
        *,
        limit: int | None = None,
    ) -> dict[str, int]:
        if self.config.discover_fair_rotation:
            return self._discover_campaign_fair(category_names, limit=limit)
        total = {"accepted": 0, "rejected": 0}
        remaining_global = limit
        for category_name in category_names:
            if remaining_global is not None and remaining_global <= 0:
                print(f"discover global limit reached ({limit} accepted total), stopping")
                break
            category = next(item for item in self.config.categories if item.name == category_name)
            if limit is None and self.state.is_category_complete(category.name, category.collect_count):
                print(
                    f"{category.name}: skip "
                    f"({self.state.category_accepted(category.name)}/{category.collect_count})"
                )
                continue
            cat_limit = remaining_global if remaining_global is not None else None
            try:
                result = self.discover_category(category.name, limit=cat_limit)
            except QuotaExceededError:
                self.state.flush_all_pending(shard_size=self.config.shard_size)
                raise
            total["accepted"] += result["accepted"]
            total["rejected"] += result["rejected"]
            if remaining_global is not None:
                remaining_global -= result["accepted"]
            print(f"{category.name}: accepted={result['accepted']} rejected={result['rejected']}")
        return total

    def _fair_category_cap(self, categories: List[CategoryConfig]) -> int:
        explicit = self.config.discover_fair_category_cap
        if explicit is not None:
            return int(explicit)
        total_target = self.config.discover_target_total
        if total_target is None:
            total_target = sum(cat.target_count for cat in categories)
        if not total_target:
            total_target = sum(cat.collect_count for cat in categories)
        return max(1, (int(total_target) + len(categories) - 1) // len(categories))

    def _discover_campaign_fair(
        self,
        category_names: List[str],
        *,
        limit: int | None = None,
    ) -> dict[str, int]:
        categories = [
            cat
            for name in category_names
            for cat in self.config.categories
            if cat.name == name
        ]
        fair_cap = self._fair_category_cap(categories)
        min_kw = max(self.config.min_videos_per_keyword, 1)
        total = {"accepted": 0, "rejected": 0}

        def category_complete(category: CategoryConfig) -> bool:
            return self.state.category_accepted(category.name) >= fair_cap

        print(
            f"discover fair rotation: {len(categories)} categories, "
            f"cap={fair_cap}/category, batch={min_kw}/keyword/round"
        )

        while True:
            active = [cat for cat in categories if not category_complete(cat)]
            if not active:
                break
            if limit is not None and total["accepted"] >= limit:
                print(f"discover global limit reached ({limit} accepted total), stopping")
                break

            round_accepted = 0
            for category in active:
                if limit is not None and total["accepted"] >= limit:
                    break
                remaining = fair_cap - self.state.category_accepted(category.name)
                if remaining <= 0:
                    continue
                try:
                    result = self.discover_category(
                        category.name,
                        limit=min(min_kw, remaining),
                        max_keywords=1,
                    )
                except QuotaExceededError:
                    self.state.flush_all_pending(shard_size=self.config.shard_size)
                    raise
                total["accepted"] += result["accepted"]
                total["rejected"] += result["rejected"]
                round_accepted += result["accepted"]
                print(
                    f"{category.name}: +{result['accepted']} "
                    f"({self.state.category_accepted(category.name)}/{fair_cap})"
                )

            if round_accepted == 0:
                print("discover fair rotation: no progress this round, stopping")
                break

        return total

    def discover_category(
        self,
        category_name: str,
        *,
        limit: int | None = None,
        max_keywords: int | None = None,
    ) -> dict[str, int]:
        category = next(item for item in self.config.categories if item.name == category_name)
        already = self.state.category_accepted(category.name)
        remaining_target = max(0, category.collect_count - already)
        if remaining_target <= 0:
            self.state.clear_checkpoint()
            return {"accepted": 0, "rejected": 0}

        target = remaining_target if limit is None else min(limit, remaining_target)
        if target <= 0:
            return {"accepted": 0, "rejected": 0}

        checkpoint = self._resolve_checkpoint(category)
        session_accepted = 0
        session_rejected = 0
        platform_bucket_accepted: Dict[Tuple[str, str | None], int] = {}
        video_filter = VideoFilter(
            merged_filters(self.config, category),
            state_path=self.state.channel_counts_path,
        )
        buckets = [bucket_from_config(raw) for raw in self.config.time_interval_buckets]
        bucket_targets = allocate_counts(buckets, target) if buckets else {None: target}
        bucket_list = buckets or [None]
        start_bucket_idx = self._bucket_index(bucket_list, checkpoint.bucket_name if checkpoint else None)
        keywords_processed = 0

        try:
            for bucket in bucket_list[start_bucket_idx:]:
                bucket_name = bucket.name if bucket else None
                bucket_target = bucket_targets.get(bucket_name, target)
                if bucket_target <= 0:
                    continue
                published_after, published_before = bucket.published_window() if bucket else (None, None)
                bucket_accepted = 0

                platform_items = list(self._platform_limits(category.platform_weights, bucket_target))
                start_platform_idx = 0
                if checkpoint and checkpoint.bucket_name == bucket_name:
                    start_platform_idx = self._platform_index(platform_items, checkpoint.platform)

                for platform, platform_limit in platform_items[start_platform_idx:]:
                    adapter = self.adapters.get(platform)
                    if adapter is None or not adapter.capabilities.search:
                        continue

                    start_keyword_idx = 0
                    if (
                        checkpoint
                        and checkpoint.bucket_name == bucket_name
                        and checkpoint.platform == platform
                    ):
                        start_keyword_idx = checkpoint.keyword_index

                    completed_keywords = self.state.load_completed_keyword_indices(
                        category=category.name,
                        bucket_name=bucket_name,
                        platform=platform,
                    )

                    for keyword_idx, keyword in enumerate(
                        category.keywords[start_keyword_idx:],
                        start=start_keyword_idx,
                    ):
                        if keyword_idx in completed_keywords:
                            if self.progress:
                                self.progress.log_keyword_skip(
                                    category=category.name,
                                    keyword_index=keyword_idx,
                                    keywords_total=len(category.keywords),
                                    keyword=keyword,
                                )
                            continue
                        if bucket_accepted >= bucket_target:
                            break
                        if self.state.category_accepted(category.name) - already >= target:
                            break

                        platform_key = (platform, bucket_name)
                        platform_accepted = platform_bucket_accepted.get(platform_key, 0)
                        remaining = platform_limit - platform_accepted
                        if remaining <= 0:
                            self._save_keyword_checkpoint(
                                category, bucket_name, platform, keyword_idx, keyword
                            )
                            continue

                        self._save_keyword_checkpoint(
                            category, bucket_name, platform, keyword_idx, keyword
                        )
                        min_kw = self.config.min_videos_per_keyword
                        search_limit = max(
                            remaining,
                            min_kw * max(self.config.keyword_search_multiplier, 1),
                        )
                        keyword_accepted = 0
                        keyword_scanned = 0
                        keyword_dup = 0
                        keyword_filtered = 0
                        if self.progress:
                            self.progress.log_keyword_start(
                                category=category.name,
                                category_accepted=self.state.live_category_accepted(category.name),
                                category_target=category.collect_count,
                                keyword_index=keyword_idx,
                                keywords_total=len(category.keywords),
                                keyword=keyword,
                                min_unique=min_kw,
                            )

                        try:
                            discover_kwargs = {
                                "category": category.name,
                                "query": keyword,
                                "limit": search_limit,
                                "published_after": published_after,
                                "published_before": published_before,
                                "time_interval": bucket_name,
                            }
                            if platform == "youtube":
                                discover_kwargs.update(
                                    self._youtube_search_params(
                                        category,
                                        keyword_index=keyword_idx,
                                    )
                                )
                            for video in adapter.discover(**discover_kwargs):
                                keyword_scanned += 1
                                if bucket_accepted >= bucket_target:
                                    break
                                if self.state.live_category_accepted(category.name) - already >= target:
                                    break

                                if self.state.is_seen(video.dedup_key):
                                    keyword_dup += 1
                                    session_rejected += self._handle_reject(
                                        video, "duplicate_seen"
                                    )
                                    continue

                                decision = video_filter.decide(jsonable(video.dict()))
                                if not decision.accepted:
                                    keyword_filtered += 1
                                    session_rejected += self._handle_reject(
                                        video, decision.reason or "rejected"
                                    )
                                    continue

                                balancer_decision = self.balancer.decide(video)
                                if self.balancer.enabled:
                                    fill_ratios = {
                                        (field, value): self.balancer.bucket_fill_ratio(field, value)
                                        for field, value in balancer_decision.field_values.items()
                                    }
                                    record_balancer_decision(
                                        category=video.category,
                                        accepted=balancer_decision.accepted,
                                        reason=balancer_decision.reason,
                                        score=balancer_decision.score,
                                        field_scores=balancer_decision.field_scores,
                                        field_values=balancer_decision.field_values,
                                        fill_ratios=fill_ratios,
                                    )
                                if not balancer_decision.accepted:
                                    keyword_filtered += 1
                                    session_rejected += self._handle_reject(
                                        video,
                                        balancer_decision.reason or "balancer_rejected",
                                    )
                                    continue

                                self._attach_initial_comments(adapter, video)
                                video_filter.accept(jsonable(video.dict()))
                                self.balancer.observe_accept(video)
                                self.state.mark_seen(video.dedup_key, category=category.name)
                                self.state.append_schedule(
                                    build_schedule_entry(
                                        video,
                                        self.config.snapshot_schedule_days,
                                        schedule_hours=self.config.snapshot_schedule_hours,
                                        schedule_minutes=self.config.snapshot_schedule_minutes,
                                        snapshot_sleep_seconds=self.config.snapshot_sleep_seconds,
                                        snapshot_follow_up_count=self.config.snapshot_follow_up_count,
                                    )
                                )
                                self.state.enqueue_download(video)
                                self.state.buffer_accepted(category.name, video)
                                session_accepted += 1
                                bucket_accepted += 1
                                keyword_accepted += 1
                                platform_bucket_accepted[platform_key] = (
                                    platform_bucket_accepted.get(platform_key, 0) + 1
                                )
                                self._on_accepted(video)

                                written = self.state.flush_pending(
                                    category.name,
                                    shard_size=self.config.shard_size,
                                )
                                self._log_progress(
                                    category,
                                    keyword_index=keyword_idx,
                                    keyword=keyword,
                                    keyword_accepted=keyword_accepted,
                                    keyword_min=min_kw,
                                )

                                if keyword_accepted >= min_kw:
                                    break

                        except QuotaExceededError:
                            self.state.flush_all_pending(shard_size=self.config.shard_size)
                            self._save_keyword_checkpoint(
                                category, bucket_name, platform, keyword_idx, keyword
                            )
                            raise

                        self._record_keyword_progress(
                            category=category.name,
                            bucket_name=bucket_name,
                            platform=platform,
                            keyword_index=keyword_idx,
                            keyword=keyword,
                            accepted=keyword_accepted,
                            min_required=min_kw,
                            scanned=keyword_scanned,
                            duplicate=keyword_dup,
                            rejected=keyword_filtered,
                        )
                        if self.progress:
                            self.progress.log_keyword_done(
                                category=category.name,
                                category_accepted=self.state.live_category_accepted(category.name),
                                category_target=category.collect_count,
                                keyword_index=keyword_idx,
                                keywords_total=len(category.keywords),
                                keyword=keyword,
                                keyword_accepted=keyword_accepted,
                                keyword_min=min_kw,
                                keyword_scanned=keyword_scanned,
                                keyword_dup=keyword_dup,
                                keyword_rejected=keyword_filtered,
                                warn=keyword_accepted < min_kw,
                            )

                        next_keyword_idx = keyword_idx + 1
                        if next_keyword_idx < len(category.keywords):
                            self._save_keyword_checkpoint(
                                category,
                                bucket_name,
                                platform,
                                next_keyword_idx,
                                category.keywords[next_keyword_idx],
                            )

                        keywords_processed += 1
                        if max_keywords is not None and keywords_processed >= max_keywords:
                            break

                        if bucket_accepted >= bucket_target:
                            break
                    if max_keywords is not None and keywords_processed >= max_keywords:
                        break
                    if bucket_accepted >= bucket_target:
                        break
                if max_keywords is not None and keywords_processed >= max_keywords:
                    break
                if self.state.live_category_accepted(category.name) - already >= target:
                    break
        finally:
            written = self.state.flush_all_pending(shard_size=self.config.shard_size)
            video_filter.flush_state()
            self.balancer.flush_snapshot()
        if self.state.live_category_accepted(category.name) >= category.collect_count:
            self.state.clear_checkpoint()

        return {"accepted": session_accepted, "rejected": session_rejected}

    def _resolve_checkpoint(self, category: CategoryConfig) -> DiscoveryCheckpoint | None:
        checkpoint = self.state.load_checkpoint()
        if checkpoint is None or checkpoint.category != category.name:
            return None
        return checkpoint

    def _youtube_search_params(self, category: CategoryConfig, *, keyword_index: int) -> dict[str, str]:
        languages = category.youtube_relevance_languages or self.config.youtube_relevance_languages
        regions = category.youtube_region_codes or self.config.youtube_region_codes
        params: dict[str, str] = {}
        if languages:
            params["relevance_language"] = languages[keyword_index % len(languages)]
        if regions:
            params["region_code"] = regions[keyword_index % len(regions)]
        return params

    @staticmethod
    def _bucket_index(buckets: list, bucket_name: str | None) -> int:
        if bucket_name is None:
            return 0
        for index, bucket in enumerate(buckets):
            if bucket and bucket.name == bucket_name:
                return index
        return 0

    @staticmethod
    def _platform_index(platform_items: List[Tuple[str, int]], platform: str) -> int:
        for index, (name, _) in enumerate(platform_items):
            if name == platform:
                return index
        return 0

    def _save_keyword_checkpoint(
        self,
        category: CategoryConfig,
        bucket_name: str | None,
        platform: str,
        keyword_index: int,
        keyword: str,
    ) -> None:
        self.state.save_checkpoint(
            DiscoveryCheckpoint(
                category=category.name,
                bucket_name=bucket_name,
                platform=platform,
                keyword_index=keyword_index,
                keyword=keyword,
            )
        )

    def _record_keyword_progress(
        self,
        *,
        category: str,
        bucket_name: str | None,
        platform: str,
        keyword_index: int,
        keyword: str,
        accepted: int,
        min_required: int,
        scanned: int,
        duplicate: int,
        rejected: int,
    ) -> None:
        status = "done" if accepted >= min_required else "low"
        self.state.append_keyword_progress(
            KeywordProgressEntry(
                category=category,
                bucket_name=bucket_name,
                platform=platform,
                keyword_index=keyword_index,
                keyword=keyword,
                accepted=accepted,
                min_required=min_required,
                scanned=scanned,
                duplicate=duplicate,
                rejected=rejected,
                status=status,
            )
        )

    def _handle_reject(self, video: CollectedVideo, reason: str) -> int:
        self.state.buffer_rejected(self._reject(video, reason))
        if self.state.pending_rejected_count >= self.config.shard_size:
            self.state.flush_pending(video.category, shard_size=self.config.shard_size, force=True)
        dataset_collector_videos_rejected_total.labels(category=video.category, reason=reason).inc()
        record_reject_reason(video.category, reason)
        if self.progress:
            self.progress.record_reject()
            self.state.increment_session(rejected=1)
            update_gauges(self.progress.snapshot())
        return 1

    def _on_accepted(self, video: CollectedVideo) -> None:
        payload = jsonable(video.dict())
        dataset_collector_videos_accepted_total.labels(
            category=video.category,
            time_interval=video.time_interval or "unknown",
            platform=video.platform,
        ).inc()
        observe_video_metrics(payload, category=video.category)
        if self.progress:
            self.progress.record_accept()
            self.state.increment_session(accepted=1)
            update_gauges(self.progress.snapshot())

    def _log_progress(
        self,
        category: CategoryConfig,
        *,
        keyword_index: int,
        keyword: str,
        keyword_accepted: int = 0,
        keyword_min: int = 0,
        force: bool = False,
    ) -> None:
        if self.progress is None:
            return
        self.progress.log(
            category=category.name,
            category_accepted=self.state.live_category_accepted(category.name),
            category_target=category.collect_count,
            keyword_index=keyword_index,
            keywords_total=len(category.keywords),
            keyword=keyword,
            keyword_accepted=keyword_accepted,
            keyword_min=keyword_min,
            force=force,
        )

    @staticmethod
    def _platform_limits(weights: Dict[str, float], target: int) -> Iterable[Tuple[str, int]]:
        total_weight = sum(max(weight, 0) for weight in weights.values()) or 1.0
        for platform, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
            yield platform, max(1, int(target * max(weight, 0) / total_weight))

    def _attach_initial_comments(self, adapter: DiscoveryAdapter, video: CollectedVideo) -> None:
        if video.snapshot_0.comments or not adapter.capabilities.comments:
            return
        try:
            if int(video.snapshot_0.commentCount or 0) <= 0:
                return
        except (TypeError, ValueError):
            pass
        collect_comments = getattr(adapter, "collect_comments", None)
        if collect_comments is None:
            return
        try:
            video.snapshot_0.comments = collect_comments(
                video.video_id,
                comments_limit=self.config.comments_per_snapshot,
            )
        except Exception as exc:
            if is_comments_disabled_error(exc):
                video.snapshot_0.comments = []
                return
            print(
                f"[collector] WARNING: comments fetch failed for {video.video_id}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            video.snapshot_0.raw["comments_error"] = str(exc)[:500]
            video.snapshot_0.comments = []

    @staticmethod
    def _reject(video: CollectedVideo, reason: str) -> RejectedRecord:
        return RejectedRecord(
            platform=video.platform,
            video_id=video.video_id,
            category=video.category,
            query=video.query,
            reason=reason,
            record=jsonable(video.dict()),
            rejected_at=utcnow(),
        )
