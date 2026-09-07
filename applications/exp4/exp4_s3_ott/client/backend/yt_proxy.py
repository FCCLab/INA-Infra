"""YouTube URL helpers (shared by UE Chromium play path)."""
from __future__ import annotations

import re
from typing import Optional


def extract_youtube_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    raw = url_or_id.strip()
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|v=)([\w-]{11})",
        raw,
    )
    return m.group(1) if m else None
