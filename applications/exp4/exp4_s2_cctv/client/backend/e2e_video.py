#!/usr/bin/env python3
"""Slice-2 application E2E: per-stream camera + YOLO + RTSP/HLS, then mean.

Each camera has its own t_send (server mux) and t_recv (UE annotated path).

  e2e_i  = camera_ms_i + yolo_ms_i + rtsp_hls_ms_i
  e2e_ms = mean(e2e_i)   # Grafana / Influx
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

LOG = logging.getLogger("cctv.ue.e2e")

E2E_FILE = Path(os.environ.get("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms")
HLS_MS = float(os.environ.get("EXP4_S2_HLS_MS") or "100")
PERIOD_S = float(os.environ.get("EXP4_S2_E2E_PERIOD_S") or "1")
MAX_E2E_MS = float(os.environ.get("EXP4_S2_E2E_MAX_MS") or "30000")
HLS_BASE = (os.environ.get("MTX_HLS_URL") or "http://127.0.0.1:8888").rstrip("/")
NUM_STREAMS = max(1, int(os.environ.get("DS_NUM_STREAMS", "4")))
MIN_STREAMS = max(1, int(os.environ.get("EXP4_S2_E2E_MIN_STREAMS") or "1"))


def _write(ms: float) -> None:
    try:
        tmp = E2E_FILE.with_suffix(".tmp")
        tmp.write_text(f"{ms:.3f}\n", encoding="utf-8")
        tmp.replace(E2E_FILE)
    except OSError:
        pass


def _fetch_stages(server_url: str) -> dict:
    url = f"{server_url.rstrip('/')}/api/e2e"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=1.5) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    return data if isinstance(data, dict) else {}


def _combine(stages: dict, t_recv: float) -> Optional[dict[str, float]]:
    t_send = float(stages.get("t_send") or 0.0)
    if t_send < 1_000_000_000.0 or t_recv <= 0:
        return None
    age_ms = (t_recv - t_send) * 1000.0
    if age_ms < 0:
        age_ms = 0.0
    if age_ms > MAX_E2E_MS:
        return None
    camera = max(0.0, float(stages.get("camera_ms") or 0.0))
    yolo = max(0.0, float(stages.get("yolo_ms") or 0.0))
    encode = max(0.0, float(stages.get("encode_ms") or 0.0))
    rtsp_hls = max(0.0, age_ms + HLS_MS - camera - yolo)
    e2e = camera + yolo + rtsp_hls
    return {
        "id": int(stages.get("id") or 0),
        "camera_ms": round(camera, 3),
        "yolo_ms": round(yolo, 3),
        "encode_ms": round(encode, 3),
        "rtsp_hls_ms": round(rtsp_hls, 3),
        "e2e_ms": round(e2e, 3),
        "t_send": t_send,
        "t_recv": t_recv,
    }


def _hls_t_recv(cam_id: int) -> float:
    url = f"{HLS_BASE}/annotated{cam_id}/index.m3u8"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.apple.mpegurl,*/*"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            body = resp.read(512)
        if body:
            return time.time()
    except Exception:
        return 0.0
    return 0.0


def _run_rtsp_recv(url: str, holder: dict[str, Any], stop: threading.Event) -> None:
    """Prefer in-process GI; else system python3 (venv has no PyGObject)."""
    if _run_rtsp_gi(url, holder, stop):
        return
    script = Path(__file__).resolve().parent / "e2e_rtsp_probe.py"
    py = "/usr/bin/python3"
    while not stop.is_set():
        proc = None
        try:
            proc = subprocess.Popen(  # noqa: S603
                [py, str(script), url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if stop.is_set():
                    break
                if line.startswith("t_recv "):
                    try:
                        holder["t_recv"] = float(line.split()[1])
                        holder["n"] = int(holder.get("n") or 0) + 1
                    except (IndexError, ValueError):
                        pass
                elif line.startswith("error"):
                    LOG.warning("rtsp e2e probe %s: %s", url, line.strip())
        except Exception as exc:  # noqa: BLE001
            LOG.debug("rtsp e2e probe: %s", exc)
        finally:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if not stop.is_set():
            stop.wait(1.0)


def _run_rtsp_gi(url: str, holder: dict[str, Any], stop: threading.Event) -> bool:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: WPS433
    except Exception:
        return False

    Gst.init(None)
    desc = (
        f'uridecodebin uri="{url}" '
        "! videoconvert ! video/x-raw "
        "! appsink name=sink emit-signals=false max-buffers=1 drop=true sync=false"
    )
    while not stop.is_set():
        pipeline = None
        try:
            pipeline = Gst.parse_launch(desc)
            sink = pipeline.get_by_name("sink")
            pipeline.set_state(Gst.State.PLAYING)
            while not stop.is_set():
                sample = sink.emit("try-pull-sample", 200 * Gst.MSECOND)
                if sample is not None:
                    holder["t_recv"] = time.time()
                    holder["n"] = int(holder.get("n") or 0) + 1
        except Exception as exc:  # noqa: BLE001
            LOG.debug("rtsp e2e pipeline: %s", exc)
            stop.wait(2.0)
        finally:
            if pipeline is not None:
                try:
                    pipeline.set_state(Gst.State.NULL)
                except Exception:  # noqa: BLE001
                    pass
        if not stop.is_set():
            stop.wait(1.0)
    return True


def _mean(samples: list[dict[str, float]], key: str) -> float:
    return round(sum(s[key] for s in samples) / len(samples), 3)


def start(
    server_url: str,
    rtsp_url: str,
    on_sample: Optional[Callable[[dict[str, float]], None]] = None,
) -> threading.Event:
    stop = threading.Event()
    urls = [u.strip() for u in (rtsp_url or "").split("|") if u.strip()]
    n_cam = max(NUM_STREAMS, len(urls) or 1)
    if not urls:
        urls = [f"rtsp://127.0.0.1:8555/annotated{i}" for i in range(1, n_cam + 1)]
    holders: dict[int, dict[str, Any]] = {
        i: {"t_recv": 0.0, "n": 0} for i in range(1, n_cam + 1)
    }

    def _loop() -> None:
        last_log = 0.0
        while not stop.is_set():
            now = time.time()
            for cam_id, holder in holders.items():
                t_recv = float(holder.get("t_recv") or 0.0)
                if t_recv <= 0 or (now - t_recv) >= 3.0:
                    hls = _hls_t_recv(cam_id)
                    if hls > 0:
                        holder["t_recv"] = hls
                        holder["n"] = int(holder.get("n") or 0) + 1
            combined_list: list[dict[str, float]] = []
            stages: dict = {}
            try:
                stages = _fetch_stages(server_url)
                by_id = {
                    int(s.get("id") or 0): s
                    for s in (stages.get("streams") or [])
                    if isinstance(s, dict) and int(s.get("id") or 0) > 0
                }
                for cam_id, holder in holders.items():
                    t_recv = float(holder.get("t_recv") or 0.0)
                    if t_recv <= 0 or (time.time() - t_recv) >= 3.0:
                        continue
                    one = by_id.get(cam_id)
                    if not one:
                        continue
                    item = _combine(one, t_recv)
                    if item is not None:
                        combined_list.append(item)
            except Exception as exc:  # noqa: BLE001
                if (time.time() - last_log) > 15.0:
                    last_log = time.time()
                    LOG.warning("e2e stages fetch: %s", exc)
            if len(combined_list) >= MIN_STREAMS:
                avg = {
                    "camera_ms": _mean(combined_list, "camera_ms"),
                    "yolo_ms": _mean(combined_list, "yolo_ms"),
                    "encode_ms": _mean(combined_list, "encode_ms"),
                    "rtsp_hls_ms": _mean(combined_list, "rtsp_hls_ms"),
                    "e2e_ms": _mean(combined_list, "e2e_ms"),
                    "n_streams": float(len(combined_list)),
                }
                _write(avg["e2e_ms"])
                if on_sample is not None:
                    try:
                        on_sample(avg)
                    except Exception:  # noqa: BLE001
                        pass
            elif (time.time() - last_log) > 15.0:
                last_log = time.time()
                LOG.warning(
                    "s2 e2e waiting: ok=%d/%d server_streams=%d",
                    len(combined_list),
                    n_cam,
                    len(stages.get("streams") or []),
                )
            stop.wait(PERIOD_S)

    for i, url in enumerate(urls, start=1):
        cam_id = i if i <= n_cam else ((i - 1) % n_cam) + 1
        threading.Thread(
            target=_run_rtsp_recv,
            args=(url, holders[cam_id], stop),
            name=f"s2-e2e-rtsp-{cam_id}",
            daemon=True,
        ).start()
    threading.Thread(target=_loop, name="s2-e2e-mean", daemon=True).start()
    LOG.info(
        "slice-2 video E2E mean of %d streams: rtsp=%s hls_ms=%.0f",
        n_cam,
        "|".join(urls),
        HLS_MS,
    )
    return stop
