"""YouTube video resolver: download once with yt-dlp, then play the local file.

Live googlevideo CDN URLs often return HTTP 403 to souphttpsrc/curl even when
yt-dlp can download the same media. Caching locally is the reliable lab path.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger("ott.youtube")

CACHE_DIR = os.environ.get("OTT_YOUTUBE_CACHE", "/tmp/ott-youtube")
MAX_HEIGHT = int(os.environ.get("OTT_YOUTUBE_MAX_HEIGHT", "720"))


def _video_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    if re.fullmatch(r"[\w-]{11}", url_or_id):
        return url_or_id
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|v=)([\w-]{11})",
        url_or_id,
    )
    return m.group(1) if m else None


def resolve_youtube_stream_url(url_or_id: str, preferred_height: int = 0) -> Tuple[str, str]:
    """Resolve YouTube URL/ID (or local path) to a playable local file or testsrc.

    Returns:
        (path_or_testsrc, title)
    """
    if not url_or_id:
        return _fallback_source()

    if os.path.exists(url_or_id):
        return url_or_id, os.path.basename(url_or_id)

    if url_or_id.startswith("/") or url_or_id.startswith("./") or url_or_id.endswith(
        (".mp4", ".mkv", ".mov", ".ts", ".webm")
    ):
        logger.warning(f"Local media path not found: {url_or_id}, using fallback")
        return _fallback_source()

    if url_or_id == "testsrc" or url_or_id.startswith("synthetic:"):
        return "testsrc", "Synthetic Color Test Pattern"

    height = preferred_height or MAX_HEIGHT
    vid = _video_id(url_or_id)
    youtube_url = (
        f"https://www.youtube.com/watch?v={vid}"
        if vid
        else (url_or_id if url_or_id.startswith("http") else f"https://www.youtube.com/watch?v={url_or_id}")
    )
    cache_key = vid or re.sub(r"[^\w.-]+", "_", url_or_id)[-40:]
    os.makedirs(CACHE_DIR, exist_ok=True)
    out_tmpl = os.path.join(CACHE_DIR, f"{cache_key}.%(ext)s")
    cached = _find_cached(cache_key)
    if cached:
        logger.info(f"Using cached YouTube media {cached}")
        return cached, f"YouTube {cache_key}"

    try:
        import yt_dlp

        ydl_opts = {
            # Progressive single-file preferred; fall back to merge (needs ffmpeg).
            "format": (
                f"best[height<=?{height}][ext=mp4][protocol^=http][protocol!*=m3u8]/"
                f"best[height<=?{height}][ext=mp4]/"
                f"bv*[height<=?{height}]+ba/best[height<=?{height}]/best"
            ),
            "outtmpl": out_tmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 3,
            "merge_output_format": "mp4",
            "nopart": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            title = (info or {}).get("title") or f"YouTube {cache_key}"
        cached = _find_cached(cache_key)
        if cached and os.path.getsize(cached) > 0:
            logger.info(f"Downloaded YouTube '{title}' → {cached} ({os.path.getsize(cached)} bytes)")
            return cached, title
        logger.warning(f"yt-dlp finished but cache missing for {cache_key}")
    except Exception as e:
        logger.warning(f"yt-dlp download failed for {url_or_id}: {e}, using fallback")

    return _fallback_source()


def _find_cached(cache_key: str) -> Optional[str]:
    for ext in ("mp4", "mkv", "webm", "mov"):
        path = os.path.join(CACHE_DIR, f"{cache_key}.{ext}")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


def _fallback_source() -> Tuple[str, str]:
    candidates = [
        "/data/source.mp4",
        "/data/example.mp4",
        "/app/data/source.mp4",
        "/home/fcp/INA-Infra/applications/cctv/data/example.mp4",
    ]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c, "Local HD Benchmark Video"
    return "testsrc", "Synthetic Color Test Pattern"
