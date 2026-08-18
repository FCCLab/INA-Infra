"""Global state manager for OTT videos/channels and connected UE clients."""
from __future__ import annotations

import dataclasses
import os
import re
import threading
import time
from typing import Dict, List, Optional


def _http_url(host: str, port: int = 443) -> str:
    """UE Multus consoles serve HTTPS on :443 (HTTP :80 redirects)."""
    host = (host or "").strip()
    if not host:
        return ""
    if port in (443, 0, 80):
        return f"https://{host}/"
    return f"https://{host}:{port}/"


def public_http_base() -> str:
    explicit = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if explicit:
        return explicit
    multus = os.environ.get("MULTUS_IP") or "10.1.137.213"
    return f"http://{multus}"


def public_rtsp_base() -> str:
    explicit = (os.environ.get("MTX_RTSP_PUBLIC") or "").rstrip("/")
    if explicit:
        return explicit
    multus = os.environ.get("MULTUS_IP") or "10.1.137.213"
    port = int(os.environ.get("MTX_RTSP_PUBLIC_PORT") or "8555")
    return f"rtsp://{multus}:{port}"


# youtube = UE/browser plays YouTube directly (default). mediamtx = legacy republish.
PLAY_MODE = (os.environ.get("OTT_PLAY_MODE") or "youtube").strip().lower()


def extract_youtube_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    raw = url_or_id.strip()
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|v=)([\w-]{11})",
        raw,
    )
    return m.group(1) if m else None


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

    def resolved_youtube_id(self) -> Optional[str]:
        return self.youtube_id or extract_youtube_id(self.source_url)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["youtube_id"] = self.resolved_youtube_id()
        d["play_mode"] = PLAY_MODE
        return d

    def play_urls(self) -> dict:
        """Play links for UEs — YouTube-direct by default (no MediaMTX)."""
        yt = self.resolved_youtube_id()
        watch = f"https://www.youtube.com/watch?v={yt}" if yt else (self.source_url or "")
        embed = (
            f"https://www.youtube.com/embed/{yt}?autoplay=1&mute=1&rel=0&modestbranding=1"
            if yt
            else ""
        )
        out = {
            "video_id": self.id,
            "title": self.name,
            "play_mode": PLAY_MODE,
            "youtube_id": yt or "",
            "youtube_url": watch,
            "embed_url": embed,
            "hls_url": "",
            "whep_url": "",
            "rtsp_url": "",
            "hls_path": "",
            "whep_path": "",
            "rtsp_path": "",
        }
        if PLAY_MODE == "mediamtx":
            base = public_http_base()
            rtsp = public_rtsp_base()
            hls = self.hls_path or f"/live/{self.id}/index.m3u8"
            whep = self.whep_path or f"/whep/{self.id}"
            out.update(
                {
                    "hls_url": f"{base}{hls}" if hls.startswith("/") else hls,
                    "whep_url": f"{base}{whep}" if whep.startswith("/") else whep,
                    "rtsp_url": f"{rtsp}/{self.id}",
                    "hls_path": hls,
                    "whep_path": whep,
                    "rtsp_path": self.rtsp_path or f"rtsp://127.0.0.1:8555/{self.id}",
                }
            )
        return out


@dataclasses.dataclass
class ConnectedClient:
    id: str
    name: str
    ip: str = "127.0.0.1"
    console_ip: str = ""
    console_port: int = 443
    console_mac: str = ""
    console_url: str = ""
    pdu_iface: str = "oaitun_ue3"
    pdu_ip: str = ""
    state: str = "IDLE"  # "STREAMING" | "STOPPED" | "IDLE"
    assigned_channel: str = ""
    selected_video_id: str = ""
    play_hls_url: str = ""
    play_rtsp_url: str = ""
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
        if not d.get("console_url") and d.get("console_ip"):
            d["console_url"] = _http_url(d["console_ip"], self.console_port)
        return d


GLOBAL_LOCK = threading.Lock()

# Video catalog published to UEs (also used as MediaMTX path names).
# Examples from: https://www.youtube.com/results?search_query=4k+nature+24+hours
CHANNELS: Dict[str, OttChannel] = {
    "channel_1": OttChannel(
        id="channel_1",
        name="24h Underwater 4K (YouTube)",
        category="Nature · 4K · 24h",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=yc31e3h8Ehk",
        youtube_id="yc31e3h8Ehk",
        description="24 HOURS of 4K Underwater Wonders + Relaxing Music",
        bitrate_kbps=6000,
        hls_path="/live/channel_1/index.m3u8",
        whep_path="/whep/channel_1",
        rtsp_path="rtsp://127.0.0.1:8555/channel_1",
    ),
    "channel_2": OttChannel(
        id="channel_2",
        name="Norway Nature 4K 24/7 (YouTube)",
        category="Nature · 4K · 24h",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=JTEQQrtXrYM",
        youtube_id="JTEQQrtXrYM",
        description="4K Video 24/7 — Norway nature with relaxing music",
        bitrate_kbps=6000,
        hls_path="/live/channel_2/index.m3u8",
        whep_path="/whep/channel_2",
        rtsp_path="rtsp://127.0.0.1:8555/channel_2",
    ),
    "channel_3": OttChannel(
        id="channel_3",
        name="Splendors of Nature 12h 4K (YouTube)",
        category="Nature · 4K · Long-form",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=3xPkwNu2o8g",
        youtube_id="3xPkwNu2o8g",
        description="12 HOUR 4K film — Planet Earth's wonders by drone, land & sea",
        bitrate_kbps=6000,
        hls_path="/live/channel_3/index.m3u8",
        whep_path="/whep/channel_3",
        rtsp_path="rtsp://127.0.0.1:8555/channel_3",
    ),
    "channel_4": OttChannel(
        id="channel_4",
        name="Nature Europe 4K (YouTube)",
        category="Nature · 4K",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=SYyvx6GE2UI",
        youtube_id="SYyvx6GE2UI",
        description="Nature Europe 4K · Calm horizon with deep relaxing music",
        bitrate_kbps=5000,
        hls_path="/live/channel_4/index.m3u8",
        whep_path="/whep/channel_4",
        rtsp_path="rtsp://127.0.0.1:8555/channel_4",
    ),
}

# Populated by UE heartbeats (no static fake clients).
CONNECTED_CLIENTS: Dict[str, ConnectedClient] = {}


def get_channel(channel_id: str) -> Optional[OttChannel]:
    with GLOBAL_LOCK:
        return CHANNELS.get(channel_id)


def list_channels() -> List[dict]:
    with GLOBAL_LOCK:
        return [c.to_dict() for c in CHANNELS.values()]


def list_videos() -> List[dict]:
    """UE-facing catalog: channel metadata + YouTube-direct play URLs."""
    with GLOBAL_LOCK:
        out = []
        for ch in CHANNELS.values():
            if not ch.is_active:
                continue
            item = ch.to_dict()
            item.update(ch.play_urls())
            out.append(item)
        return out


def get_client(client_id: str) -> Optional[ConnectedClient]:
    with GLOBAL_LOCK:
        return CONNECTED_CLIENTS.get(client_id)


def list_clients(*, alive_only: bool = False) -> List[dict]:
    with GLOBAL_LOCK:
        rows = [cl.to_dict() for cl in CONNECTED_CLIENTS.values()]
    if alive_only:
        rows = [r for r in rows if r.get("is_alive")]
    rows.sort(key=lambda r: r.get("id") or "")
    return rows


def set_client_state(client_id: str, state: str) -> bool:
    with GLOBAL_LOCK:
        cl = CONNECTED_CLIENTS.get(client_id)
        if cl:
            cl.state = state
            cl.last_heartbeat = time.time()
            if state != "STREAMING":
                cl.play_hls_url = ""
                cl.play_rtsp_url = ""
            return True
        return False


def set_client_channel(client_id: str, channel_id: str) -> bool:
    with GLOBAL_LOCK:
        cl = CONNECTED_CLIENTS.get(client_id)
        ch = CHANNELS.get(channel_id)
        if not cl or not ch:
            return False
        urls = ch.play_urls()
        cl.assigned_channel = channel_id
        cl.selected_video_id = channel_id
        cl.play_hls_url = urls.get("youtube_url") or urls.get("hls_url") or ""
        cl.play_rtsp_url = urls.get("rtsp_url") or ""
        cl.state = "STREAMING"
        cl.last_heartbeat = time.time()
        return True


def resolve_channel_for_client(video_or_channel_id: str, client_id: str = "") -> Optional[OttChannel]:
    with GLOBAL_LOCK:
        ch = CHANNELS.get(video_or_channel_id)
        if ch and ch.is_active:
            return ch
        # If requested video_id (e.g. channel_5) does not exist, rotate modulo active channels
        active = [c for c in CHANNELS.values() if c.is_active]
        if not active:
            return None
        m = re.search(r"(\d+)", video_or_channel_id) or (re.search(r"(\d+)", client_id) if client_id else None)
        if m:
            num = int(m.group(1))
            idx = (num - 1) % len(active)
            return active[idx]
        return active[0]


def select_video_for_client(client_id: str, video_id: str) -> Optional[dict]:
    """UE selects a video → return YouTube-direct play info (default)."""
    with GLOBAL_LOCK:
        ch = resolve_channel_for_client(video_id, client_id)
        if not ch or not ch.is_active:
            return None
        if client_id not in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS[client_id] = ConnectedClient(
                id=client_id,
                name=f"UE {client_id}",
            )
        cl = CONNECTED_CLIENTS[client_id]
        urls = ch.play_urls()
        cl.assigned_channel = ch.id
        cl.selected_video_id = ch.id
        cl.play_hls_url = urls.get("youtube_url") or urls.get("hls_url") or ""
        cl.play_rtsp_url = urls.get("rtsp_url") or ""
        cl.state = "STREAMING"
        cl.last_heartbeat = time.time()
        return {
            "ok": True,
            "client_id": client_id,
            "state": cl.state,
            "video": ch.to_dict(),
            **urls,
        }



def update_client_heartbeat(
    client_id: str,
    ip: Optional[str] = None,
    net_delay_ms: Optional[float] = None,
    rx_fps: Optional[float] = None,
    rx_bitrate_mbps: Optional[float] = None,
    dropped_frames: Optional[int] = None,
    total_frames: Optional[int] = None,
    pdu_ip: Optional[str] = None,
    console_ip: Optional[str] = None,
    console_mac: Optional[str] = None,
    name: Optional[str] = None,
    state: Optional[str] = None,
) -> ConnectedClient:
    with GLOBAL_LOCK:
        if client_id not in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS[client_id] = ConnectedClient(
                id=client_id,
                name=name or f"UE {client_id}",
                ip=ip or "127.0.0.1",
            )
        cl = CONNECTED_CLIENTS[client_id]
        if name:
            cl.name = name
        if ip:
            cl.ip = ip
        if state:
            cl.state = state
        if console_ip:
            cl.console_ip = console_ip
            cl.console_url = _http_url(console_ip, cl.console_port)
        if console_mac:
            cl.console_mac = console_mac
        if pdu_ip:
            cl.pdu_ip = pdu_ip
        if net_delay_ms is not None:
            cl.net_delay_ms = net_delay_ms
        if rx_fps is not None:
            cl.rx_fps = rx_fps
        if rx_bitrate_mbps is not None:
            cl.rx_bitrate_mbps = rx_bitrate_mbps

        if not cl.assigned_channel:
            active_channels = [c for c in CHANNELS.values() if c.is_active]
            if active_channels:
                # Extract client number (e.g. ue1 -> 1, slice3-ott-client-5 -> 5)
                m = re.search(r"(\d+)", client_id)
                num = int(m.group(1)) if m else (len(CONNECTED_CLIENTS))
                # Rotate across available channels if there are more UEs than videos
                idx = (num - 1) % len(active_channels)
                picked = active_channels[idx]
                cl.assigned_channel = picked.id
                cl.selected_video_id = picked.id
                urls = picked.play_urls()
                cl.play_hls_url = urls.get("youtube_url") or urls.get("hls_url") or ""
                cl.play_rtsp_url = urls.get("rtsp_url") or ""
        cl.last_heartbeat = time.time()
        return cl

