"""FastAPI server for OTT Video Portal, MediaMTX proxying, and UE client orchestration."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from prometheus_client import Gauge

from server.state import (
    CHANNELS,
    GLOBAL_LOCK,
    OttChannel,
    get_channel,
    get_client,
    list_channels,
    list_clients,
    list_videos,
    select_video_for_client,
    set_client_channel,
    set_client_state,
    update_client_heartbeat,
)

logger = logging.getLogger("ott.api")

app = FastAPI(
    title="OTT Video Streaming & UE Console API",
    version="2.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MTX_HLS = os.environ.get("MTX_HLS_URL", "http://127.0.0.1:8888").rstrip("/")
MTX_WHEP = os.environ.get("MTX_WHEP_URL", "http://127.0.0.1:8889").rstrip("/")
MTX_API = os.environ.get("MTX_API_URL", "http://127.0.0.1:9997").rstrip("/")

HTTPX_CLIENT = httpx.AsyncClient(timeout=5.0)

APP_UE_LATENCY_MS = Gauge(
    "app_ue_latency_ms", "Per-UE application latency (milliseconds)", ["ue_id"]
)
APP_UE_THROUGHPUT_MBPS = Gauge(
    "app_ue_throughput_mbps", "Per-UE application throughput (Mbps)", ["ue_id"]
)
APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
APP_THROUGHPUT_MBPS = Gauge(
    "app_throughput_mbps", "Aggregated application throughput (Mbps)"
)
_slo_ues: set[str] = set()


def _refresh_slo_gauges() -> None:
    clients = list_clients(alive_only=True)
    live = {str(c.get("id") or c.get("name") or "ue") for c in clients}
    for stale in list(_slo_ues - live):
        try:
            APP_UE_LATENCY_MS.remove(stale)
            APP_UE_THROUGHPUT_MBPS.remove(stale)
        except KeyError:
            pass
        _slo_ues.discard(stale)
    lats = []
    tput = 0.0
    for c in clients:
        uid = str(c.get("id") or c.get("name") or "ue")
        lat = float(c.get("net_delay_ms") or 0.0)
        mbps = float(c.get("rx_bitrate_mbps") or 0.0)
        APP_UE_LATENCY_MS.labels(ue_id=uid).set(lat)
        APP_UE_THROUGHPUT_MBPS.labels(ue_id=uid).set(mbps)
        _slo_ues.add(uid)
        lats.append(lat)
        tput += mbps
    APP_LATENCY_MS.set((sum(lats) / len(lats)) if lats else 0.0)
    APP_THROUGHPUT_MBPS.set(round(tput, 3))

# Global engine pointer set by main entrypoint
OTT_ENGINE = None


class PlayRequest(BaseModel):
    youtube_url: str = Field(..., description="YouTube video URL or Video ID")
    title: Optional[str] = Field(None, description="Optional custom channel title")
    category: Optional[str] = Field("User Stream", description="Video category")


class ClientStateRequest(BaseModel):
    state: str = Field(..., description="STREAMING, STOPPED, or IDLE")


class ClientChannelRequest(BaseModel):
    channel_id: str = Field(..., description="Target Channel ID (e.g. channel_1)")


class SelectVideoRequest(BaseModel):
    video_id: str = Field(..., description="Video / channel id from GET /api/v1/videos")


class ClientHeartbeatRequest(BaseModel):
    client_id: str
    ip: Optional[str] = None
    console_ip: Optional[str] = None
    console_mac: Optional[str] = None
    name: Optional[str] = None
    pdu_ip: Optional[str] = None
    state: Optional[str] = None
    net_delay_ms: Optional[float] = None
    rx_fps: Optional[float] = None
    rx_bitrate_mbps: Optional[float] = None
    dropped_frames: Optional[int] = None
    total_frames: Optional[int] = None



@app.get("/api/v1/health", tags=["System"])
async def health():
    return {
        "ok": True,
        "app": "application-ott",
        "timestamp": time.time(),
    }


@app.get("/api/v1/status", tags=["System"])
async def status():
    channels = list_channels()
    clients = list_clients(alive_only=True)
    active_streamers = len([c for c in clients if c.get("state") == "STREAMING"])
    total_bitrate = sum(c.get("rx_bitrate_mbps", 0.0) for c in clients)

    return {
        "ok": True,
        "channels_count": len(channels),
        "clients_count": len(clients),
        "active_streaming_clients": active_streamers,
        "total_downlink_throughput_mbps": round(total_bitrate, 2),
        "channels": channels,
        "videos": list_videos(),
        "clients": clients,
    }


# --- Channel / Video catalog -----------------------------------------------

@app.get("/api/v1/channels", tags=["Channels"])
async def get_channels():
    return {
        "ok": True,
        "channels": list_channels(),
    }


@app.get("/api/v1/videos", tags=["Videos"])
async def get_videos():
    """UE-facing video catalog with absolute MediaMTX HLS / WHEP / RTSP URLs."""
    return {
        "ok": True,
        "videos": list_videos(),
    }


@app.get("/api/v1/videos/{video_id}/mediamtx", tags=["Videos"])
async def get_video_mediamtx(video_id: str):
    """Return MediaMTX subscribe links for a selected video (no client binding)."""
    ch = get_channel(video_id)
    if not ch or not ch.is_active:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"ok": True, **ch.play_urls(), "video": ch.to_dict()}


@app.get("/api/v1/channels/{channel_id}", tags=["Channels"])
async def get_single_channel(channel_id: str):
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True, "channel": ch.to_dict(), **ch.play_urls()}


@app.post("/api/v1/channels/{channel_id}/play", tags=["Channels"])
async def play_youtube_on_channel(channel_id: str, req: PlayRequest):
    """Set a custom YouTube video URL or ID to play on a specific channel."""
    with GLOBAL_LOCK:
        ch = CHANNELS.get(channel_id)
        if not ch:
            ch = OttChannel(
                id=channel_id,
                name=req.title or f"Custom YouTube ({channel_id})",
                category=req.category or "Custom",
                source_type="youtube",
                source_url=req.youtube_url,
                hls_path=f"/live/{channel_id}/index.m3u8",
                whep_path=f"/whep/{channel_id}",
                rtsp_path=f"rtsp://127.0.0.1:8555/{channel_id}",
            )
            CHANNELS[channel_id] = ch
        else:
            ch.source_url = req.youtube_url
            ch.source_type = "youtube"
            if req.title:
                ch.name = req.title

    if OTT_ENGINE:
        OTT_ENGINE.restart_channel(channel_id)

    return {"ok": True, "channel": ch.to_dict(), "msg": f"Playing {req.youtube_url} on {channel_id}"}


# --- Connected UE Client Management Endpoints ------------------------------

@app.get("/api/v1/clients", tags=["Clients"])
async def get_clients(alive_only: bool = False):
    """List connected UEs (default: all registered clients) with UE console links."""
    return {
        "ok": True,
        "clients": list_clients(alive_only=alive_only),
    }



@app.post("/api/v1/clients/{client_id}/start", tags=["Clients"])
async def start_client_stream(client_id: str):
    """Command a specific UE client to start receiving video downlink."""
    if not set_client_state(client_id, "STREAMING"):
        raise HTTPException(status_code=404, detail="Client not found")
    cl = get_client(client_id)
    return {"ok": True, "client": cl.to_dict() if cl else {}, "msg": f"Client {client_id} started streaming"}


@app.post("/api/v1/clients/{client_id}/stop", tags=["Clients"])
async def stop_client_stream(client_id: str):
    """Command a specific UE client to stop receiving video downlink."""
    if not set_client_state(client_id, "STOPPED"):
        raise HTTPException(status_code=404, detail="Client not found")
    cl = get_client(client_id)
    return {"ok": True, "client": cl.to_dict() if cl else {}, "msg": f"Client {client_id} stopped streaming"}


@app.post("/api/v1/clients/{client_id}/channel", tags=["Clients"])
async def switch_client_channel(client_id: str, req: ClientChannelRequest):
    """Assign or switch the video channel streamed to a specific UE client."""
    if not set_client_channel(client_id, req.channel_id):
        raise HTTPException(status_code=400, detail="Invalid client or channel ID")
    cl = get_client(client_id)
    ch = get_channel(req.channel_id)
    urls = ch.play_urls() if ch else {}
    return {
        "ok": True,
        "client": cl.to_dict() if cl else {},
        "msg": f"Client {client_id} switched to {req.channel_id}",
        **urls,
    }


@app.post("/api/v1/clients/{client_id}/select", tags=["Clients"])
async def client_select_video(client_id: str, req: SelectVideoRequest):
    """UE selects a video; server returns MediaMTX links for that UE to play."""
    result = select_video_for_client(client_id, req.video_id)
    if not result:
        raise HTTPException(status_code=404, detail="Video not found")
    return result


class ProbeRequest(BaseModel):
    client_id: str = ""
    t_send: float = 0.0


@app.post("/api/v1/probe", tags=["Clients"])
async def latency_probe(req: ProbeRequest):
    """Tiny echo for UE probe thread. RTT rises when YouTube saturates the PDU."""
    now = time.time()
    return {
        "ok": True,
        "client_id": req.client_id,
        "t_send": req.t_send,
        "t_recv": now,
    }


@app.post("/api/v1/clients/heartbeat", tags=["Clients"])
async def client_heartbeat(req: ClientHeartbeatRequest):
    """Called by UE clients to register, update telemetry, and sync play state."""
    cl = update_client_heartbeat(
        client_id=req.client_id,
        ip=req.ip,
        console_ip=req.console_ip,
        console_mac=req.console_mac,
        name=req.name,
        pdu_ip=req.pdu_ip,
        state=req.state,
        net_delay_ms=req.net_delay_ms,
        rx_fps=req.rx_fps,
        rx_bitrate_mbps=req.rx_bitrate_mbps,
        dropped_frames=req.dropped_frames,
        total_frames=req.total_frames,
    )

    ch = get_channel(cl.assigned_channel) if cl.assigned_channel else None
    urls = ch.play_urls() if ch else {
        "hls_url": cl.play_hls_url,
        "rtsp_url": cl.play_rtsp_url,
        "whep_url": "",
        "video_id": cl.selected_video_id or "",
        "play_mode": "youtube",
        "youtube_id": "",
        "youtube_url": cl.play_hls_url or "",
        "embed_url": "",
    }
    _refresh_slo_gauges()
    return {
        "ok": True,
        "state": cl.state,
        "assigned_channel": cl.assigned_channel,
        "selected_video_id": cl.selected_video_id,
        "play_mode": urls.get("play_mode") or "youtube",
        "youtube_id": urls.get("youtube_id") or "",
        "youtube_url": urls.get("youtube_url") or "",
        "embed_url": urls.get("embed_url") or "",
        "hls_url": urls.get("hls_url") or cl.play_hls_url,
        "rtsp_url": urls.get("rtsp_url") or cl.play_rtsp_url,
        "whep_url": urls.get("whep_url") or "",
        "console_url": cl.console_url,
    }


# --- MediaMTX Proxy Endpoints ----------------------------------------------


# --- MediaMTX Proxy Endpoints ----------------------------------------------

@app.api_route("/live/{path:path}", methods=["GET", "HEAD"], tags=["MediaMTX"])
async def live_hls(path: str, request: Request) -> Response:
    url = f"{MTX_HLS}/{path}"
    try:
        req = HTTPX_CLIENT.build_request(
            request.method,
            url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
            params=request.query_params,
        )
        res = await HTTPX_CLIENT.send(req, stream=True)
        headers = dict(res.headers)
        headers["Access-Control-Allow-Origin"] = "*"
        return Response(content=await res.aread(), status_code=res.status_code, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MediaMTX HLS unavailable: {e}")


@app.api_route("/whep/{path:path}", methods=["POST", "OPTIONS"], tags=["MediaMTX"])
async def whep_proxy(path: str, request: Request) -> Response:
    url = f"{MTX_WHEP}/{path}"
    body = await request.body()
    try:
        req = HTTPX_CLIENT.build_request(
            request.method,
            url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host",)},
            content=body,
        )
        res = await HTTPX_CLIENT.send(req)
        headers = dict(res.headers)
        headers["Access-Control-Allow-Origin"] = "*"
        return Response(content=res.content, status_code=res.status_code, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MediaMTX WHEP unavailable: {e}")
