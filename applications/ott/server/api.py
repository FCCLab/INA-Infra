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

from server.state import (
    CHANNELS,
    GLOBAL_LOCK,
    OttChannel,
    get_channel,
    get_client,
    list_channels,
    list_clients,
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


class ClientHeartbeatRequest(BaseModel):
    client_id: str
    ip: Optional[str] = None
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
    clients = list_clients()
    active_streamers = len([c for c in clients if c.get("state") == "STREAMING"])
    total_bitrate = sum(c.get("rx_bitrate_mbps", 0.0) for c in clients)

    return {
        "ok": True,
        "channels_count": len(channels),
        "clients_count": len(clients),
        "active_streaming_clients": active_streamers,
        "total_downlink_throughput_mbps": round(total_bitrate, 2),
        "channels": channels,
        "clients": clients,
    }


# --- Channel Management Endpoints ------------------------------------------

@app.get("/api/v1/channels", tags=["Channels"])
async def get_channels():
    return {
        "ok": True,
        "channels": list_channels(),
    }


@app.get("/api/v1/channels/{channel_id}", tags=["Channels"])
async def get_single_channel(channel_id: str):
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True, "channel": ch.to_dict()}


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
async def get_clients():
    """List all connected UEs, their stream reception state, and 5G downlink metrics."""
    return {
        "ok": True,
        "clients": list_clients(),
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
    return {
        "ok": True,
        "client": cl.to_dict() if cl else {},
        "msg": f"Client {client_id} switched to {req.channel_id}",
    }


@app.post("/api/v1/clients/heartbeat", tags=["Clients"])
async def client_heartbeat(req: ClientHeartbeatRequest):
    """Called by UE clients to register, update telemetry, and fetch their desired state."""
    cl = update_client_heartbeat(
        client_id=req.client_id,
        ip=req.ip,
        net_delay_ms=req.net_delay_ms,
        rx_fps=req.rx_fps,
        rx_bitrate_mbps=req.rx_bitrate_mbps,
        dropped_frames=req.dropped_frames,
        total_frames=req.total_frames,
    )
    # Return current state and assigned channel to client
    ch = get_channel(cl.assigned_channel)
    return {
        "ok": True,
        "state": cl.state,
        "assigned_channel": cl.assigned_channel,
        "rtsp_url": ch.rtsp_path if ch else "",
        "hls_url": ch.hls_path if ch else "",
    }


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
