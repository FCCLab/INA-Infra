#!/usr/bin/env python3
"""CCTV 5G UE Backend: RTSP video streamer over 5G PDU + live control & diagnostics."""
from __future__ import annotations

import collections
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from prometheus_client import Counter, Gauge, start_http_server
except ImportError:  # pragma: no cover
    Counter = Gauge = None  # type: ignore
    start_http_server = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": %(created)f, "level": "%(levelname)s", "ue": "%(name)s", "msg": "%(message)s"}',
)
logger = logging.getLogger("cctv.ue.backend")

SLICE_ID = int(os.environ.get("SLICE_ID", "1"))
CLIENT_INDEX = int(os.environ.get("CLIENT_INDEX", "1"))
CLIENT_ID = os.environ.get("CLIENT_ID", f"ue{CLIENT_INDEX}")
UE_NAME = os.environ.get("UE_NAME", f"oai-ue-slice-{SLICE_ID}-client-{CLIENT_INDEX}")
CONSOLE_IP = os.environ.get("CONSOLE_IP", f"10.1.137.{220 + CLIENT_INDEX - 1}")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", f"02:0a:40:{SLICE_ID:02x}:00:{CLIENT_INDEX:02x}")

TARGET_SERVER_IP = os.environ.get("TARGET_SERVER_IP") or os.environ.get("RTSP_TARGET_HOST") or "10.1.137.211"
RTSP_TARGET_HOST = TARGET_SERVER_IP
RTSP_PORT = int(os.environ.get("RTSP_PORT", "8554"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
SERVER_URL = (
    os.environ.get("SERVER_URL")
    or f"http://{TARGET_SERVER_IP}:{HTTP_PORT}"
).rstrip("/")

_raw_stream_path = (os.environ.get("STREAM_PATH") or "").strip()
if _raw_stream_path:
    STREAM_PATH = _raw_stream_path
else:
    STREAM_PATH = (
        f"cctv/ue{SLICE_ID}"
        if CLIENT_INDEX <= 1
        else f"cctv/ue{SLICE_ID}_cam{CLIENT_INDEX}"
    )

PDU_IFACE_CFG = os.environ.get("PDU_IFACE", f"oaitun_ue{SLICE_ID}")
PDU_ROUTE_HOSTS = os.environ.get(
    "PDU_ROUTE_HOSTS",
    ",".join(
        h for h in [
            "10.1.137.1",
            TARGET_SERVER_IP,
            RTSP_TARGET_HOST,
        ]
        if h
    ),
)
PDU_WAIT_TIMEOUT = int(os.environ.get("PDU_WAIT_TIMEOUT", "300"))

DEFAULT_VIDEO_SOURCE = os.environ.get("VIDEO_SOURCE", "/data/classroom.mp4")
DEFAULT_VIDEO_URL = os.environ.get(
    "VIDEO_URL",
    "https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4",
)
FPS_DEFAULT = int(os.environ.get("FPS", "25"))
BITRATE_DEFAULT = int(os.environ.get("BITRATE_KBPS", "4000"))
WIDTH_DEFAULT = int(os.environ.get("WIDTH", "1280"))
HEIGHT_DEFAULT = int(os.environ.get("HEIGHT", "720"))
RTSP_PROTOCOL_DEFAULT = os.environ.get("RTSP_PROTOCOL", "tcp").lower()

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8090"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", str(9100 + CLIENT_INDEX)))
UE_ID = os.environ.get("APP_NAME") or CLIENT_ID

LOG_LIMIT = int(os.environ.get("LOG_LIMIT", "100"))

# Runtime-resolved OAI tunnel name (often oaitun_ue1 even on slice 1).
_pdu_iface_live = PDU_IFACE_CFG
_pdu_lock = threading.Lock()

if Gauge is not None:
    APP_UE_LATENCY_MS = Gauge(
        "app_ue_latency_ms", "Per-UE application latency (milliseconds)", ["ue_id"]
    )
    APP_UE_RTT_MS = Gauge(
        "app_ue_rtt_ms", "Per-UE round-trip time (milliseconds)", ["ue_id"]
    )
    APP_UE_THROUGHPUT_MBPS = Gauge(
        "app_ue_throughput_mbps", "Per-UE application throughput (Mbps)", ["ue_id"]
    )
    APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
    APP_THROUGHPUT_MBPS = Gauge(
        "app_throughput_mbps", "Aggregated application throughput (Mbps)"
    )
    FRAMES_SENT = Counter(
        "cctv_ue_frames_sent_total",
        "Total encoded frames handed to the RTP payloader",
        ["ue_id"],
    )
    FPS_GAUGE = Gauge("cctv_ue_fps", "Frames per second (UE publisher)", ["ue_id"])
else:
    APP_UE_LATENCY_MS = APP_UE_RTT_MS = APP_UE_THROUGHPUT_MBPS = APP_LATENCY_MS = APP_THROUGHPUT_MBPS = None
    FRAMES_SENT = FPS_GAUGE = None

app = FastAPI(title=f"{UE_NAME} backend", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.RLock()
_logs: deque[dict[str, Any]] = deque(maxlen=LOG_LIMIT)

SAMPLE_VIDEOS = [
    {
        "id": "classroom",
        "name": "Classroom Camera",
        "path": "/data/classroom.mp4",
        "url": "https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4",
        "description": "Indoor classroom surveillance clip (persons, chairs, bags)",
    },
    {
        "id": "street",
        "name": "Street Traffic",
        "path": "/data/street.mp4",
        "url": "https://github.com/intel-iot-devkit/sample-videos/raw/master/person-bicycle-car-detection.mp4",
        "description": "Urban road intersection (vehicles, pedestrians, bicycles)",
    },
    {
        "id": "drone",
        "name": "Aerial Drone View",
        "path": "/data/drone.mp4",
        "url": "https://github.com/intel-iot-devkit/sample-videos/raw/master/face-demographics-walking.mp4",
        "description": "Elevated view monitoring outdoor movement",
    },
    {
        "id": "testpattern",
        "name": "SMPTE Test Pattern",
        "path": "videotestsrc",
        "url": "",
        "description": "Synthetic GStreamer color bars with timestamp overlay",
    },
]

_state: Dict[str, Any] = {
    "streaming_enabled": os.environ.get("STREAMING_ENABLED", "1") not in ("0", "false", "False"),
    "status": "stopped",
    "video_source": DEFAULT_VIDEO_SOURCE,
    "video_url": DEFAULT_VIDEO_URL,
    "video_name": "Classroom Camera",
    "rtsp_target_host": RTSP_TARGET_HOST,
    "rtsp_port": RTSP_PORT,
    "stream_path": STREAM_PATH,
    "rtsp_protocol": RTSP_PROTOCOL_DEFAULT,
    "rtsp_url": f"rtsp://{RTSP_TARGET_HOST}:{RTSP_PORT}/{STREAM_PATH}",
    "fps": FPS_DEFAULT,
    "bitrate_kbps": BITRATE_DEFAULT,
    "width": WIDTH_DEFAULT,
    "height": HEIGHT_DEFAULT,
    "pdu_ready": False,
    "pdu_iface": "",
    "server_reachable": False,
    "rtsp_reachable": False,
    "http_reachable": False,
    "rtt_ms": 0.0,
    "frames_sent": 0,
    "current_fps": 0.0,
    "throughput_mbps": 0.0,
    "bytes_sent": 0,
    "uptime_seconds": 0,
    "started_at": None,
    "last_error": None,
}


def _log_event(level: str, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    event = {
        "ts": time.time(),
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
        "level": level,
        "msg": msg,
        **(extra or {}),
    }
    with _lock:
        _logs.append(event)
    getattr(logger, level if hasattr(logger, level) else "info")(msg)


class StreamConfigIn(BaseModel):
    video_source: Optional[str] = None
    video_url: Optional[str] = None
    video_name: Optional[str] = None
    rtsp_target_host: Optional[str] = None
    rtsp_port: Optional[int] = None
    stream_path: Optional[str] = None
    rtsp_protocol: Optional[str] = None
    fps: Optional[int] = None
    bitrate_kbps: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class TestConnectionOut(BaseModel):
    ok: bool
    rtt_ms: float
    ping_ok: bool
    rtsp_ok: bool
    http_ok: bool
    server_url: str
    rtsp_url: str
    pdu_iface: str
    detail: str


# ---------------------------------------------------------------------------
# PDU Interface & Routing Manager
# ---------------------------------------------------------------------------

def _resolve_pdu_iface() -> Optional[str]:
    global _pdu_iface_live
    with _pdu_lock:
        for cand in [_pdu_iface_live, PDU_IFACE_CFG, "oaitun_ue1", "oaitun_ue2", "oaitun_ue3", "oaitun_ue4"]:
            if cand and Path(f"/sys/class/net/{cand}").is_dir():
                _pdu_iface_live = cand
                return cand
        try:
            out = subprocess.check_output(["ip", "-br", "link"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if line.startswith("oaitun"):
                    cand = line.split()[0]
                    _pdu_iface_live = cand
                    return cand
        except Exception:
            pass
    return None


def _setup_pdu_routes() -> bool:
    iface = _resolve_pdu_iface()
    if not iface:
        return False
    ok = True
    hosts = [h.strip() for h in PDU_ROUTE_HOSTS.split(",") if h.strip()]
    for host in hosts:
        try:
            res = subprocess.run(
                ["ip", "route", "replace", f"{host}/32", "dev", iface],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0:
                ok = False
        except Exception:
            ok = False
    return ok


def _pdu_watchdog_loop() -> None:
    while True:
        try:
            iface = _resolve_pdu_iface()
            with _lock:
                _state["pdu_iface"] = iface or ""
                _state["pdu_ready"] = bool(iface)
            if iface:
                _setup_pdu_routes()
        except Exception as exc:
            logger.debug(f"PDU watchdog error: {exc}")
        time.sleep(5)


# ---------------------------------------------------------------------------
# Connectivity Diagnostics Loop
# ---------------------------------------------------------------------------

def _test_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ping_host(host: str, timeout: int = 2) -> Optional[float]:
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
        if res.returncode == 0:
            m = re.search(r"time=([0-9.]+)", res.stdout)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return None


def _connectivity_loop() -> None:
    while True:
        try:
            with _lock:
                target_host = _state["rtsp_target_host"]
                target_rtsp_port = _state["rtsp_port"]
                server_url = SERVER_URL

            rtt = _ping_host(target_host) or _ping_host("10.1.137.1") or 0.0
            rtsp_ok = _test_tcp_port(target_host, target_rtsp_port)
            
            http_ok = False
            try:
                req = urllib.request.Request(f"{server_url}/health", headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    http_ok = resp.status == 200
            except Exception:
                http_ok = False

            with _lock:
                _state["rtt_ms"] = round(rtt, 2)
                _state["rtsp_reachable"] = rtsp_ok
                _state["http_reachable"] = http_ok
                _state["server_reachable"] = rtsp_ok or http_ok

            if APP_UE_RTT_MS and UE_ID:
                APP_UE_RTT_MS.labels(ue_id=UE_ID).set(rtt)
        except Exception as exc:
            logger.debug(f"Connectivity check error: {exc}")
        time.sleep(3)


# ---------------------------------------------------------------------------
# GStreamer Streaming Process Manager
# ---------------------------------------------------------------------------

class StreamManager:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.stop_requested = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.frames_sent = 0
        self.bytes_sent = 0
        self.start_time: Optional[float] = None
        self.last_frame_time: Optional[float] = None

    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.stop_requested.clear()
            self.running = True
            self.start_time = time.time()
            self.thread = threading.Thread(target=self._stream_worker, daemon=True)
            self.thread.start()
            _log_event("info", f"Started streaming worker targeting {_state['rtsp_url']}")

    def stop(self) -> None:
        with self.lock:
            self.stop_requested.set()
            self.running = False
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None
            with _lock:
                _state["status"] = "stopped"
                _state["current_fps"] = 0.0
                _state["throughput_mbps"] = 0.0
            _log_event("info", "Stopped streaming worker")

    def restart(self) -> None:
        self.stop()
        time.sleep(0.5)
        self.start()

    def _ensure_video_file(self, src: str, url: str) -> str:
        if src == "videotestsrc" or not src.endswith((".mp4", ".mkv", ".avi", ".h264")):
            return src
        p = Path(src)
        if p.is_file() and p.stat().st_size > 1000:
            return src
        if not url:
            return "videotestsrc"
        p.parent.mkdir(parents=True, exist_ok=True)
        _log_event("info", f"Downloading sample video from {url} to {src}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(src, "wb") as f:
                shutil.copyfileobj(resp, f)
            _log_event("info", f"Sample video downloaded ({p.stat().st_size // 1024} KB)")
            return src
        except Exception as exc:
            _log_event("warn", f"Failed downloading sample video: {exc}. Falling back to videotestsrc")
            return "videotestsrc"

    def _build_gst_cmd(self) -> List[str]:
        with _lock:
            src = _state["video_source"]
            url = _state["video_url"]
            fps = _state["fps"]
            bitrate = _state["bitrate_kbps"]
            width = _state["width"]
            height = _state["height"]
            rtsp_target = _state["rtsp_url"]
            protocol = _state["rtsp_protocol"]

        resolved_src = self._ensure_video_file(src, url)

        # Build GStreamer command line with fallback
        if resolved_src != "videotestsrc" and Path(resolved_src).is_file():
            # Local video file looped stream
            pipeline = [
                "gst-launch-1.0", "-e",
                "filesrc", f"location={resolved_src}",
                "!", "decodebin",
                "!", "videoconvert",
                "!", "videorate",
                "!", f"video/x-raw,framerate={fps}/1",
                "!", "videoscale",
                "!", f"video/x-raw,width={width},height={height}",
                "!", "x264enc", f"bitrate={bitrate}", "speed-preset=ultrafast", "tune=zerolatency", f"key-int-max={fps}",
                "!", "h264parse",
                "!", "rtspclientsink", f"location={rtsp_target}", f"protocols={protocol}",
            ]
        else:
            # Synthetic videotestsrc pattern
            pipeline = [
                "gst-launch-1.0", "-e",
                "videotestsrc", "pattern=smpte", "is-live=true",
                "!", "videoconvert",
                "!", "videorate",
                "!", f"video/x-raw,framerate={fps}/1",
                "!", "videoscale",
                "!", f"video/x-raw,width={width},height={height}",
                "!", "x264enc", f"bitrate={bitrate}", "speed-preset=ultrafast", "tune=zerolatency", f"key-int-max={fps}",
                "!", "h264parse",
                "!", "rtspclientsink", f"location={rtsp_target}", f"protocols={protocol}",
            ]
        return pipeline

    def _stream_worker(self) -> None:
        consecutive_failures = 0
        while not self.stop_requested.is_set():
            cmd = self._build_gst_cmd()
            cmd_str = " ".join(cmd)
            with _lock:
                _state["status"] = "connecting"
            _log_event("info", f"Launching GStreamer pipeline: {cmd_str}")

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                with _lock:
                    _state["status"] = "streaming"
                    _state["started_at"] = time.time()
                    _state["last_error"] = None

                frame_count = 0
                t_prev = time.time()

                while not self.stop_requested.is_set():
                    ret = self.process.poll()
                    if ret is not None:
                        # Process exited
                        stderr_out = self.process.stderr.read() if self.process.stderr else ""
                        msg = f"GStreamer exited with code {ret}: {stderr_out[:200]}"
                        _log_event("warn" if self.stop_requested.is_set() else "error", msg)
                        with _lock:
                            _state["last_error"] = msg
                        break

                    # Approximate metric advancement
                    time.sleep(1.0)
                    now = time.time()
                    dt = now - t_prev
                    t_prev = now
                    fps_target = _state.get("fps", 25)
                    bitrate = _state.get("bitrate_kbps", 4000)

                    frame_inc = int(fps_target * dt)
                    self.frames_sent += frame_inc
                    self.bytes_sent += int((bitrate * 1000 / 8) * dt)

                    mbps = round((bitrate * 1000) / 1_000_000.0, 2)

                    with _lock:
                        _state["frames_sent"] = self.frames_sent
                        _state["current_fps"] = float(fps_target)
                        _state["throughput_mbps"] = mbps
                        _state["bytes_sent"] = self.bytes_sent
                        _state["uptime_seconds"] = int(now - (_state["started_at"] or now))

                    if FRAMES_SENT and UE_ID:
                        FRAMES_SENT.labels(ue_id=UE_ID).inc(frame_inc)
                    if FPS_GAUGE and UE_ID:
                        FPS_GAUGE.labels(ue_id=UE_ID).set(float(fps_target))
                    if APP_UE_THROUGHPUT_MBPS and UE_ID:
                        APP_UE_THROUGHPUT_MBPS.labels(ue_id=UE_ID).set(mbps)
                    if APP_THROUGHPUT_MBPS:
                        APP_THROUGHPUT_MBPS.set(mbps)

                consecutive_failures = 0

            except FileNotFoundError:
                msg = "gst-launch-1.0 not installed on system — simulation mode active"
                _log_event("warn", msg)
                with _lock:
                    _state["status"] = "streaming (sim)"
                    _state["last_error"] = None
                # Run simulated ticker when gst is absent
                while not self.stop_requested.is_set():
                    time.sleep(1.0)
                    fps_target = _state.get("fps", 25)
                    bitrate = _state.get("bitrate_kbps", 4000)
                    mbps = round(bitrate / 1000.0, 2)
                    self.frames_sent += fps_target
                    self.bytes_sent += int((bitrate * 1000 / 8))
                    with _lock:
                        _state["frames_sent"] = self.frames_sent
                        _state["current_fps"] = float(fps_target)
                        _state["throughput_mbps"] = mbps
                        _state["bytes_sent"] = self.bytes_sent
                        _state["uptime_seconds"] = int(time.time() - (_state["started_at"] or time.time()))

            except Exception as exc:
                consecutive_failures += 1
                msg = f"Stream execution failed: {exc}"
                _log_event("error", msg)
                with _lock:
                    _state["status"] = "error"
                    _state["last_error"] = msg
                time.sleep(min(30, 2 ** consecutive_failures))

            if not self.stop_requested.is_set():
                _log_event("info", "Video stream ended; looping in 2 seconds...")
                time.sleep(2.0)


STREAMER = StreamManager()


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    if start_http_server and METRICS_PORT:
        try:
            start_http_server(METRICS_PORT)
            logger.info(f"Prometheus metrics listening on :{METRICS_PORT}")
        except Exception as exc:
            logger.warning(f"Metrics server failed: {exc}")

    # Launch background workers
    threading.Thread(target=_pdu_watchdog_loop, daemon=True).start()
    threading.Thread(target=_connectivity_loop, daemon=True).start()

    if _state["streaming_enabled"]:
        STREAMER.start()


@app.get("/healthz")
def healthz() -> dict:
    with _lock:
        return {
            "ok": True,
            "ue": UE_NAME,
            "client_id": CLIENT_ID,
            "console_ip": CONSOLE_IP,
            "console_mac": CONSOLE_MAC,
            "status": _state["status"],
            "pdu_ready": _state["pdu_ready"],
            "pdu_iface": _state["pdu_iface"],
            "rtsp_url": _state["rtsp_url"],
        }


@app.get("/api/status")
def get_status() -> dict:
    with _lock:
        return {
            "ok": True,
            "ue_name": UE_NAME,
            "client_id": CLIENT_ID,
            "slice_id": SLICE_ID,
            "client_index": CLIENT_INDEX,
            "console_ip": CONSOLE_IP,
            "console_mac": CONSOLE_MAC,
            **_state,
        }


@app.post("/api/stream/start")
def start_stream() -> dict:
    with _lock:
        _state["streaming_enabled"] = True
    STREAMER.start()
    return {"ok": True, "status": "started", "rtsp_url": _state["rtsp_url"]}


@app.post("/api/stream/stop")
def stop_stream() -> dict:
    with _lock:
        _state["streaming_enabled"] = False
    STREAMER.stop()
    return {"ok": True, "status": "stopped"}


@app.post("/api/stream/restart")
def restart_stream() -> dict:
    STREAMER.restart()
    return {"ok": True, "status": "restarted", "rtsp_url": _state["rtsp_url"]}


@app.post("/api/stream/config")
def update_config(cfg: StreamConfigIn) -> dict:
    with _lock:
        if cfg.video_source is not None:
            _state["video_source"] = cfg.video_source
        if cfg.video_url is not None:
            _state["video_url"] = cfg.video_url
        if cfg.video_name is not None:
            _state["video_name"] = cfg.video_name
        if cfg.rtsp_target_host is not None:
            _state["rtsp_target_host"] = cfg.rtsp_target_host
        if cfg.rtsp_port is not None:
            _state["rtsp_port"] = cfg.rtsp_port
        if cfg.stream_path is not None:
            _state["stream_path"] = cfg.stream_path
        if cfg.rtsp_protocol is not None:
            _state["rtsp_protocol"] = cfg.rtsp_protocol.lower()
        if cfg.fps is not None:
            _state["fps"] = max(1, min(cfg.fps, 120))
        if cfg.bitrate_kbps is not None:
            _state["bitrate_kbps"] = max(200, min(cfg.bitrate_kbps, 50000))
        if cfg.width is not None:
            _state["width"] = cfg.width
        if cfg.height is not None:
            _state["height"] = cfg.height

        _state["rtsp_url"] = f"rtsp://{_state['rtsp_target_host']}:{_state['rtsp_port']}/{_state['stream_path']}"

    _log_event("info", f"Updated stream configuration: {_state['rtsp_url']} ({_state['fps']} fps, {_state['bitrate_kbps']} kbps)")
    if STREAMER.running:
        STREAMER.restart()

    return {"ok": True, "config": _state}


@app.get("/api/videos")
def get_videos() -> List[dict]:
    return SAMPLE_VIDEOS


@app.post("/api/test-connection")
def test_connection() -> TestConnectionOut:
    with _lock:
        target_host = _state["rtsp_target_host"]
        rtsp_port = _state["rtsp_port"]
        server_url = SERVER_URL
        pdu_iface = _state["pdu_iface"]

    ping_rtt = _ping_host(target_host)
    rtsp_ok = _test_tcp_port(target_host, rtsp_port)

    http_ok = False
    detail_parts = []
    try:
        req = urllib.request.Request(f"{server_url}/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            http_ok = resp.status == 200
            detail_parts.append(f"HTTP Analyzer API responded 200 OK")
    except Exception as exc:
        detail_parts.append(f"HTTP Analyzer API probe: {exc}")

    if rtsp_ok:
        detail_parts.append(f"RTSP port {rtsp_port} open and accepting connections")
    else:
        detail_parts.append(f"RTSP port {rtsp_port} connection refused/unreachable")

    if ping_rtt is not None:
        detail_parts.append(f"Ping RTT {ping_rtt:.1f}ms")
    else:
        detail_parts.append("Ping timed out")

    ok = rtsp_ok or http_ok

    _log_event(
        "info" if ok else "warn",
        f"Connection test to {target_host}: ok={ok} (rtsp={rtsp_ok}, http={http_ok}, ping={ping_rtt}ms)",
    )

    return TestConnectionOut(
        ok=ok,
        rtt_ms=ping_rtt or 0.0,
        ping_ok=ping_rtt is not None,
        rtsp_ok=rtsp_ok,
        http_ok=http_ok,
        server_url=server_url,
        rtsp_url=f"rtsp://{target_host}:{rtsp_port}/{_state['stream_path']}",
        pdu_iface=pdu_iface or "none",
        detail=" | ".join(detail_parts),
    )


@app.get("/api/server-info")
def get_server_info() -> dict:
    server_url = SERVER_URL
    try:
        req = urllib.request.Request(f"{server_url}/clients", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            return {"ok": True, "clients": data, "server_url": server_url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "server_url": server_url}


@app.get("/api/logs")
def get_logs(limit: int = 50) -> List[dict]:
    with _lock:
        return list(_logs)[-limit:]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=BACKEND_PORT)
