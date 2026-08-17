"""Global state manager for OTT channels and connected UE clients."""
from __future__ import annotations

import dataclasses
import threading
import time
from typing import Dict, List, Optional


@dataclasses.dataclass
class OttChannel:
    id: str
    name: str
    category: str
    source_type: str  # "youtube" or "file" or "synthetic"
    source_url: str
    youtube_id: Optional[str] = None
    description: str = ""
    is_active: bool = True
    fps: float = 25.0
    width: int = 1280
    height: int = 720
    bitrate_kbps: int = 4000
    hls_path: str = ""
    whep_path: str = ""
    rtsp_path: str = ""
    subscribers_count: int = 0
    frames_sent: int = 0
    last_frame_ts: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ConnectedClient:
    id: str
    name: str
    ip: str = "127.0.0.1"
    state: str = "STREAMING"  # "STREAMING" | "STOPPED" | "IDLE"
    assigned_channel: str = "channel_1"
    net_delay_ms: float = 0.0
    rx_fps: float = 0.0
    rx_bitrate_mbps: float = 0.0
    dropped_frames: int = 0
    total_frames_received: int = 0
    connected_at: float = dataclasses.field(default_factory=time.time)
    last_heartbeat: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["uptime_seconds"] = max(0.0, time.time() - self.connected_at)
        d["is_alive"] = (time.time() - self.last_heartbeat) < 15.0
        return d


GLOBAL_LOCK = threading.Lock()

# Initial Default OTT Channels (featuring YouTube open-source video references and local assets)
CHANNELS: Dict[str, OttChannel] = {
    "channel_1": OttChannel(
        id="channel_1",
        name="4K City Drone (YouTube)",
        category="Urban & 4K",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=1La4QzGeaaQ",
        youtube_id="1La4QzGeaaQ",
        description="High dynamic range urban cinematic footage",
        bitrate_kbps=4500,
        hls_path="/live/channel_1/index.m3u8",
        whep_path="/whep/channel_1",
        rtsp_path="rtsp://127.0.0.1:8555/channel_1",
    ),
    "channel_2": OttChannel(
        id="channel_2",
        name="Nature Wildlife (YouTube)",
        category="Documentary",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=LXb3EKWsInQ",
        youtube_id="LXb3EKWsInQ",
        description="Costa Rica 4K Wildlife & Tropical Nature",
        bitrate_kbps=4000,
        hls_path="/live/channel_2/index.m3u8",
        whep_path="/whep/channel_2",
        rtsp_path="rtsp://127.0.0.1:8555/channel_2",
    ),
    "channel_3": OttChannel(
        id="channel_3",
        name="Cyberpunk Tech (YouTube)",
        category="Technology",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=ysz5S6PUM-U",
        youtube_id="ysz5S6PUM-U",
        description="Fast-paced dynamic visual benchmark",
        bitrate_kbps=5000,
        hls_path="/live/channel_3/index.m3u8",
        whep_path="/whep/channel_3",
        rtsp_path="rtsp://127.0.0.1:8555/channel_3",
    ),
    "channel_4": OttChannel(
        id="channel_4",
        name="Big Buck Bunny (HD Benchmark)",
        category="Animation",
        source_type="file",
        source_url="/data/source.mp4",
        description="Standard 1080p/720p H.264 video benchmark",
        bitrate_kbps=3500,
        hls_path="/live/channel_4/index.m3u8",
        whep_path="/whep/channel_4",
        rtsp_path="rtsp://127.0.0.1:8555/channel_4",
    ),
}

# Connected UE Clients (pre-registered default slots for UE 1 to UE 4)
CONNECTED_CLIENTS: Dict[str, ConnectedClient] = {
    "ue1": ConnectedClient(
        id="ue1",
        name="5G UE 1 (USRPA)",
        ip="10.1.137.103",
        state="STREAMING",
        assigned_channel="channel_1",
    ),
    "ue2": ConnectedClient(
        id="ue2",
        name="5G UE 2 (USRPB)",
        ip="10.1.137.104",
        state="STREAMING",
        assigned_channel="channel_2",
    ),
    "ue3": ConnectedClient(
        id="ue3",
        name="5G UE 3 (Edge Sidecar)",
        ip="10.1.137.105",
        state="STOPPED",
        assigned_channel="channel_3",
    ),
    "ue4": ConnectedClient(
        id="ue4",
        name="5G UE 4 (Mobile Client)",
        ip="10.1.137.106",
        state="STOPPED",
        assigned_channel="channel_4",
    ),
}


def get_channel(channel_id: str) -> Optional[OttChannel]:
    with GLOBAL_LOCK:
        return CHANNELS.get(channel_id)


def list_channels() -> List[dict]:
    with GLOBAL_LOCK:
        return [c.to_dict() for c in CHANNELS.values()]


def get_client(client_id: str) -> Optional[ConnectedClient]:
    with GLOBAL_LOCK:
        return CONNECTED_CLIENTS.get(client_id)


def list_clients() -> List[dict]:
    with GLOBAL_LOCK:
        # Sort by ID numerically where possible
        return [cl.to_dict() for cl in CONNECTED_CLIENTS.values()]


def set_client_state(client_id: str, state: str) -> bool:
    with GLOBAL_LOCK:
        cl = CONNECTED_CLIENTS.get(client_id)
        if cl:
            cl.state = state
            cl.last_heartbeat = time.time()
            return True
        return False


def set_client_channel(client_id: str, channel_id: str) -> bool:
    with GLOBAL_LOCK:
        cl = CONNECTED_CLIENTS.get(client_id)
        if cl and channel_id in CHANNELS:
            cl.assigned_channel = channel_id
            cl.last_heartbeat = time.time()
            return True
        return False


def update_client_heartbeat(
    client_id: str,
    ip: Optional[str] = None,
    net_delay_ms: Optional[float] = None,
    rx_fps: Optional[float] = None,
    rx_bitrate_mbps: Optional[float] = None,
    dropped_frames: Optional[int] = None,
    total_frames: Optional[int] = None,
) -> ConnectedClient:
    with GLOBAL_LOCK:
        if client_id not in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS[client_id] = ConnectedClient(
                id=client_id,
                name=f"Client {client_id}",
                ip=ip or "127.0.0.1",
            )
        cl = CONNECTED_CLIENTS[client_id]
        if ip:
            cl.ip = ip
        if net_delay_ms is not None:
            cl.net_delay_ms = net_delay_ms
        if rx_fps is not None:
            cl.rx_fps = rx_fps
        if rx_bitrate_mbps is not None:
            cl.rx_bitrate_mbps = rx_bitrate_mbps
        if dropped_frames is not None:
            cl.dropped_frames = dropped_frames
        if total_frames is not None:
            cl.total_frames_received = total_frames
        cl.last_heartbeat = time.time()
        return cl
