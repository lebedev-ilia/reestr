from __future__ import annotations

import os
import sys
from itertools import cycle
from pathlib import Path
from typing import Callable, Iterator, Optional

from fetcher.config import settings
from fetcher.dataset_collector.schemas import CampaignConfig


def normalize_proxy_url(value: str, *, default_scheme: str = "http") -> str:
    """Normalize proxy URL; preserve explicit socks4/socks5/socks5h/http/https schemes."""
    proxy = value.strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"{default_scheme}://{proxy}"
    return proxy


def is_local_proxy(value: str) -> bool:
    lowered = value.lower()
    return "://127." in lowered or "://localhost" in lowered or "://0.0.0.0" in lowered


def pytubefix_proxy_dict(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    url = normalize_proxy_url(proxy_url)
    return {"http": url, "https": url}


def is_proxy_transport_error(exc: Exception) -> bool:
    """True when the failure is likely caused by the proxy, not YouTube/API key."""
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(
        exc,
        (
            httpx.ProxyError,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ),
    )


def load_proxy_file(
    path: str | Path,
    *,
    default_scheme: str = "http",
    include_local: bool = False,
    download_only: bool | None = None,
) -> list[str]:
    """Load proxies from file.

    Lines may end with ``download_only`` (or ``nodpi``) — used only for pytubefix download.
    Other lines are for YouTube Data API discovery and yt-dlp enrich.
    """
    proxy_path = Path(path)
    if not proxy_path.exists():
        return []
    proxies: list[str] = []
    for raw_line in proxy_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        proxy = normalize_proxy_url(parts[0], default_scheme=default_scheme)
        if not proxy:
            continue
        flags = {part.lower() for part in parts[1:]}
        is_download_only = "download_only" in flags or "nodpi" in flags

        if download_only is True and not is_download_only:
            continue
        if download_only is False and is_download_only:
            continue

        if is_local_proxy(proxy) and not include_local and not is_download_only:
            continue
        proxies.append(proxy)
    return list(dict.fromkeys(proxies))


def configured_proxies(
    *,
    config: CampaignConfig | None = None,
    include_local: bool = False,
    download_only: bool = False,
) -> list[str]:
    """Return proxy list for discovery/enrich (default) or download-only pool."""
    if not download_only and config is not None and not config.use_proxies_for_discovery:
        return []

    proxies: list[str] = []
    if config is not None and config.proxies_file:
        proxy_path = Path(config.proxies_file)
        if not proxy_path.is_absolute():
            fetcher_root = Path(__file__).resolve().parents[2]
            proxy_path = fetcher_root / proxy_path
        proxies.extend(
            load_proxy_file(
                proxy_path,
                default_scheme=config.proxy_default_scheme,
                include_local=include_local or config.include_local_proxies_for_discovery,
                download_only=download_only,
            )
        )
    if download_only:
        return list(dict.fromkeys(proxies))

    if getattr(settings, "enable_proxies", False):
        raw = getattr(settings, "proxies", [])
        if isinstance(raw, str):
            proxies.extend([normalize_proxy_url(item.strip()) for item in raw.split(",") if item.strip()])
        else:
            proxies.extend([normalize_proxy_url(str(item).strip()) for item in raw if str(item).strip()])
    env_proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if env_proxy:
        proxies.append(normalize_proxy_url(env_proxy))
    if not include_local:
        proxies = [proxy for proxy in proxies if not is_local_proxy(proxy)]
    return list(dict.fromkeys(proxies))


class ProxyRotator:
    """Round-robin proxy pool with immediate blacklist on transport failures."""

    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        config: CampaignConfig | None = None,
        include_local: bool = False,
        download_only: bool = False,
        on_blacklist: Callable[[str, str], None] | None = None,
    ) -> None:
        self.proxies = [
            normalize_proxy_url(proxy)
            for proxy in (
                proxies
                if proxies is not None
                else configured_proxies(
                    config=config,
                    include_local=include_local,
                    download_only=download_only,
                )
            )
            if proxy
        ]
        self._blacklist: set[str] = set()
        self._last_good: str | None = None
        self._iterator: Iterator[str] | None = cycle(self.proxies) if self.proxies else None
        self._on_blacklist = on_blacklist or self._default_blacklist_logger

    @staticmethod
    def _default_blacklist_logger(proxy: str, reason: str) -> None:
        print(f"[proxy] blacklisted {proxy} ({reason})", file=sys.stderr, flush=True)

    def blacklisted(self) -> set[str]:
        return set(self._blacklist)

    def record_success(self, proxy: str | None) -> None:
        if not proxy:
            return
        self._last_good = normalize_proxy_url(proxy)

    def record_failure(self, proxy: str | None, error: Exception | None = None) -> None:
        if not proxy:
            return
        normalized = normalize_proxy_url(proxy)
        if error is not None and not is_proxy_transport_error(error):
            return
        self._blacklist.add(normalized)
        reason = type(error).__name__ if error is not None else "transport_error"
        self._on_blacklist(normalized, reason)
        if self._last_good == normalized:
            self._last_good = None

    def record_download_failure(self, proxy: str | None) -> None:
        """Blacklist proxy after a failed pytubefix/yt-dlp download attempt."""
        if not proxy:
            return
        normalized = normalize_proxy_url(proxy)
        self._blacklist.add(normalized)
        self._on_blacklist(normalized, "download_failed")
        if self._last_good == normalized:
            self._last_good = None

    def _available_proxies(self) -> list[str]:
        available = [proxy for proxy in self.proxies if proxy not in self._blacklist]
        if available:
            return available
        if self._blacklist:
            print(
                "[proxy] all proxies blacklisted, resetting blacklist",
                file=sys.stderr,
                flush=True,
            )
            self._blacklist.clear()
        return list(self.proxies)

    def next(self) -> Optional[str]:
        available = self._available_proxies()
        if not available:
            return None
        if self._last_good and self._last_good in available:
            return self._last_good
        if self._iterator is None:
            self._iterator = cycle(self.proxies)
        for _ in range(len(self.proxies)):
            candidate = normalize_proxy_url(next(self._iterator))
            if candidate in available:
                return candidate
        return available[0]
