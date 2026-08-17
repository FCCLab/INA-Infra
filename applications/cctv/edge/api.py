"""CCTV FastAPI control plane (Swagger at /docs). Serves the dashboard SPA."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from edge.state import (
        CLIENT_STREAMS,
        GLOBAL_LOCK,
        format_client_name,
        mtx_path_for,
        normalize_canonical_client_id,
        snapshot_clients,
    )
except ImportError:
    from state import (
        CLIENT_STREAMS,
        GLOBAL_LOCK,
        format_client_name,
        mtx_path_for,
        normalize_canonical_client_id,
        snapshot_clients,
    )

FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/app/frontend/dist"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
RTSP_PORT = int(os.environ.get("RTSP_PORT", "8554"))
STREAM_PATH = os.environ.get("STREAM_PATH", "slicea")
YOLO_ENABLED = os.environ.get("YOLO_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
YOLO_PROCESS_PER_CLIENT = os.environ.get("YOLO_PROCESS_PER_CLIENT", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
YOLO_MODEL = os.environ.get("YOLO_MODEL", "yolov8n.pt")
YOLO_DEVICE = os.environ.get("YOLO_DEVICE", "cpu")
MTX_API = os.environ.get("MTX_API_URL", "http://127.0.0.1:9997")
MTX_HLS = os.environ.get("MTX_HLS_URL", "http://127.0.0.1:8888")
MTX_WHEP = os.environ.get("MTX_WHEP_URL", "http://127.0.0.1:8889")

app = FastAPI(
    title="CCTV Analyzer API",
    description=(
        "NeuroRAN CCTV backend. UE cameras publish RTSP RECORD into the analyzer; "
        "annotated video is published to MediaMTX. The dashboard **subscribes** via "
        "HLS (`/live/{path}/index.m3u8`) or WHEP (`/whep/{path}`). "
        "See [MediaMTX](https://github.com/bluenviron/mediamtx)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ClientOut(BaseModel):
    id: str
    name: str
    active: bool
    fps: float
    net_delay_ms: float
    yolo_delay_ms: float
    e2e_delay_ms: float
    detections_count: int
    detected_objects: List[str]
    has_frame: bool
    mtx_path: str
    mtx_publishing: bool
    publish_path: str
    hls_path: str
    whep_path: str
    mjpeg_path: str
    snapshot_path: str


class ConnectedClientItem(BaseModel):
    id: str
    name: str
    active: bool
    fps: float
    has_frame: bool
    mtx_publishing: bool


class ConnectedClientsOut(BaseModel):
    ok: bool = True
    count: int
    active_count: int
    client_ids: List[str]
    clients: List[ConnectedClientItem]


class StatusOut(BaseModel):
    app: str = "cctv"
    yolo_enabled: bool
    yolo_process_per_client: bool
    yolo_model: str
    yolo_device: str
    rtsp_port: int
    http_port: int
    stream_path: str
    frontend: bool
    mediamtx: Dict[str, Any] = Field(default_factory=dict)
    clients: List[ClientOut]


class HealthOut(BaseModel):
    ok: bool
    analyzer_clients: int
    mediamtx: bool
    frontend: bool


def _mtx_paths() -> Dict[str, Any]:
    try:
        r = httpx.get(f"{MTX_API.rstrip('/')}/v3/paths/list", timeout=1.5)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc), "items": []}


@app.get("/api/v1/health", response_model=HealthOut, tags=["Health"])
def health() -> HealthOut:
    mtx = _mtx_paths()
    return HealthOut(
        ok=True,
        analyzer_clients=len(snapshot_clients()),
        mediamtx="error" not in mtx,
        frontend=FRONTEND_DIR.is_dir(),
    )


@app.get(
    "/api/v1/connected",
    response_model=ConnectedClientsOut,
    tags=["Status"],
    summary="Get connected clients (primary backend status)",
)
@app.get("/api/connected", response_model=ConnectedClientsOut, tags=["Status"], include_in_schema=False)
@app.get("/api/v1/clients/connected", response_model=ConnectedClientsOut, tags=["Status"], include_in_schema=False)
def get_connected_clients() -> ConnectedClientsOut:
    """Fast, lightweight endpoint returning connected clients and basic stream presence without publishing data."""
    items = []
    client_ids = []
    active_cnt = 0
    for c in snapshot_clients():
        is_act = bool(c.get("active", False))
        if is_act:
            active_cnt += 1
        client_ids.append(c["id"])
        items.append(
            ConnectedClientItem(
                id=c["id"],
                name=c.get("name", c["id"]),
                active=is_act,
                fps=float(c.get("fps", 0.0)),
                has_frame=bool(c.get("has_frame", False)),
                mtx_publishing=bool(c.get("mtx_publishing", False)),
            )
        )
    return ConnectedClientsOut(
        ok=True,
        count=len(items),
        active_count=active_cnt,
        client_ids=client_ids,
        clients=items,
    )


@app.get("/api/v1/status", response_model=StatusOut, tags=["Status"])
@app.get("/api/status", response_model=StatusOut, tags=["Status"], include_in_schema=False)
def status() -> StatusOut:
    clients = [ClientOut.model_validate(c) for c in snapshot_clients()]
    return StatusOut(
        yolo_enabled=YOLO_ENABLED,
        yolo_process_per_client=YOLO_PROCESS_PER_CLIENT,
        yolo_model=YOLO_MODEL,
        yolo_device=YOLO_DEVICE,
        rtsp_port=RTSP_PORT,
        http_port=HTTP_PORT,
        stream_path=STREAM_PATH,
        frontend=FRONTEND_DIR.is_dir(),
        mediamtx=_mtx_paths(),
        clients=clients,
    )


@app.get("/api/v1/clients", response_model=Dict[str, List[ClientOut]], tags=["Clients"])
@app.get("/api/clients", response_model=Dict[str, List[ClientOut]], tags=["Clients"], include_in_schema=False)
def clients() -> Dict[str, List[ClientOut]]:
    return {"clients": [ClientOut.model_validate(c) for c in snapshot_clients()]}


@app.api_route("/snapshot/{client_id}", methods=["GET", "HEAD"], tags=["Media"], summary="JPEG snapshot")
def snapshot(client_id: str) -> Response:
    norm_id = normalize_canonical_client_id(client_id)
    jpeg = None
    with GLOBAL_LOCK:
        ctx = CLIENT_STREAMS.get(norm_id) or CLIENT_STREAMS.get(client_id)
        if ctx is not None:
            jpeg = ctx.latest_jpeg
        elif CLIENT_STREAMS:
            jpeg = next(iter(CLIENT_STREAMS.values())).latest_jpeg
    if not jpeg:
        raise HTTPException(status_code=404, detail="No frame available")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.api_route("/video/{client_id}", methods=["GET", "HEAD"], tags=["Media"], summary="MJPEG fallback subscribe")
def mjpeg(client_id: str) -> StreamingResponse:
    norm_id = normalize_canonical_client_id(client_id)

    def gen():
        last = None
        try:
            while True:
                jpeg = None
                with GLOBAL_LOCK:
                    ctx = CLIENT_STREAMS.get(norm_id) or CLIENT_STREAMS.get(client_id)
                    if ctx is None and CLIENT_STREAMS:
                        ctx = next(iter(CLIENT_STREAMS.values()))
                    if ctx is not None:
                        jpeg = ctx.latest_jpeg
                if jpeg and jpeg is not last:
                    last = jpeg
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                time.sleep(0.04)
        except (GeneratorExit, ConnectionResetError, BrokenPipeError):
            pass

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=--frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.api_route("/live/{path:path}", methods=["GET", "HEAD"], tags=["MediaMTX"], summary="HLS subscribe (proxy)")
async def live_hls(path: str, request: Request) -> Response:
    url = f"{MTX_HLS.rstrip('/')}/{path}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.request(request.method, url, params=request.query_params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"MediaMTX HLS: {exc}") from exc
    headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower() in ("content-type", "cache-control", "access-control-allow-origin")
    }
    headers.setdefault("Access-Control-Allow-Origin", "*")
    return Response(content=r.content, status_code=r.status_code, headers=headers)


@app.api_route("/whep/{path:path}", methods=["GET", "POST", "PATCH", "OPTIONS"], tags=["MediaMTX"], summary="WHEP subscribe (proxy)")
async def live_whep(path: str, request: Request) -> Response:
    url = f"{MTX_WHEP.rstrip('/')}/{path}/whep"
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.request(
                request.method,
                url,
                content=body or None,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() in ("content-type", "accept")
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"MediaMTX WHEP: {exc}") from exc
    headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower() in ("content-type", "location", "access-control-allow-origin")
    }
    headers.setdefault("Access-Control-Allow-Origin", "*")
    return Response(content=r.content, status_code=r.status_code, headers=headers)


if FRONTEND_DIR.is_dir():
    assets = FRONTEND_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="spa")
