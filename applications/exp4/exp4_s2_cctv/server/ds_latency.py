#!/usr/bin/env python3
"""Per-stream Slice-2 stage clocks (one YOLO graph per camera).

ds_pipeline pad probes write this file; ds_main /api/e2e reads it.
Client measures each annotated path, then publishes the mean E2E:

  e2e_i  = camera_ms_i + yolo_ms_i + rtsp_hls_ms_i
  e2e_ms = mean(e2e_i)
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

FILE = Path(os.environ.get("EXP4_S2_STAGE_FILE") or "/tmp/exp4_s2_stage_ms.json")
ALPHA = float(os.environ.get("EXP4_S2_STAGE_EMA", "0.25"))
FLUSH_EVERY = max(1, int(os.environ.get("EXP4_S2_STAGE_FLUSH_N", "1")))

_lock = threading.Lock()
_ema: Dict[int, Dict[str, Optional[float]]] = {}
_pending: Dict[int, Dict[str, float]] = {}
_t_send: Dict[int, float] = {}
_sink_n = 0


def _slot(idx: int) -> Dict[str, Optional[float]]:
    return _ema.setdefault(idx, {"camera_ms": None, "yolo_ms": None, "encode_ms": None})


def _smooth(idx: int, key: str, sample_ms: float) -> None:
    slot = _slot(idx)
    prev = slot[key]
    if prev is None:
        slot[key] = max(0.0, sample_ms)
    else:
        slot[key] = ALPHA * max(0.0, sample_ms) + (1.0 - ALPHA) * prev


def mark_mux(stream_idx: int, t_wall: Optional[float] = None, t_mono: Optional[float] = None) -> None:
    """Raw frame enters nvstreammux (after camera/MTX). t_send is before YOLO."""
    wall = time.time() if t_wall is None else t_wall
    mono = time.monotonic() if t_mono is None else t_mono
    with _lock:
        _t_send[stream_idx] = wall
        _pending[stream_idx] = {"t_mux": mono, "t_send": wall}


def mark_pgie_in(stream_idx: int, t_mono: Optional[float] = None) -> None:
    """nvinfer sink: camera ingest = mux → YOLO start."""
    mono = time.monotonic() if t_mono is None else t_mono
    with _lock:
        p = _pending.get(stream_idx)
        if not p or "t_mux" not in p:
            _t_send[stream_idx] = time.time()
            _pending[stream_idx] = {"t_pgie_in": mono, "t_send": _t_send[stream_idx]}
            return
        _smooth(stream_idx, "camera_ms", (mono - p["t_mux"]) * 1000.0)
        p["t_pgie_in"] = mono


def mark_pgie_out(stream_idx: int, t_mono: Optional[float] = None) -> None:
    """nvinfer src: YOLO inference duration."""
    mono = time.monotonic() if t_mono is None else t_mono
    snap = None
    with _lock:
        p = _pending.get(stream_idx)
        if not p or "t_pgie_in" not in p:
            return
        _smooth(stream_idx, "yolo_ms", (mono - p["t_pgie_in"]) * 1000.0)
        p["t_pgie_out"] = mono
        snap = _snapshot_unlocked()
    if snap is not None:
        _write(snap)


def mark_encoded(stream_idx: int, t_mono: Optional[float] = None) -> None:
    """rtspclientsink: OSD + encode + publish into MediaMTX annotated path."""
    global _sink_n
    mono = time.monotonic() if t_mono is None else t_mono
    with _lock:
        p = _pending.get(stream_idx)
        if p and "t_pgie_out" in p:
            _smooth(stream_idx, "encode_ms", (mono - p["t_pgie_out"]) * 1000.0)
        _sink_n += 1
        n = _sink_n
        snap = _snapshot_unlocked()
    if n % FLUSH_EVERY == 0:
        _write(snap)


def snapshot() -> Dict[str, Any]:
    with _lock:
        return _snapshot_unlocked()


def _one(idx: int) -> Dict[str, Any]:
    slot = _ema.get(idx) or {}
    camera = float(slot.get("camera_ms") or 0.0)
    yolo = float(slot.get("yolo_ms") or 0.0)
    encode = float(slot.get("encode_ms") or 0.0)
    t_send = float(_t_send.get(idx) or 0.0)
    out: Dict[str, Any] = {
        "id": idx + 1,
        "camera_ms": round(camera, 3),
        "yolo_ms": round(yolo, 3),
        "encode_ms": round(encode, 3),
        "server_ms": round(camera + yolo + encode, 3),
    }
    if t_send >= 1_000_000_000.0:
        out["t_send"] = t_send
    return out


def _snapshot_unlocked() -> Dict[str, Any]:
    idxs = sorted(set(_ema) | set(_t_send) | set(_pending))
    streams = [_one(i) for i in idxs]
    cams = [s["camera_ms"] for s in streams]
    yolos = [s["yolo_ms"] for s in streams]
    encs = [s["encode_ms"] for s in streams]
    out: Dict[str, Any] = {"ok": True, "streams": streams}
    if streams:
        n = float(len(streams))
        out["camera_ms"] = round(sum(cams) / n, 3)
        out["yolo_ms"] = round(sum(yolos) / n, 3)
        out["encode_ms"] = round(sum(encs) / n, 3)
        out["server_ms"] = round(out["camera_ms"] + out["yolo_ms"] + out["encode_ms"], 3)
    else:
        out["camera_ms"] = 0.0
        out["yolo_ms"] = 0.0
        out["encode_ms"] = 0.0
        out["server_ms"] = 0.0
    return out


def read_file(max_age_s: float = 5.0) -> Optional[Dict[str, Any]]:
    try:
        st = FILE.stat()
        if max_age_s > 0 and (time.time() - st.st_mtime) > max_age_s:
            return None
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write(payload: Dict[str, Any]) -> None:
    try:
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        tmp.replace(FILE)
    except OSError:
        pass
