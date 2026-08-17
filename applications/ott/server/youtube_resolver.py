"""YouTube video stream resolver using yt-dlp with graceful fallback to local clips."""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger("ott.youtube")


def resolve_youtube_stream_url(url_or_id: str, preferred_height: int = 720) -> Tuple[str, str]:
    """Resolve a YouTube video URL or ID to a playable progressive MP4 stream URL or cached path.
    
    Returns:
        (stream_url_or_path, video_title)
    """
    if not url_or_id:
        return _fallback_source()

    # If it is already a local file path
    if os.path.exists(url_or_id):
        return url_or_id, os.path.basename(url_or_id)

    # Format YouTube URL
    if not url_or_id.startswith("http"):
        youtube_url = f"https://www.youtube.com/watch?v={url_or_id}"
    else:
        youtube_url = url_or_id

    try:
        import yt_dlp

        ydl_opts = {
            "format": f"best[height<={preferred_height}][ext=mp4]/best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 8,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            if info:
                title = info.get("title", "YouTube Stream")
                stream_url = info.get("url")
                if stream_url:
                    logger.info(f"Resolved YouTube URL for '{title}' -> {stream_url[:60]}...")
                    return stream_url, title
    except Exception as e:
        logger.warning(f"yt-dlp resolution failed for {url_or_id}: {e}, using fallback")

    return _fallback_source()


def _fallback_source() -> Tuple[str, str]:
    # Check for local video assets in order of preference
    candidates = [
        "/data/source.mp4",
        "/data/example.mp4",
        "/home/fcp/INA-Infra/applications/cctv/data/example.mp4",
        "/app/data/source.mp4",
    ]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c, "Local HD Benchmark Video"
    return "testsrc", "Synthetic Color Test Pattern"
