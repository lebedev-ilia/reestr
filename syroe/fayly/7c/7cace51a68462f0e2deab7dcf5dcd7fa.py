from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from fetcher.dataset_collector.discovery.base import DiscoveryCapabilities
from fetcher.dataset_collector.proxy import ProxyRotator
from fetcher.dataset_collector.schemas import CollectedVideo, Snapshot
from fetcher.dataset_collector.state import format_time_get, utcnow
from fetcher.services.youtube_data_client import (
    ChannelMetadataDto,
    QuotaExceededError,
    YouTubeAPIError,
    YouTubeDataClient,
    is_comments_disabled_error,
)


@dataclass
class YouTubeKeyState:
    api_key: str
    used_units: int = 0
    disabled_until: Optional[str] = None
    last_error: Optional[str] = None


class YouTubeKeyPool:
    def __init__(
        self,
        api_keys: Iterable[str],
        *,
        state_path: Path | None = None,
        daily_quota_limit: int = 10_000,
        proxy_rotator: ProxyRotator | None = None,
    ) -> None:
        self.state_path = state_path
        self.daily_quota_limit = daily_quota_limit
        self.proxy_rotator = proxy_rotator
        self.states: Dict[str, YouTubeKeyState] = {
            key: YouTubeKeyState(api_key=key) for key in api_keys if key
        }
        if not self.states:
            raise ValueError("YouTubeKeyPool requires at least one API key")
        self._load()

    def get_client(self) -> YouTubeDataClient:
        state = self._select_key()
        remaining = max(self.daily_quota_limit - state.used_units, 1)
        rotator = self.proxy_rotator
        return YouTubeDataClient(
            api_key=state.api_key,
            daily_quota_limit=remaining,
            proxy=rotator.next() if rotator else None,
            on_proxy_success=rotator.record_success if rotator else None,
            on_proxy_failure=rotator.record_failure if rotator else None,
        )

    def record_success(self, api_key: str, units: int | None = None) -> None:
        state = self.states[api_key]
        state.used_units += units or 0
        state.last_error = None
        self._save()

    def record_failure(self, api_key: str, error: Exception) -> None:
        if is_comments_disabled_error(error):
            return
        state = self.states[api_key]
        state.last_error = str(error)[:500]
        err_str = str(error)
        if isinstance(error, QuotaExceededError) or "quota" in err_str.lower():
            tomorrow = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
            state.disabled_until = tomorrow.isoformat()
        elif "429" in err_str or "403" in err_str:
            state.disabled_until = (utcnow() + timedelta(minutes=15)).isoformat()
        else:
            # Generic error (network issue, invalid key, etc.) — short backoff to
            # prevent tight loops. If persistent, all keys eventually get disabled
            # and _select_key raises QuotaExceededError, exiting the discover loop.
            state.disabled_until = (utcnow() + timedelta(minutes=2)).isoformat()
        self._save()

    def _select_key(self) -> YouTubeKeyState:
        now = utcnow()
        candidates = []
        for state in self.states.values():
            if state.disabled_until:
                disabled_until = datetime.fromisoformat(state.disabled_until)
                if disabled_until > now:
                    continue
            if state.used_units < self.daily_quota_limit:
                candidates.append(state)
        if not candidates:
            raise QuotaExceededError("All YouTube API keys are exhausted or disabled")
        return sorted(candidates, key=lambda item: item.used_units)[0]

    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        for key, raw in data.get("keys", {}).items():
            if key in self.states:
                self.states[key] = YouTubeKeyState(api_key=key, **{k: v for k, v in raw.items() if k != "api_key"})

    def _save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": {key: state.__dict__ for key, state in self.states.items()}}
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def quota_stats(self) -> dict[str, int]:
        now = utcnow()
        available = 0
        total_used = 0
        for state in self.states.values():
            total_used += int(state.used_units or 0)
            if state.disabled_until:
                disabled_until = datetime.fromisoformat(state.disabled_until)
                if disabled_until > now:
                    continue
            if int(state.used_units or 0) < self.daily_quota_limit:
                available += 1
        return {
            "keys_available": available,
            "keys_total": len(self.states),
            "quota_used_total": total_used,
        }


class YouTubeDiscoveryAdapter:
    platform = "youtube"
    capabilities = DiscoveryCapabilities(
        search=True,
        metadata=True,
        snapshots=True,
        comments=True,
        downloads=True,
    )

    def __init__(self, key_pool: YouTubeKeyPool) -> None:
        self.key_pool = key_pool

    def discover(
        self,
        *,
        category: str,
        query: str,
        limit: int,
        published_after: Optional[datetime] = None,
        published_before: Optional[datetime] = None,
        time_interval: Optional[str] = None,
        relevance_language: Optional[str] = None,
        region_code: Optional[str] = None,
    ) -> Iterable[CollectedVideo]:
        collected = 0
        page_token: Optional[str] = None
        while collected < limit:
            channels: dict[str, ChannelMetadataDto] = {}
            client = self.key_pool.get_client()
            api_key = client.api_key
            try:
                search = client.search_videos(
                    query,
                    page_token=page_token,
                    max_results=min(50, limit - collected),
                    published_after=published_after,
                    published_before=published_before,
                    relevance_language=relevance_language,
                    region_code=region_code,
                    order="date" if published_after or published_before else "relevance",
                )
                video_ids = [item.video_id for item in search.items]
                metadata = client.get_videos_metadata_batch(video_ids)
                channel_ids = [item.channel_id for item in metadata if item.channel_id]
                if channel_ids:
                    channels = {
                        item.channel_id: item
                        for item in client.get_channels_metadata_batch(channel_ids)
                    }
                self.key_pool.record_success(api_key, client.quota_tracker.used_units)
            except (QuotaExceededError, YouTubeAPIError) as exc:
                self.key_pool.record_failure(api_key, exc)
                continue

            for item in metadata:
                channel = channels.get(item.channel_id)
                snapshot = self._snapshot_from_metadata(item, channel, snapshot_index=0)
                yield CollectedVideo(
                    platform=self.platform,
                    video_id=item.video_id,
                    url=f"https://www.youtube.com/watch?v={item.video_id}",
                    category=category,
                    query=query,
                    metadata=self._metadata_dict(item, channel),
                    snapshot_0=snapshot,
                    time_interval=time_interval,
                    discovered_at=utcnow(),
                    platform_capabilities=self.capabilities.__dict__,
                )
                collected += 1
                if collected >= limit:
                    break
            page_token = search.next_page_token
            if not page_token:
                break

    def collect_snapshot(self, video_id: str, *, snapshot_index: int, comments_limit: int) -> Snapshot:
        client = self.key_pool.get_client()
        api_key = client.api_key
        try:
            metadata = client.get_video_metadata(video_id)
            channel = None
            if metadata.channel_id:
                channels = client.get_channels_metadata_batch([metadata.channel_id])
                if channels:
                    channel = channels[0]
            comments = [
                {
                    "commentId": comment.comment_id,
                    "text": comment.text_original,
                    "likeCount": comment.like_count,
                    "publishedAt": comment.published_at.isoformat(),
                    "authorName": comment.author_display_name,
                }
                for comment in client.iter_comments(video_id, max_count=comments_limit)
            ]
            self.key_pool.record_success(api_key, client.quota_tracker.used_units)
        except (QuotaExceededError, YouTubeAPIError) as exc:
            self.key_pool.record_failure(api_key, exc)
            raise
        snapshot = self._snapshot_from_metadata(metadata, channel, snapshot_index=snapshot_index)
        snapshot.comments = comments
        if snapshot_index > 0:
            from fetcher.dataset_collector.schemas import compact_follow_up_snapshot

            return compact_follow_up_snapshot(snapshot)
        return snapshot

    def collect_comments(self, video_id: str, *, comments_limit: int, attempts: int = 5) -> list[dict]:
        last_error: Exception | None = None
        for _ in range(max(attempts, 1)):
            client = self.key_pool.get_client()
            api_key = client.api_key
            try:
                comments = [
                    {
                        "text": comment.text_original,
                        "likeCount": comment.like_count,
                        "repliesCount": comment.replies_count,
                        "publishedAt": comment.published_at.isoformat().replace("+00:00", "Z"),
                        "authorName": comment.author_display_name,
                    }
                    for comment in client.iter_comments(video_id, max_count=comments_limit)
                ]
                self.key_pool.record_success(api_key, client.quota_tracker.used_units)
                return comments
            except (QuotaExceededError, YouTubeAPIError) as exc:
                if is_comments_disabled_error(exc):
                    self.key_pool.record_success(api_key, client.quota_tracker.used_units)
                    return []
                last_error = exc
                self.key_pool.record_failure(api_key, exc)
                continue
        if last_error is not None:
            raise last_error
        return []

    @staticmethod
    def _metadata_dict(item, channel: ChannelMetadataDto | None) -> dict:
        data = {
            "title": item.title,
            "description": item.description,
            "duration_seconds": item.duration_seconds,
            "publishedAt": item.published_at.isoformat(),
            "channelTitle": item.channel_title,
            "channel_id": item.channel_id,
            "raw": item.raw_json,
        }
        if channel:
            data["channel"] = channel.raw_json
        return data

    @staticmethod
    def _snapshot_from_metadata(item, channel: ChannelMetadataDto | None, *, snapshot_index: int) -> Snapshot:
        now = utcnow()
        return Snapshot(
            snapshot_index=snapshot_index,
            time_get=format_time_get(now),
            collected_at=now,
            viewCount=str(item.view_count),
            likeCount=str(item.like_count),
            commentCount=str(item.comment_count),
            subscriberCount=channel.subscriber_count if channel else None,
            videoCount=channel.video_count if channel else None,
            viewCount_channel=channel.view_count if channel else None,
            raw=(
                {"video": item.raw_json, "channel": channel.raw_json if channel else None}
                if snapshot_index == 0
                else {}
            ),
        )
