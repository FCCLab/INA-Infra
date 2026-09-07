#!/usr/bin/env python3
"""DeepStream mode control plane: status API for N raw + annotated MediaMTX paths."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

LOG = logging.getLogger("ds_main")

DS_NUM_STREAMS = max(1, int(os.environ.get("DS_NUM_STREAMS", "4")))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
MTX_RTSP = os.environ.get("MTX_RTSP_URL", "rtsp://127.0.0.1:8555").rstrip("/")
MTX_API = os.environ.get("MTX_API_URL", "http://127.0.0.1:9997")
MTX_HLS = os.environ.get("MTX_HLS_URL", "http://127.0.0.1:8888")
MTX_WHEP = os.environ.get("MTX_WHEP_URL", "http://127.0.0.1:8889")
DASHBOARD_STATIC = os.environ.get("DASHBOARD_STATIC", "/app/frontend-console/static")
YOLO_BACKEND = os.environ.get("YOLO_BACKEND", "deepstream")

_start = time.time()
_feed_stop: Optional[threading.Event] = None
_ds_proc: Optional[subprocess.Popen] = None


app = FastAPI(title="Exp4 S2 DeepStream YOLO", version="1.0.0", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _stream_list() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for i in range(1, DS_NUM_STREAMS + 1):
        raw = f"raw/cam{i}"
        ann = f"annotated/cam{i}"
        items.append(
            {
                "id": i,
                "name": f"Camera {i}",
                "raw_path": raw,
                "annotated_path": ann,
                "raw_rtsp": f"{MTX_RTSP}/{raw}",
                "annotated_rtsp": f"{MTX_RTSP}/{ann}",
                "hls_path": f"/live/{ann}/index.m3u8",
                "whep_path": f"/whep/{ann}/whep",
                "hls_url": f"{MTX_HLS}/{ann}/index.m3u8",
                "whep_url": f"{MTX_WHEP}/{ann}/whep",
            }
        )
    return items


@app.get("/api/health")
@app.get("/api/v1/health")
@app.get("/health")
@app.get("/healthz")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "backend": YOLO_BACKEND,
        "num_streams": DS_NUM_STREAMS,
        "uptime_s": int(time.time() - _start),
        "ds_running": bool(_ds_proc and _ds_proc.poll() is None),
    }


@app.post("/api/v1/clients/heartbeat")
@app.post("/api/v1/clients/register")
@app.post("/api/clients/heartbeat")
def client_heartbeat() -> Dict[str, Any]:
    """Compatibility stubs for legacy UE client probes."""
    return {"ok": True, "backend": YOLO_BACKEND, "num_streams": DS_NUM_STREAMS}


@app.get("/api/status")
@app.get("/api/v1/status")
def status() -> Dict[str, Any]:
    streams = _stream_list()
    return {
        "ok": True,
        "backend": YOLO_BACKEND,
        "yolo_backend": YOLO_BACKEND,
        "num_streams": DS_NUM_STREAMS,
        "mtx_rtsp": MTX_RTSP,
        "mtx_api": MTX_API,
        "uptime_s": int(time.time() - _start),
        "ds_running": bool(_ds_proc and _ds_proc.poll() is None),
        "streams": streams,
        "clients": [
            {
                "id": f"cam{s['id']}",
                "name": s["name"],
                "active": True,
                "mtx_path": s["annotated_path"],
                "mtx_publishing": True,
                "hls_path": s["hls_path"],
                "whep_path": s["whep_path"],
            }
            for s in streams
        ],
    }


@app.get("/api/v1/clients")
@app.get("/api/clients")
def clients() -> Dict[str, Any]:
    st = status()
    return {"clients": st["clients"]}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/console/")


def _prepare_pgie_configs() -> str:
    """Write one batch-size=1 PGIE config per stream (dedicated YOLO engines).

    All streams share the same on-disk TensorRT engine file (b1). Each nvinfer
    loads its own runtime context so labels cannot cross stream_ids.
    Returns the config directory path (also exported as DS_PGIE_CONFIG_DIR).
    """
    candidates = [
        os.environ.get("DS_PGIE_STOCK", ""),
        "/opt/DeepStream-Yolo/config_infer_primary_yoloV8.txt",
        "/app/edge/ds_configs/config_infer_primary_yoloV8.txt",
    ]
    src = next((p for p in candidates if p and os.path.isfile(p)), "")
    if not src:
        raise FileNotFoundError(
            "DeepStream-Yolo PGIE config not found "
            "(expected /opt/DeepStream-Yolo/config_infer_primary_yoloV8.txt)"
        )
    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    if "Primary_Detector" in raw or "resnet18_trafficcamnet" in raw:
        raise RuntimeError(f"refusing stock Primary_Detector PGIE config: {src}")

    out_dir = "/tmp/ds_pgie"
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs("/models", exist_ok=True)
    # DeepStream-Yolo NvDsInferYoloCudaEngineGet serializes
    # cwd/model_b1_gpu0_fp16.engine — not model-engine-file. Use that name and
    # alias the previous path so a cached engine is reused across streams.
    engine = "/models/model_b1_gpu0_fp16.engine"
    _sync_engine_file(engine)

    for cam in range(1, DS_NUM_STREAMS + 1):
        out = os.path.join(out_dir, f"config_infer_yoloV8_s{cam}.txt")
        lines: List[str] = []
        for line in raw.splitlines(keepends=True):
            key = line.split("=", 1)[0].strip().lower()
            if key == "batch-size":
                lines.append("batch-size=1\n")
            elif key == "model-engine-file":
                lines.append(f"model-engine-file={engine}\n")
            elif key == "gie-unique-id":
                lines.append(f"gie-unique-id={cam}\n")
            else:
                lines.append(line if line.endswith("\n") else line + "\n")
        with open(out, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        LOG.info("dedicated PGIE cam%d -> %s (engine=%s gie-id=%d)", cam, out, engine, cam)

    # Keep a single-file fallback for older tooling.
    legacy = "/tmp/config_infer_primary_yoloV8_batch.txt"
    with open(os.path.join(out_dir, "config_infer_yoloV8_s1.txt"), "r", encoding="utf-8") as fh:
        first = fh.read()
    with open(legacy, "w", encoding="utf-8") as fh:
        fh.write(first)
    os.environ["DS_PGIE_CONFIG"] = legacy
    os.environ["DS_PGIE_CONFIG_DIR"] = out_dir
    LOG.info(
        "DeepStream dedicated YOLO: %d configs in %s (shared b1 engine %s) from %s",
        DS_NUM_STREAMS,
        out_dir,
        engine,
        src,
    )
    return out_dir


def _sync_engine_file(dest: str) -> None:
    """Copy a previously built TensorRT engine onto dest if dest is missing.

    The custom YOLO engine builder writes ``model_b1_gpu0_fp16.engine`` into
    the process cwd (often ``/app``), while PGIE configs look under ``/models``.
    Without this copy every nvinfer rebuilds (~20 min each).
    """
    os.makedirs(os.path.dirname(dest) or "/models", exist_ok=True)
    aliases = [
        dest,
        "/models/model_b1_gpu0_fp16.engine",
        "/models/yolov8n_b1_gpu0_fp16.engine",
        "/app/model_b1_gpu0_fp16.engine",
    ]
    src = next(
        (p for p in aliases if os.path.isfile(p) and os.path.getsize(p) > 1_000_000),
        "",
    )
    if not src:
        LOG.info("no cached TensorRT engine yet (first boot will build one)")
        return
    for path in (dest, "/models/yolov8n_b1_gpu0_fp16.engine"):
        if os.path.abspath(src) == os.path.abspath(path):
            continue
        if os.path.isfile(path) and os.path.getsize(path) == os.path.getsize(src):
            continue
        shutil.copy2(src, path)
        LOG.info("cached TensorRT engine %s -> %s", src, path)


def _start_workers() -> None:
    global _feed_stop, _ds_proc
    from ds_feed import start_feeders  # noqa: WPS433

    _feed_stop, _ = start_feeders(DS_NUM_STREAMS)
    _prepare_pgie_configs()
    # Settle so feeders attach to MediaMTX before DeepStream connects.
    time.sleep(float(os.environ.get("DS_FEED_SETTLE_S", "8")))
    _ds_proc = subprocess.Popen(
        [sys.executable, "/app/edge/ds_pipeline.py"],
        env=os.environ.copy(),
        cwd="/models",
    )
    LOG.info("DeepStream dedicated pipeline pid=%s streams=%d", _ds_proc.pid, DS_NUM_STREAMS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # Ensure /app/edge is importable when executed as a script path.
    edge_dir = os.path.dirname(os.path.abspath(__file__))
    if edge_dir not in sys.path:
        sys.path.insert(0, edge_dir)
    _start_workers()
    if os.path.isdir(DASHBOARD_STATIC):
        app.mount("/console", StaticFiles(directory=DASHBOARD_STATIC, html=True), name="console")
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")


if __name__ == "__main__":
    main()
