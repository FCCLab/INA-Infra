"""Shared CCTV backend state (analyzer process + FastAPI)."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

GLOBAL_LOCK = threading.Lock()
CLIENT_STREAMS: Dict[str, "ClientStreamContext"] = {}


class ClientStreamContext:
    """Tracks state and latest frames for a single client stream."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.name = f"Camera {client_id.upper()}"
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
        self.detected_objects: List[str] = []
        self.detections_count = 0
        self.latest_jpeg: Optional[bytes] = None
        self.last_frame_time = time.monotonic()
        self.net_samples: list[float] = []
        self.yolo_samples: list[float] = []
        self.e2e_samples: list[float] = []
        self.mtx_path = mtx_path_for(client_id)
        self.mtx_publishing = False


def mtx_path_for(client_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(client_id)).strip("_") or "cam"
    return f"cam_{safe}"


def snapshot_clients(*, stale_s: float = 5.0) -> List[Dict[str, Any]]:
    now = time.monotonic()
    out: List[Dict[str, Any]] = []
    with GLOBAL_LOCK:
        for cid, ctx in CLIENT_STREAMS.items():
            is_active = (now - ctx.last_frame_time) < stale_s
            net_ms = round(ctx.net_delay_ms, 1)
            yolo_ms = round(ctx.yolo_delay_ms, 1)
            e2e_ms = round(ctx.e2e_delay_ms, 1)
            if e2e_ms < (net_ms + yolo_ms) and yolo_ms > 0:
                e2e_ms = round(net_ms + yolo_ms, 1)
            out.append(
                {
                    "id": cid,
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
                    "mjpeg_path": f"/video/{cid}",
                    "snapshot_path": f"/snapshot/{cid}",
                }
            )
    return out
