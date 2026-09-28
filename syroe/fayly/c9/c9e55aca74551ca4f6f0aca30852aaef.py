from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from pytubefix import YouTube

from fetcher.dataset_collector.cookies import (
    CookieRotator,
    apply_cookiefile,
    install_pytubefix_session,
)
from fetcher.dataset_collector.proxy import ProxyRotator, configured_proxies, pytubefix_proxy_dict
from fetcher.dataset_collector.schemas import CampaignConfig
from fetcher.dataset_collector.state import DatasetState
from fetcher.dataset_collector.local_delete import delete_local_file
from fetcher.dataset_collector.worker_shutdown import should_stop
from fetcher.dataset_collector.queue_retries import (
    load_dead_letter_keys,
    queue_item_key,
    record_queue_failure,
)
from fetcher.dataset_collector.download_pacing import apply_download_pause
from fetcher.dataset_collector.hf_coordination import WorkerCoordination, coord_enabled
from fetcher.dataset_collector.worker_logging import (
    count_glob_files,
    count_jsonl_lines,
    log_kv_block,
    log_pass_footer,
    log_pass_header,
    worker_log,
)

MAX_DOWNLOAD_HEIGHT = 1080
# Try highest cap first; on failure retry with lower caps (merge / stream errors).
DOWNLOAD_HEIGHT_TIERS = (1080, 720, 480, 360, 240, 144)


class BotDetectionDownloadError(RuntimeError):
    """YouTube rejected the download request as automated traffic."""


class VideoUnavailableDownloadError(RuntimeError):
    """Video deleted, private, or region-blocked — no point retrying cookies/clients."""


def is_pytubefix_client_error(exc: BaseException) -> bool:
    """YouTube innertube parse failures (visitorData / empty params) — try another client."""
    if isinstance(exc, (IndexError, KeyError)):
        return True
    text = str(exc).lower()
    return "visitordata" in text or "list index out of range" in text


def is_bot_detection_error(exc_or_text: Exception | str) -> bool:
    text = str(exc_or_text).lower()
    return any(
        marker in text
        for marker in (
            "botdetection",
            "detected as a bot",
            "sign in to confirm",
            "not a bot",
            "po_token",
            "potoken invalid",
            "sabr maximum reload",
            "sabrerror",
        )
    )


def is_video_unavailable_error(exc_or_text: Exception | str) -> bool:
    text = str(exc_or_text).lower()
    return "is unavailable" in text or "videounavailable" in text


def iter_download_queue(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def scan_metadata_shards_for_downloads(
    state: DatasetState,
    *,
    category: str | None = None,
) -> int:
    """Enqueue discover metadata videos not downloaded/uploaded yet."""
    done = state.load_download_done() | state.load_hf_video_upload_done()
    queued = state.load_hf_video_upload_queued()
    existing_queue = {
        f"{row.get('platform') or 'youtube'}:{row.get('category') or 'unknown'}:{row.get('video_id')}"
        for row in iter_download_queue(state.download_dir / "queue.jsonl")
        if row.get("video_id")
    }
    metadata_root = state.shards_dir / "metadata"
    if not metadata_root.exists():
        return 0
    pattern = f"category={category}/part_*.json" if category else "**/part_*.json"
    added = 0
    for shard_path in sorted(metadata_root.glob(pattern)):
        if shard_path.name.endswith(".tmp"):
            continue
        data = json.loads(shard_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        category_name = category or shard_path.parent.name.replace("category=", "")
        for video_id, entry in data.items():
            platform = entry.get("platform") or "youtube"
            key = f"{platform}:{category_name}:{video_id}"
            if key in done or key in queued or key in existing_queue:
                continue
            url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
            state.enqueue_download_item(
                platform=platform,
                video_id=video_id,
                url=url,
                category=category_name,
            )
            existing_queue.add(key)
            added += 1
    return added


def local_video_path(state: DatasetState, *, category: str, video_id: str) -> Path:
    return state.download_dir / "videos" / category / f"{video_id}.mp4"


def _maybe_remove_orphan_local_mp4(
    target: Path,
    *,
    config: CampaignConfig,
    video_id: str,
    reason: str,
) -> bool:
    """Remove local mp4 left after HF upload or when video is already on HF."""
    if not target.is_file() or target.stat().st_size <= 0:
        return False
    delete_local_file(
        target,
        output_dir=config.output_dir,
        permanent_on_drive=config.drive_permanent_delete,
        log_channel="download",
    )
    worker_log("download", f"orphan_local_removed {video_id}: {reason}")
    return True


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _parse_resolution_height(resolution: str | None) -> int:
    if not resolution:
        return 0
    digits = "".join(ch for ch in resolution if ch.isdigit())
    return int(digits) if digits else 0


def _pick_progressive_stream(yt: YouTube, max_height: int):
    streams = yt.streams.filter(progressive=True, file_extension="mp4")
    for height in range(max_height, 0, -1):
        stream = streams.filter(res=f"{height}p").first()
        if stream:
            return stream
    return streams.get_highest_resolution()


def _pick_adaptive_streams(yt: YouTube, *, max_height: int):
    video = None
    for stream in yt.streams.filter(adaptive=True, only_video=True).order_by("resolution").desc():
        height = _parse_resolution_height(stream.resolution)
        if height > max_height:
            continue
        if stream.subtype == "mp4":
            video = stream
            break
    if video is None:
        for stream in yt.streams.filter(adaptive=True, only_video=True).order_by("resolution").desc():
            if _parse_resolution_height(stream.resolution) <= max_height:
                video = stream
                break
    audio = yt.streams.filter(only_audio=True, mime_type="audio/mp4").order_by("abr").desc().first()
    if audio is None:
        audio = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if video is None or audio is None:
        return None, None
    return video, audio


def _check_shutdown() -> None:
    if should_stop():
        raise KeyboardInterrupt("worker shutdown requested")


def _cleanup_download_temps(
    target: Path,
    *,
    output_dir: str | None = None,
    permanent_on_drive: bool | None = None,
) -> None:
    for suffix in (".video.tmp", ".audio.tmp"):
        delete_local_file(
            target.with_name(f"{target.stem}{suffix}"),
            output_dir=output_dir,
            permanent_on_drive=permanent_on_drive,
            log_channel="download",
        )
    if target.exists() and target.stat().st_size == 0:
        delete_local_file(
            target,
            output_dir=output_dir,
            permanent_on_drive=permanent_on_drive,
            log_channel="download",
        )


def _merge_av_with_ffmpeg(video_path: Path, audio_path: Path, output_path: Path) -> None:
    video_path = video_path.resolve()
    audio_path = audio_path.resolve()
    output_path = output_path.resolve()
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # communicate() in a daemon thread avoids the pipe-buffer deadlock that occurs when
    # ffmpeg writes > ~64 KB to stderr while the main thread only calls poll().
    result: dict = {}

    def _communicate() -> None:
        result["stdout"], result["stderr"] = proc.communicate()

    thread = threading.Thread(target=_communicate, daemon=True)
    thread.start()
    while thread.is_alive():
        _check_shutdown()
        thread.join(timeout=0.5)

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            output=result.get("stdout"),
            stderr=result.get("stderr"),
        )


def _download_youtube_mp4_at_height(
    yt: YouTube,
    *,
    target: Path,
    max_height: int,
    log: Callable[[str], None],
) -> str:
    """Download best mp4 up to max_height; ffmpeg-merge adaptive when higher than progressive."""
    progressive = _pick_progressive_stream(yt, max_height)
    progressive_height = _parse_resolution_height(progressive.resolution if progressive else None)

    video_stream, audio_stream = _pick_adaptive_streams(yt, max_height=max_height)
    adaptive_height = _parse_resolution_height(video_stream.resolution if video_stream else None)

    if video_stream and audio_stream and adaptive_height > progressive_height:
        tmp_video = target.with_name(f"{target.stem}.video.tmp")
        tmp_audio = target.with_name(f"{target.stem}.audio.tmp")
        log(
            f"  adaptive {video_stream.resolution} + {audio_stream.abr} "
            f"({video_stream.filesize_mb:.1f}+{audio_stream.filesize_mb:.1f} MiB) -> ffmpeg merge"
        )
        try:
            video_stream.download(output_path=str(target.parent), filename=tmp_video.name)
            _check_shutdown()
            audio_stream.download(output_path=str(target.parent), filename=tmp_audio.name)
            _check_shutdown()
            _merge_av_with_ffmpeg(tmp_video, tmp_audio, target)
        finally:
            delete_local_file(tmp_video, log_channel="download")
            delete_local_file(tmp_audio, log_channel="download")
        return f"{video_stream.resolution} (merged)"

    if progressive is None:
        raise RuntimeError(f"no stream available up to {max_height}p")

    log(f"  progressive {progressive.resolution} ({progressive.filesize_mb:.1f} MiB)")
    progressive.download(output_path=str(target.parent), filename=target.name)
    return f"{progressive.resolution} progressive"


def download_youtube_mp4(
    yt: YouTube,
    *,
    target: Path,
    max_height: int = MAX_DOWNLOAD_HEIGHT,
    log: Callable[[str], None] | None = None,
) -> str:
    """Pick best quality ≤1080, stepping down 720/480/… on hard failures."""
    emit = log or (lambda _msg: None)
    tiers = [h for h in DOWNLOAD_HEIGHT_TIERS if h <= max_height]
    if not tiers:
        tiers = list(DOWNLOAD_HEIGHT_TIERS)

    last_error: Exception | None = None
    for tier in tiers:
        _check_shutdown()
        try:
            return _download_youtube_mp4_at_height(yt, target=target, max_height=tier, log=emit)
        except KeyboardInterrupt:
            _cleanup_download_temps(target)
            raise
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            last_error = exc
            _cleanup_download_temps(target)
            if tier != tiers[-1]:
                emit(f"  tier ≤{tier}p failed ({exc}); trying lower resolution …")
            continue

    raise RuntimeError(str(last_error) if last_error else "no stream available")


def _pick_stream(yt: YouTube, *, max_height: int = MAX_DOWNLOAD_HEIGHT):
    return _pick_progressive_stream(yt, max_height)


def _progress_callback(video_id: str) -> Callable:
    last_pct = -1

    def _on_progress(stream, chunk, bytes_remaining) -> None:  # noqa: ARG001
        nonlocal last_pct
        _check_shutdown()
        total = stream.filesize or 0
        if total <= 0:
            return
        pct = int(100 * (total - bytes_remaining) / total)
        if pct >= last_pct + 5 or pct >= 99:
            last_pct = pct
            worker_log("download", f"  {video_id} {pct}%")

    return _on_progress


_node_ok_for_web: bool | None = None
_web_client_skip_logged = False
# Sticky pytubefix client across videos: stay on last working client until all cookies bot.
_pytubefix_sticky_client_index: int = 0


def _short_error(exc: BaseException, *, limit: int = 400) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if len(message) <= limit:
        return message
    return message[:limit] + "... [truncated]"


def _nodejs_ok_for_pytubefix_web() -> bool:
    """WEB client needs Node for botGuard poToken; Colab often has apt node but not nodejs-wheel."""
    global _node_ok_for_web
    if _node_ok_for_web is not None:
        return _node_ok_for_web
    try:
        from pytubefix.botGuard.bot_guard import NODE_PATH

        node = Path(NODE_PATH)
        if not node.is_file() or not os.access(node, os.X_OK):
            _node_ok_for_web = False
            return False
        proc = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        _node_ok_for_web = proc.returncode == 0
    except Exception:
        _node_ok_for_web = False
    return _node_ok_for_web


def _pytubefix_client_sequence(config: CampaignConfig) -> list[str]:
    global _web_client_skip_logged
    clients = [str(item).strip() for item in (config.download_pytubefix_clients or []) if str(item).strip()]
    if not clients:
        clients = ["ANDROID_VR", "WEB"]
    if "WEB" in clients and not _nodejs_ok_for_pytubefix_web():
        if not _web_client_skip_logged:
            worker_log(
                "download",
                "skip WEB client: Node.js for pytubefix botGuard unavailable "
                "(pip install nodejs-wheel-binaries; Colab: apt-get install -y nodejs)",
            )
            _web_client_skip_logged = True
        clients = [client for client in clients if client.upper() != "WEB"]
    return clients or ["ANDROID_VR"]


def _download_video_local_pytubefix_attempt(
    state: DatasetState,
    config: CampaignConfig,
    *,
    platform: str,
    video_id: str,
    url: str,
    category: str,
    target: Path,
    proxy_rotator: ProxyRotator | None,
    cookie_rotator: CookieRotator | None,
    cookie_file: Path | None,
    client_name: str,
    started_at: float,
) -> Path | None:
    proxy_url = proxy_rotator.next() if proxy_rotator else None
    proxies = pytubefix_proxy_dict(proxy_url)
    install_pytubefix_session(proxies=proxies, cookie_file=cookie_file)

    worker_log(
        "download",
        f"pytubefix start {video_id} category={category} client={client_name} "
        f"proxy={proxy_url or 'direct'} cookie={cookie_file.name if cookie_file else 'none'}",
    )
    worker_log("download", f"  url={url}")
    try:
        yt = YouTube(
            url,
            client=client_name,
            on_progress_callback=_progress_callback(video_id),
        )
        label = download_youtube_mp4(
            yt,
            target=target,
            max_height=MAX_DOWNLOAD_HEIGHT,
            log=lambda msg: worker_log("download", msg),
        )
        worker_log("download", f"  saved as {label}")
    except KeyboardInterrupt:
        _cleanup_download_temps(
            target,
            output_dir=config.output_dir,
            permanent_on_drive=config.drive_permanent_delete,
        )
        worker_log("download", f"STOP {video_id}: shutdown requested")
        raise
    except subprocess.CalledProcessError as exc:
        if proxy_rotator and proxy_url:
            proxy_rotator.record_download_failure(proxy_url)
        worker_log("download", f"FAIL {video_id}: ffmpeg merge failed: {exc.stderr.decode(errors='replace')[:300]}")
        return None
    except Exception as exc:
        if proxy_rotator and proxy_url:
            proxy_rotator.record_download_failure(proxy_url)
        if is_video_unavailable_error(exc):
            raise VideoUnavailableDownloadError(str(exc)) from exc
        if is_bot_detection_error(exc):
            raise BotDetectionDownloadError(str(exc)) from exc
        if is_pytubefix_client_error(exc):
            worker_log(
                "download",
                f"pytubefix client error {video_id} client={client_name}: {_short_error(exc)}",
            )
            return None
        worker_log("download", f"FAIL {video_id}: {_short_error(exc)}")
        return None

    if proxy_rotator and proxy_url:
        proxy_rotator.record_success(proxy_url)

    if not target.exists() or target.stat().st_size <= 0:
        worker_log("download", f"FAIL {video_id}: file missing after download")
        return None

    if cookie_rotator is not None:
        cookie_rotator.set_current(cookie_file)
        cookie_rotator.record_success()

    elapsed = time.perf_counter() - started_at
    size_bytes = target.stat().st_size
    state.record_performance_event(
        "download",
        {
            "platform": platform,
            "video_id": video_id,
            "category": category,
            "seconds": round(elapsed, 3),
            "size_bytes": size_bytes,
            "local_path": str(target.relative_to(state.root)),
            "backend": "pytubefix",
            "client": client_name,
        },
    )
    worker_log(
        "download",
        f"OK {video_id} -> {target.relative_to(state.root)} "
        f"({_format_bytes(size_bytes)}, {elapsed:.1f}s, pytubefix:{client_name})",
    )
    return target


def download_video_local(
    state: DatasetState,
    config: CampaignConfig,
    *,
    platform: str,
    video_id: str,
    url: str,
    category: str,
    proxy_rotator: ProxyRotator | None,
    cookie_rotator: CookieRotator | None = None,
) -> Path | None:
    if platform != "youtube":
        worker_log("download", f"skip {video_id}: platform {platform!r} not supported")
        return None

    target = local_video_path(state, category=category, video_id=video_id)
    if target.exists() and target.stat().st_size > 0:
        worker_log(
            "download",
            f"already on disk {video_id} -> {target.relative_to(state.root)} "
            f"({_format_bytes(target.stat().st_size)})",
        )
        return target

    if target.exists() and target.stat().st_size == 0:
        delete_local_file(
            target,
            output_dir=config.output_dir,
            permanent_on_drive=config.drive_permanent_delete,
            log_channel="download",
        )
        worker_log("download", f"removed empty partial file {video_id}")

    target.parent.mkdir(parents=True, exist_ok=True)
    cookie_attempts = cookie_rotator.iter_attempts() if cookie_rotator else [None]
    backend = (config.download_backend or "pytubefix").lower().replace("-", "_")
    if backend not in {"pytubefix", "yt_dlp", "yt_dlp_first"}:
        backend = "pytubefix"
    started_at = time.perf_counter()

    if backend in {"yt_dlp", "yt_dlp_first"}:
        cookie_file = cookie_attempts[0]
        try:
            return _download_video_local_ytdlp(
                state,
                config,
                platform=platform,
                video_id=video_id,
                url=url,
                category=category,
                target=target,
                proxy_rotator=proxy_rotator,
                cookie_file=cookie_file,
                started_at=started_at,
            )
        except BotDetectionDownloadError as exc:
            worker_log("download", f"FAIL {video_id}: yt-dlp bot detection: {exc}")
            raise
        except KeyboardInterrupt:
            _cleanup_download_temps(
                target,
                output_dir=config.output_dir,
                permanent_on_drive=config.drive_permanent_delete,
            )
            worker_log("download", f"STOP {video_id}: shutdown requested")
            raise
        except Exception as exc:
            if backend == "yt_dlp":
                worker_log("download", f"FAIL {video_id}: yt-dlp {type(exc).__name__}: {exc}")
                return None
            worker_log("download", f"yt-dlp failed for {video_id}, trying pytubefix: {type(exc).__name__}: {exc}")

    global _pytubefix_sticky_client_index

    bot_errors: list[str] = []
    pytubefix_clients = _pytubefix_client_sequence(config)
    client_count = len(pytubefix_clients)
    start_sticky = _pytubefix_sticky_client_index
    for offset in range(client_count):
        client_idx = (start_sticky + offset) % client_count
        client_name = pytubefix_clients[client_idx]
        client_exhausted = False
        for cookie_file in cookie_attempts:
            try:
                downloaded = _download_video_local_pytubefix_attempt(
                    state,
                    config,
                    platform=platform,
                    video_id=video_id,
                    url=url,
                    category=category,
                    target=target,
                    proxy_rotator=proxy_rotator,
                    cookie_rotator=cookie_rotator,
                    cookie_file=cookie_file,
                    client_name=client_name,
                    started_at=started_at,
                )
                if downloaded is not None:
                    _pytubefix_sticky_client_index = client_idx
                    return downloaded
            except VideoUnavailableDownloadError:
                raise
            except BotDetectionDownloadError as exc:
                bot_errors.append(str(exc)[:300])
                worker_log(
                    "download",
                    f"bot_detection {video_id}: client={client_name} "
                    f"cookie={cookie_file.name if cookie_file else 'none'}",
                )
                _cleanup_download_temps(
                    target,
                    output_dir=config.output_dir,
                    permanent_on_drive=config.drive_permanent_delete,
                )
                apply_download_pause(config, "bot")
                continue
            except KeyboardInterrupt:
                raise
            except Exception:
                _cleanup_download_temps(
                    target,
                    output_dir=config.output_dir,
                    permanent_on_drive=config.drive_permanent_delete,
                )
                raise
        else:
            client_exhausted = True
        if client_exhausted and client_idx + 1 < client_count:
            next_idx = client_idx + 1
            next_client = pytubefix_clients[next_idx]
            _pytubefix_sticky_client_index = next_idx
            worker_log(
                "download",
                f"all cookies failed for {video_id} client={client_name}; "
                f"sticky -> {next_client}",
            )

    worker_log("download", f"pytubefix exhausted for {video_id}; trying yt-dlp fallback")
    try:
        return _download_video_local_ytdlp(
            state,
            config,
            platform=platform,
            video_id=video_id,
            url=url,
            category=category,
            target=target,
            proxy_rotator=proxy_rotator,
            cookie_file=cookie_attempts[0],
            started_at=started_at,
        )
    except BotDetectionDownloadError as fallback_exc:
        raise BotDetectionDownloadError("; ".join(bot_errors[-3:] + [str(fallback_exc)[:300]])) from fallback_exc
    except Exception as fallback_exc:
        worker_log(
            "download",
            f"FAIL {video_id}: yt-dlp fallback {type(fallback_exc).__name__}: {fallback_exc}",
        )
        return None


def _download_video_local_ytdlp(
    state: DatasetState,
    config: CampaignConfig,
    *,
    platform: str,
    video_id: str,
    url: str,
    category: str,
    target: Path,
    proxy_rotator: ProxyRotator | None,
    cookie_file: Path | None,
    started_at: float,
) -> Path:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed") from exc

    proxy_url = proxy_rotator.next() if proxy_rotator else None
    outtmpl = str(target.with_suffix(".%(ext)s"))
    ydl_opts = {
        "format": config.download_ytdlp_format,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": list(config.download_ytdlp_player_clients or ["android", "web"]),
            },
        },
    }
    if proxy_url:
        ydl_opts["proxy"] = proxy_url
    apply_cookiefile(ydl_opts, CookieRotator([cookie_file]) if cookie_file else None)

    worker_log(
        "download",
        f"yt-dlp start {video_id} category={category} proxy={proxy_url or 'direct'} "
        f"cookie={cookie_file.name if cookie_file else 'none'}",
    )
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        if proxy_rotator and proxy_url:
            proxy_rotator.record_download_failure(proxy_url)
        if is_bot_detection_error(exc):
            raise BotDetectionDownloadError(str(exc)) from exc
        raise

    if not target.exists():
        candidates = sorted(target.parent.glob(f"{target.stem}.*"))
        for candidate in candidates:
            if candidate.suffix == ".part":
                continue
            if candidate.is_file() and candidate.stat().st_size > 0:
                candidate.replace(target)
                break

    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("yt-dlp did not create a non-empty mp4")

    if proxy_rotator and proxy_url:
        proxy_rotator.record_success(proxy_url)

    elapsed = time.perf_counter() - started_at
    size_bytes = target.stat().st_size
    state.record_performance_event(
        "download",
        {
            "platform": platform,
            "video_id": video_id,
            "category": category,
            "seconds": round(elapsed, 3),
            "size_bytes": size_bytes,
            "local_path": str(target.relative_to(state.root)),
            "backend": "yt_dlp",
        },
    )
    worker_log(
        "download",
        f"OK {video_id} -> {target.relative_to(state.root)} "
        f"({_format_bytes(size_bytes)}, {elapsed:.1f}s, yt-dlp)",
    )
    return target


def run_download_queue(
    state: DatasetState,
    config: CampaignConfig,
    queue_path: Path,
    *,
    limit: int | None = None,
    cookie_rotator=None,
) -> dict[str, int]:
    cookie_rotator = cookie_rotator or CookieRotator.from_config(config)
    if cookie_rotator.cookie_files:
        worker_log(
            "download",
            f"cookies: {', '.join(p.name for p in cookie_rotator.cookie_files)}",
        )

    clients = _pytubefix_client_sequence(config)
    if clients:
        sticky_name = clients[_pytubefix_sticky_client_index % len(clients)]
        worker_log(
            "download",
            f"pytubefix sticky client: {sticky_name} "
            f"(index {_pytubefix_sticky_client_index + 1}/{len(clients)}, "
            f"order: {', '.join(clients)})",
        )

    log_pass_header("download", "pass start")
    coord = WorkerCoordination(state, config)
    if coord_enabled(config):
        coord_stats = coord.sync_from_hf("download")
        worker_log("download", f"coord_sync worker={coord.worker_id} {coord_stats}")

    queued_from_metadata = scan_metadata_shards_for_downloads(state)
    if queued_from_metadata:
        worker_log("download", f"queued_from_metadata_shards: {queued_from_metadata}")

    proxy_list = configured_proxies(config=config, download_only=True)
    if proxy_list:
        worker_log("download", f"proxies (download_only): {', '.join(proxy_list)}")
    else:
        worker_log("download", "no download_only proxies — direct connection")
    proxy_rotator = ProxyRotator(proxies=proxy_list)

    done_keys = state.load_download_done()
    if coord_enabled(config):
        done_keys |= coord.global_done_keys
    hf_done_keys = state.load_hf_video_upload_done()
    hf_queued_keys = state.load_hf_video_upload_queued()
    post_enrich_rejected = state.load_post_enrich_rejected_video_ids()
    dead_letter_keys = load_dead_letter_keys(state, service="download")
    attempt_counts_cache: dict[str, int] = load_attempt_counts(state, service="download")
    queue_lines = count_jsonl_lines(queue_path)
    videos_dir = state.download_dir / "videos"
    local_mp4 = count_glob_files(videos_dir, "**/*.mp4") if videos_dir.exists() else 0

    log_kv_block(
        "download",
        [
            ("queue_file", queue_path),
            ("queue_lines", queue_lines),
            ("already_done", len(done_keys)),
            ("already_on_hf", len(hf_done_keys)),
            ("pending_hf_upload", len(hf_queued_keys - hf_done_keys)),
            ("local_mp4_on_disk", local_mp4),
            ("limit_this_pass", limit if limit is not None else "none (until queue end)"),
        ],
    )

    if queue_lines == 0:
        worker_log("download", "queue empty — nothing to download (wait for discover)")
        result = {"downloaded": 0, "failed": 0, "skipped": 0, "attempted": 0}
        log_pass_footer("download", result)
        return result

    results = {"downloaded": 0, "failed": 0, "skipped": 0}
    skip_reasons: dict[str, int] = {}
    attempted = 0

    for item in iter_download_queue(queue_path):
        if should_stop():
            worker_log("download", "shutdown requested — stopping pass")
            break
        if limit is not None and results["downloaded"] + results["failed"] >= limit:
            worker_log("download", f"limit reached ({limit}), stopping this pass")
            break

        platform = item.get("platform")
        url = item.get("url")
        video_id = item.get("video_id")
        category = item.get("category") or "unknown"
        if not platform or not url or not video_id:
            results["skipped"] += 1
            skip_reasons["invalid_row"] = skip_reasons.get("invalid_row", 0) + 1
            continue

        key = f"{platform}:{category}:{video_id}"
        retry_key = queue_item_key("download", item)
        if retry_key in dead_letter_keys:
            results["skipped"] += 1
            skip_reasons["dead_letter"] = skip_reasons.get("dead_letter", 0) + 1
            continue
        target = local_video_path(state, category=category, video_id=video_id)
        if key in done_keys or key in hf_done_keys:
            if key in hf_done_keys:
                if _maybe_remove_orphan_local_mp4(
                    target,
                    config=config,
                    video_id=video_id,
                    reason="already_on_hf",
                ):
                    skip_reasons["orphan_local_removed"] = skip_reasons.get("orphan_local_removed", 0) + 1
            results["skipped"] += 1
            skip_reasons["already_done"] = skip_reasons.get("already_done", 0) + 1
            continue
        if key in hf_queued_keys and target.exists() and target.stat().st_size > 0:
            if key in hf_done_keys:
                if _maybe_remove_orphan_local_mp4(
                    target,
                    config=config,
                    video_id=video_id,
                    reason="hf_done_pending_queue",
                ):
                    skip_reasons["orphan_local_removed"] = skip_reasons.get("orphan_local_removed", 0) + 1
            results["skipped"] += 1
            skip_reasons["pending_hf_upload"] = skip_reasons.get("pending_hf_upload", 0) + 1
            continue
        if video_id in post_enrich_rejected:
            results["skipped"] += 1
            skip_reasons["post_enrich_rejected"] = skip_reasons.get("post_enrich_rejected", 0) + 1
            continue
        coord_skip = coord.skip_reason("download", key)
        if coord_skip:
            coord.record_skip("download", coord_skip)
            results["skipped"] += 1
            skip_reasons[coord_skip] = skip_reasons.get(coord_skip, 0) + 1
            continue
        if not coord.try_claim("download", key):
            coord.record_skip("download", "coord_claim_busy")
            results["skipped"] += 1
            skip_reasons["coord_claim_busy"] = skip_reasons.get("coord_claim_busy", 0) + 1
            continue

        attempted += 1
        worker_log(
            "download",
            f"({attempted}) pending {video_id} [{category}] "
            f"(pass: +{results['downloaded']} ok, {results['failed']} fail)",
        )

        item_done = False
        while not should_stop() and not item_done:
            download_started = time.perf_counter()
            try:
                path = download_video_local(
                    state,
                    config,
                    platform=platform,
                    video_id=video_id,
                    url=url,
                    category=category,
                    proxy_rotator=proxy_rotator,
                    cookie_rotator=cookie_rotator,
                )
            except KeyboardInterrupt:
                worker_log("download", "shutdown requested — exiting pass")
                item_done = True
                break
            except VideoUnavailableDownloadError as exc:
                results["failed"] += 1
                worker_log("download", f"unavailable {video_id}: {_short_error(exc)}")
                if record_queue_failure(
                    state,
                    service="download",
                    item=item,
                    error=f"unavailable: {exc}",
                    dead_letter_cache=dead_letter_keys,
                    attempt_cache=attempt_counts_cache,
                ):
                    dead_letter_keys.add(retry_key)
                apply_download_pause(config, "unavailable")
                item_done = True
            except BotDetectionDownloadError as exc:
                worker_log(
                    "download",
                    f"bot_detection {video_id}: {_short_error(exc)} — "
                    f"sleep 2min, retry same video (not skipping)",
                )
                apply_download_pause(config, "bot")
                continue
            else:
                if path is None:
                    results["failed"] += 1
                    if record_queue_failure(
                        state,
                        service="download",
                        item=item,
                        error="download returned no local file",
                        dead_letter_cache=dead_letter_keys,
                        attempt_cache=attempt_counts_cache,
                    ):
                        dead_letter_keys.add(retry_key)
                    apply_download_pause(config, "fail")
                    item_done = True
                else:
                    rel = str(path.relative_to(state.root))
                    state.enqueue_hf_video_upload(
                        platform=platform,
                        video_id=video_id,
                        category=category,
                        local_path=rel,
                    )
                    coord.mark_done("download", key, video_id=video_id, category=category)
                    hf_queued_keys.add(key)
                    results["downloaded"] += 1
                    worker_log(
                        "download",
                        f"  -> enqueued HF video upload for {video_id}; done after HF commit",
                    )
                    elapsed = time.perf_counter() - download_started
                    fast_threshold = float(getattr(config, "download_fast_threshold_seconds", 8.0))
                    if elapsed < fast_threshold:
                        worker_log(
                            "download",
                            f"fast download {video_id} ({elapsed:.1f}s < {fast_threshold}s)",
                        )
                        apply_download_pause(config, "fast")
                    else:
                        apply_download_pause(config, "success")
                    item_done = True

        if should_stop():
            break

    if skip_reasons:
        log_kv_block("download", [(f"skipped_{k}", v) for k, v in sorted(skip_reasons.items())])

    local_mp4_after = count_glob_files(videos_dir, "**/*.mp4") if videos_dir.exists() else 0
    worker_log("download", f"local_mp4_on_disk now: {local_mp4_after} (+{local_mp4_after - local_mp4} this pass)")

    from fetcher.dataset_collector.inventory import refresh_summary

    if coord_enabled(config):
        coord.flush_coord_uploads("download", force=True)

    refresh_summary(state)
    results["attempted"] = attempted
    log_pass_footer("download", results)
    return results
