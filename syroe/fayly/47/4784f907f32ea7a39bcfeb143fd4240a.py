from __future__ import annotations

import http.cookiejar
import os
from pathlib import Path
from typing import Optional
from urllib import request

from fetcher.dataset_collector.schemas import CampaignConfig


class CookieRotator:
    def __init__(self, cookie_files: list[Path], *, rotate_after_successes: int = 20) -> None:
        self.cookie_files = [path for path in cookie_files if path.is_file()]
        self.rotate_after_successes = max(1, rotate_after_successes)
        self._index = 0
        self._successes_on_current = 0

    @classmethod
    def from_config(cls, config: CampaignConfig) -> "CookieRotator":
        cookie_dir = config.cookie_files_dir or os.getenv("FETCHER_COOKIE_FILES_DIR")
        if not cookie_dir:
            return cls([])
        root = Path(cookie_dir)
        if not root.is_absolute():
            # Paths in campaign JSON are relative to Fetcher/ (cli cwd).
            fetcher_root = Path(__file__).resolve().parents[2]
            root = fetcher_root / root
        return cls(
            sorted(root.glob(config.cookie_file_glob or "*.txt")),
            rotate_after_successes=config.download_cookie_rotate_successes,
        )

    @classmethod
    def from_directory(
        cls,
        directory: Path | str,
        *,
        glob_pattern: str = "*.txt",
        rotate_after_successes: int = 20,
    ) -> "CookieRotator":
        return cls(
            sorted(Path(directory).glob(glob_pattern)),
            rotate_after_successes=rotate_after_successes,
        )

    def next(self) -> Optional[Path]:
        if not self.cookie_files:
            return None
        return self.cookie_files[self._index % len(self.cookie_files)]

    def rotate(self) -> Optional[Path]:
        if not self.cookie_files:
            return None
        self._index = (self._index + 1) % len(self.cookie_files)
        self._successes_on_current = 0
        return self.next()

    def record_success(self) -> None:
        if not self.cookie_files:
            return
        self._successes_on_current += 1
        if self._successes_on_current >= self.rotate_after_successes:
            self.rotate()

    def set_current(self, cookie_file: Path | None) -> None:
        if cookie_file is None or not self.cookie_files:
            return
        try:
            self._index = self.cookie_files.index(cookie_file)
            self._successes_on_current = 0
        except ValueError:
            return

    def iter_attempts(self) -> list[Optional[Path]]:
        """Try the current cookie first, then every other cookie once."""

        current = self.next()
        if not self.cookie_files:
            return [None]
        ordered = [current]
        for offset in range(1, len(self.cookie_files)):
            ordered.append(self.cookie_files[(self._index + offset) % len(self.cookie_files)])
        return ordered


def install_pytubefix_session(
    *,
    proxies: dict[str, str] | None = None,
    cookie_file: Path | None = None,
) -> None:
    """Configure urllib opener used by pytubefix (proxy + Netscape cookie file)."""
    handlers: list[object] = []
    if proxies:
        handlers.append(request.ProxyHandler(proxies))
    if cookie_file is not None and cookie_file.is_file():
        jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
        jar.load(ignore_discard=True, ignore_expires=True)
        handlers.append(request.HTTPCookieProcessor(jar))
    if handlers:
        request.install_opener(request.build_opener(*handlers))


def apply_cookiefile(ydl_opts: dict, rotator: CookieRotator | None) -> dict:
    if rotator is None:
        return ydl_opts
    cookie_file = rotator.next()
    if cookie_file is not None:
        ydl_opts["cookiefile"] = str(cookie_file)
    return ydl_opts
