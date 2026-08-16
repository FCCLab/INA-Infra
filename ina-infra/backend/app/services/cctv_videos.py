"""Sample clips for CCTV UE publishers (one distinct file per client).

Clips are Intel IoT sample videos, fetched at container start via VIDEO_URL
into emptyDir /data (same path as VIDEO_SOURCE).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SAMPLE_BASE = (
    "https://github.com/intel-iot-devkit/sample-videos/raw/master"
)

# Distinct scenes so multi-camera walls are visually different.
CLIPS: List[Dict[str, str]] = [
    {"id": "classroom", "file": "classroom.mp4", "label": "Classroom"},
    {"id": "car-detection", "file": "car-detection.mp4", "label": "Street / cars"},
    {"id": "people-detection", "file": "people-detection.mp4", "label": "People"},
    {"id": "store-aisle", "file": "store-aisle-detection.mp4", "label": "Store aisle"},
    {"id": "person-bicycle-car", "file": "person-bicycle-car-detection.mp4", "label": "Bike / traffic"},
    {"id": "worker-zone", "file": "worker-zone-detection.mp4", "label": "Worker zone"},
    {"id": "one-by-one-person", "file": "one-by-one-person-detection.mp4", "label": "Lobby / people"},
    {"id": "bottle-detection", "file": "bottle-detection.mp4", "label": "Bottles"},
    {"id": "face-walking", "file": "face-demographics-walking.mp4", "label": "Walking faces"},
    {"id": "head-pose-male", "file": "head-pose-face-detection-male.mp4", "label": "Head pose (male)"},
    {"id": "head-pose-female", "file": "head-pose-face-detection-female.mp4", "label": "Head pose (female)"},
    {"id": "face-walking-pause", "file": "face-demographics-walking-and-pause.mp4", "label": "Walking + pause"},
]

_BY_ID = {c["id"]: c for c in CLIPS}
_BY_FILE = {c["file"]: c for c in CLIPS}


def _clip_or_default(clip_id: str) -> Dict[str, str]:
    return _BY_ID.get(clip_id) or CLIPS[0]


def parse_clip_ids(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        out: List[str] = []
        for item in raw:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return []


def clip_for_client(
    params: Optional[Mapping[str, Any]] = None,
    client_index: int = 1,
) -> Tuple[str, str, str]:
    """Return (video_source, video_url, label) for this UE camera index (1-based)."""
    p = dict(params or {})
    idx = max(int(client_index), 1)
    catalog_ids = [c["id"] for c in CLIPS]
    ids = parse_clip_ids(p.get("video_clip_ids"))
    if not ids:
        ids = list(catalog_ids)
    else:
        used = set(ids)
        for cid in catalog_ids:
            if len(ids) >= idx:
                break
            if cid not in used:
                ids.append(cid)
                used.add(cid)
        while len(ids) < idx:
            ids.append(catalog_ids[len(ids) % len(catalog_ids)])
    clip = _clip_or_default(ids[idx - 1])
    source = f"/data/{clip['file']}"
    url = f"{SAMPLE_BASE}/{clip['file']}"
    return source, url, clip["label"]
