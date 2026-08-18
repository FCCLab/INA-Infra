"""Shared CCTV backend state (analyzer process + FastAPI)."""

from __future__ import annotations

import collections
import re
import threading
import time
from typing import Any, Dict, List, Optional

GLOBAL_LOCK = threading.Lock()
CLIENT_STREAMS: Dict[str, "ClientStreamContext"] = {}


def normalize_canonical_client_id(client_id: str) -> str:
    """Normalize and unify all client IDs to slicea_camX format (e.g. slicea_cam1, slicea_cam2, slicea_cam3, slicea_cam4)."""
    s = str(client_id).lower().strip()
    m = re.search(r"cam[_-]?(\d+)", s)
    if m:
        return f"slicea_cam{m.group(1)}"
    if s in ("slicea", "default", "cam"):
        return "slicea_cam1"
    m = re.search(r"(\d+)", s)
    if m:
        return f"slicea_cam{m.group(1)}"
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")
    return f"slicea_{clean}" if clean else "slicea_cam1"


def format_client_name(client_id: str) -> str:
    """Format a consistent camera display name across all 4 slices/clients.

    Examples:
      'slicea' or 'slicea_cam1' -> 'Camera 1'
      'slicea_cam2'             -> 'Camera 2'
      'slicea_cam3'             -> 'Camera 3'
      'slicea_cam4'             -> 'Camera 4'
    """
    s = str(client_id).lower().strip()
    m = re.search(r"cam[_-]?(\d+)", s)
    if m:
        return f"Camera {m.group(1)}"
    if s in ("slicea", "default", "cam", "slicea_cam1", "slicea_1"):
        return "Camera 1"
    m = re.search(r"(\d+)", s)
    if m:
        return f"Camera {m.group(1)}"
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().title()
    return f"Camera {clean}" if not clean.lower().startswith("camera") else clean


def mtx_path_for(client_id: str) -> str:
    cid = normalize_canonical_client_id(client_id)
    return f"cam_{cid}"


class ClientStreamContext:
    """Tracks state and latest frames for a single client stream."""

    def __init__(self, client_id: str):
        self.client_id = normalize_canonical_client_id(client_id)
        self.name = format_client_name(client_id)
        self.decoded_count = 0
        self.analyzed_count = 0
        self.yolo_seq = 0
        self.last_decoded_count = 0
        self.last_analyzed_count = 0
        self.last_report = time.monotonic()
        self.fps = 0.0
        self.egress_fps = 0.0
        self.net_delay_ms = 0.0
        self.yolo_delay_ms = 0.0
        self.e2e_delay_ms = 0.0
        self.min_net_delay_s: Optional[float] = None
        self.recent_raw_delays = collections.deque(maxlen=30)
        self.detected_objects: List[str] = []
        self.detections_count = 0
        self.latest_jpeg: Optional[bytes] = None
        self.last_frame_time = time.monotonic()
        self.bytes_in = 0
        self.last_bytes_in = 0
        self.throughput_mbps = 0.0
        self.net_samples: list[float] = []
        self.yolo_samples: list[float] = []
        self.e2e_samples: list[float] = []
        self.mtx_path = mtx_path_for(client_id)
        self.mtx_publishing = False


def snapshot_clients(*, stale_s: float = 5.0) -> List[Dict[str, Any]]:
    now = time.monotonic()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    with GLOBAL_LOCK:
        def _sort_key(ctx: ClientStreamContext) -> int:
            m = re.search(r"(\d+)", ctx.client_id)
            return int(m.group(1)) if m else (1 if "slicea" in ctx.client_id else 99)

        for ctx in sorted(CLIENT_STREAMS.values(), key=_sort_key):
            if ctx.client_id in seen:
                continue
            seen.add(ctx.client_id)
            is_active = (now - ctx.last_frame_time) < stale_s
            net_ms = round(ctx.net_delay_ms, 1)
            yolo_ms = round(ctx.yolo_delay_ms, 1)
            e2e_ms = round(ctx.e2e_delay_ms, 1)
            if e2e_ms < (net_ms + yolo_ms) and yolo_ms > 0:
                e2e_ms = round(net_ms + yolo_ms, 1)
            out.append(
                {
                    "id": ctx.client_id,
                    "name": ctx.name,
                    "active": is_active,
                    "fps": round(ctx.fps, 1),
                    "net_delay_ms": net_ms,
                    "yolo_delay_ms": yolo_ms,
                    "e2e_delay_ms": e2e_ms,
                    "detections_count": ctx.detections_count,
                    "detected_objects": list(ctx.detected_objects or []),
                    "has_frame": ctx.latest_jpeg is not None,
                    "mtx_path": ctx.mtx_path,
                    "mtx_publishing": bool(ctx.mtx_publishing),
                    "publish_path": f"rtsp://127.0.0.1:8555/{ctx.mtx_path}",
                    "hls_path": f"/live/{ctx.mtx_path}/index.m3u8",
                    "whep_path": f"/whep/{ctx.mtx_path}",
                    "mjpeg_path": f"/video/{ctx.client_id}",
                    "snapshot_path": f"/snapshot/{ctx.client_id}",
                }
            )
    return out
