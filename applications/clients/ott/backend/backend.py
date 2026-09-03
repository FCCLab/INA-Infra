#!/usr/bin/env python3
"""OTT 5G UE Backend: catalog/heartbeat with app server; Chromium play via PDU SOCKS."""
from __future__ import annotations

import collections
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

try:
    from backend.yt_proxy import extract_youtube_id
    from backend.pdu_socks import PDU_SOCKS
    from backend import chrome_ctl
except ImportError:
    try:
        from ue.yt_proxy import extract_youtube_id
        from ue.pdu_socks import PDU_SOCKS
        from ue import chrome_ctl
    except ImportError:
        from yt_proxy import extract_youtube_id  # type: ignore
        from pdu_socks import PDU_SOCKS  # type: ignore
        import chrome_ctl  # type: ignore

try:
    from prometheus_client import Gauge, start_http_server
except ImportError:  # pragma: no cover
    Gauge = None  # type: ignore
    start_http_server = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": %(created)f, "level": "%(levelname)s", "ue": "%(name)s", "msg": "%(message)s"}',
)
logger = logging.getLogger("ott.ue.backend")

SLICE_ID = int(os.environ.get("SLICE_ID", "3"))
CLIENT_INDEX = int(os.environ.get("CLIENT_INDEX", "1"))
CLIENT_ID = os.environ.get("CLIENT_ID", f"ue{CLIENT_INDEX}")
UE_NAME = os.environ.get("UE_NAME", f"oai-ue-slice-{SLICE_ID}-client-{CLIENT_INDEX}")
CONSOLE_IP = os.environ.get("CONSOLE_IP", f"10.1.137.{200 + CLIENT_INDEX - 1}")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", f"02:0a:40:{SLICE_ID:02x}:00:{CLIENT_INDEX:02x}")
# Multus OTT console/API is on :80 (nginx). Prefer SERVER_URL; fall back to Multus host.
SERVER_URL = (
    os.environ.get("SERVER_URL")
    or f"http://{os.environ.get('TARGET_SERVER_IP', '10.1.137.213')}"
).rstrip("/")
SERVER_RTSP_HOST = os.environ.get("TARGET_SERVER_IP", "10.1.137.213")
SERVER_RTSP_PORT = int(os.environ.get("SERVER_RTSP_PORT", "8555"))
# ---------------------------------------------------------------------------
# Network Interfaces: To-Server (default: RAN interface) & API (default: eth0)
# ---------------------------------------------------------------------------
TO_SERVER_IFACE_CFG = (
    os.environ.get("TO_SERVER_IFACE")
    or os.environ.get("SERVER_IFACE")
    or os.environ.get("RAN_IFACE")
    or os.environ.get("PDU_IFACE")
    or f"oaitun_ue{SLICE_ID}"
)
API_IFACE_CFG = os.environ.get("API_IFACE") or "eth0"
PDU_IFACE_CFG = TO_SERVER_IFACE_CFG
PDU_ROUTE_HOSTS = os.environ.get(
    "PDU_ROUTE_HOSTS",
    ",".join(
        h for h in [
            "10.1.137.1",
            SERVER_RTSP_HOST,
            os.environ.get("TARGET_SERVER_IP", ""),
        ]
        if h
    ),
)

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8090"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9111"))
UE_ID = os.environ.get("APP_NAME") or CLIENT_ID
# Runtime-resolved OAI tunnel name (often oaitun_ue1 even on slice 3).
_pdu_iface_live = PDU_IFACE_CFG
_pdu_lock = threading.Lock()
_api_iface_live = API_IFACE_CFG

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
    APP_UE_THROUGHPUT_DL_MBPS = Gauge(
        "app_ue_throughput_dl_mbps", "Per-UE downlink throughput (Mbps)", ["ue_id"]
    )
    APP_UE_THROUGHPUT_UL_MBPS = Gauge(
        "app_ue_throughput_ul_mbps", "Per-UE uplink throughput (Mbps)", ["ue_id"]
    )
    APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
    APP_THROUGHPUT_MBPS = Gauge(
        "app_throughput_mbps", "Aggregated application throughput (Mbps)"
    )
else:
    APP_UE_LATENCY_MS = APP_UE_RTT_MS = APP_UE_THROUGHPUT_MBPS = APP_UE_THROUGHPUT_DL_MBPS = APP_UE_THROUGHPUT_UL_MBPS = APP_LATENCY_MS = APP_THROUGHPUT_MBPS = None


app = FastAPI(title=f"{UE_NAME} backend", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RLock: _log_event also takes this lock; a nested acquire must not deadlock.
_lock = threading.RLock()
# Serialise Chromium CDP so heartbeat never shares a WS with the watchdog.
_cdp_lock = threading.RLock()
_state: Dict[str, Any] = {
    "streaming_enabled": False,
    "assigned_channel": "",
    "selected_video_id": "",
    "play_mode": os.environ.get("OTT_PLAY_MODE", "youtube"),
    "youtube_id": "",
    "youtube_url": "",
    "embed_url": "",
    "hls_url": "",
    "rtsp_url": "",
    "whep_url": "",
    "pdu_ready": False,
    "pdu_ip": "",
    "pdu_iface": "",
    "frames_received": 0,
    "bytes_received": 0,
    "rx_fps": 0.0,
    "rx_bitrate_mbps": 0.0,
    "last_delay_ms": 0.0,

    "probe_rtt_ms": 0.0,
    "probe_ok": 0,
    "probe_fail": 0,
    "dropped_frames": 0,
    "last_frame_time": 0.0,
    "yt_quality": "",
    "play_quality": os.environ.get("OTT_PLAY_QUALITY", "4k"),
    "video_height": 0,
    "chrome_url": "",
    "chrome_ui": os.environ.get("CHROME_HTTP_URL", f"https://{CONSOLE_IP}"),
    "socks_bytes_up": 0,
    "socks_bytes_down": 0,
    "server_ok": False,
    "server_last_ok": 0.0,
    "server_error": "",
    "periodic_check_enabled": os.environ.get("OTT_PERIODIC_CHECK_ENABLED", "1") not in ("0", "false", "False"),
    "watchdog_last_check": 0.0,
    "watchdog_last_action": "initialized",
    "ads_skipped_count": 0,
    "resumed_count": 0,
    "watchdog_reload_count": 0,
}

_delays: collections.deque = collections.deque(maxlen=30)
_recent_events: collections.deque[Dict[str, Any]] = collections.deque(maxlen=50)
_video_cache: List[dict] = []


class PlayIn(BaseModel):
    video_id: str = Field(..., min_length=1)
    quality: str = Field(default_factory=lambda: os.environ.get("OTT_PLAY_QUALITY", "4k"))


class WatchdogToggleIn(BaseModel):
    enabled: Optional[bool] = None



DEFAULT_PLAY_QUALITY = os.environ.get("OTT_PLAY_QUALITY", "4k")
LATENCY_PROBE_PERIOD_S = float(os.environ.get("LATENCY_PROBE_PERIOD_S") or "0.5")
LATENCY_PROBE_ENABLED = os.environ.get("LATENCY_PROBE_ENABLED", "1") not in ("0", "false", "False")
WATCHDOG_PERIOD_S = float(os.environ.get("OTT_WATCHDOG_PERIOD_S") or "2")
WATCHDOG_RELOAD_AFTER = int(os.environ.get("OTT_WATCHDOG_RELOAD_AFTER") or "2")
WATCHDOG_RELOAD_COOLDOWN_S = float(os.environ.get("OTT_WATCHDOG_RELOAD_COOLDOWN_S") or "20")

_last_socks_down = 0
_last_socks_up = 0
_last_socks_t = 0.0
_probe_rtts: collections.deque[float] = collections.deque(maxlen=8)


def _set_slo_gauges() -> None:
    if APP_LATENCY_MS is None:
        return
    with _lock:
        lat = float(_state.get("last_delay_ms") or 0.0)
        rtt = float(_state.get("probe_rtt_ms") or _state.get("last_delay_ms") or 0.0)
        tput = float(_state.get("rx_bitrate_mbps") or 0.0)
        tput_ul = float(_state.get("tx_bitrate_mbps") or 0.0)
    APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(lat)
    if APP_UE_RTT_MS is not None:
        APP_UE_RTT_MS.labels(ue_id=UE_ID).set(rtt)
    APP_UE_THROUGHPUT_MBPS.labels(ue_id=UE_ID).set(tput)
    if APP_UE_THROUGHPUT_DL_MBPS is not None:
        APP_UE_THROUGHPUT_DL_MBPS.labels(ue_id=UE_ID).set(tput)
    if APP_UE_THROUGHPUT_UL_MBPS is not None:
        APP_UE_THROUGHPUT_UL_MBPS.labels(ue_id=UE_ID).set(tput_ul)
    APP_LATENCY_MS.set(lat)
    APP_THROUGHPUT_MBPS.set(tput)



def _log_event(event_type: str, msg: str, **kwargs):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "ts": time.time(),
        "type": event_type,
        "msg": msg,
        **kwargs,
    }
    with _lock:
        _recent_events.appendleft(entry)
    logger.info(f"[{event_type}] {msg}")


def get_interface_ip(ifname: str) -> Optional[str]:
    """Retrieve the primary IPv4 address of a network interface."""
    if not ifname:
        return None
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-br", "addr", "show", "dev", ifname],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        parts = out.split()
        if len(parts) >= 3:
            return parts[2].split("/")[0]
    except Exception:
        pass
    return None


def _resolve_api_iface() -> str:
    global _api_iface_live
    if _api_iface_live and Path(f"/sys/class/net/{_api_iface_live}").is_dir():
        return _api_iface_live
    for cand in [API_IFACE_CFG, "eth0"]:
        if cand and Path(f"/sys/class/net/{cand}").is_dir():
            _api_iface_live = cand
            return cand
    return API_IFACE_CFG


def _discover_pdu_iface() -> Optional[str]:
    """OAI often names the tunnel oaitun_ue1 regardless of slice id."""
    candidates = []
    for raw in (TO_SERVER_IFACE_CFG, PDU_IFACE_CFG, f"oaitun_ue{SLICE_ID}", "oaitun_ue1", "oaitun_ue2", "oaitun_ue3", "oaitun_ue4", "oaitun_ue5"):
        if raw and raw not in candidates:
            candidates.append(raw)
    try:
        res = subprocess.run(["ip", "-br", "link"], capture_output=True, text=True, timeout=5)
        for line in res.stdout.splitlines():
            name = line.split()[0] if line.strip() else ""
            if name.startswith("oaitun") and name not in candidates:
                candidates.append(name)
    except Exception:
        pass
    for name in candidates:
        try:
            r = subprocess.run(
                ["ip", "-4", "addr", "show", "dev", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and "inet " in r.stdout:
                return name
        except Exception:
            continue
    return None

_resolve_to_server_iface = _discover_pdu_iface


def _pin_server_via_pdu(iface: str) -> bool:
    """Force application-server destinations out the 5G PDU (not Multus console)."""
    hosts = []
    for h in PDU_ROUTE_HOSTS.split(","):
        h = h.strip()
        if h and h not in hosts:
            hosts.append(h)
    if SERVER_RTSP_HOST and SERVER_RTSP_HOST not in hosts:
        hosts.append(SERVER_RTSP_HOST)
    ok = False
    for host in hosts:
        r = subprocess.run(
            ["ip", "route", "replace", f"{host}/32", "dev", iface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            ok = True
        else:
            logger.debug(f"route pin {host} via {iface} failed: {r.stderr.strip()}")
    return ok


def _pdu_iface() -> str:
    with _pdu_lock:
        return _pdu_iface_live or PDU_IFACE_CFG


def _console_hls_path(video_id: str) -> str:
    """Legacy MediaMTX path (only used when OTT_PLAY_MODE=mediamtx)."""
    return f"/live/{video_id}/index.m3u8"


def _rewrite_videos_for_console(videos: List[dict]) -> List[dict]:
    out = []
    for v in videos:
        item = dict(v)
        play_mode = str(item.get("play_mode") or os.environ.get("OTT_PLAY_MODE", "youtube"))
        item["play_mode"] = play_mode
        if play_mode != "mediamtx":
            yt = item.get("youtube_id") or ""
            if yt:
                item["youtube_url"] = item.get("youtube_url") or f"https://www.youtube.com/watch?v={yt}"
                item["embed_url"] = (
                    f"https://www.youtube.com/embed/{yt}?autoplay=1&mute=1&rel=0"
                )
            item["hls_url"] = ""
            item["hls_transport"] = "chromium-via-5g-pdu-socks"
        else:
            vid = str(item.get("id") or item.get("video_id") or "")
            if vid:
                item["hls_url"] = _console_hls_path(vid)
                item["hls_transport"] = "5g-pdu-proxy"
        out.append(item)
    return out


def _http_json(method: str, path: str, body: Optional[dict] = None, timeout: float = 5.0) -> dict:
    url = f"{SERVER_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def _wait_for_pdu():
    global _pdu_iface_live
    logger.info(f"Waiting for 5G PDU tunnel (prefer {PDU_IFACE_CFG}, auto-detect oaitun*)...")
    while True:
        try:
            iface = _discover_pdu_iface()
            if iface:
                res = subprocess.run(
                    ["ip", "-4", "addr", "show", "dev", iface],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                pdu_ip = ""
                for line in res.stdout.splitlines():
                    if "inet " in line:
                        pdu_ip = line.strip().split()[1].split("/")[0]
                        break
                if pdu_ip and _pin_server_via_pdu(iface):
                    with _pdu_lock:
                        _pdu_iface_live = iface
                    with _lock:
                        _state["pdu_ready"] = True
                        _state["pdu_ip"] = pdu_ip
                        _state["pdu_iface"] = iface
                    logger.info(f"PDU ready on {iface} ({pdu_ip}); pinned {PDU_ROUTE_HOSTS or SERVER_RTSP_HOST}")
                    _log_event(
                        "pdu_ready",
                        f"5G PDU ready; app-server traffic forced via {iface}",
                        iface=iface,
                        pdu_ip=pdu_ip,
                        hosts=PDU_ROUTE_HOSTS or SERVER_RTSP_HOST,
                    )
                    # Keep re-pinning: tunnel flaps drop host /32 routes.
                    while True:
                        time.sleep(8)
                        live = _discover_pdu_iface() or iface
                        with _pdu_lock:
                            _pdu_iface_live = live
                        _pin_server_via_pdu(live)
                        # Verify preferred path
                        try:
                            chk = subprocess.run(
                                ["ip", "route", "get", SERVER_RTSP_HOST],
                                capture_output=True,
                                text=True,
                                timeout=3,
                            )
                            if live not in chk.stdout:
                                logger.warning(
                                    f"route to {SERVER_RTSP_HOST} not on {live}: {chk.stdout.strip()}"
                                )
                                _pin_server_via_pdu(live)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"PDU check error: {e}")
        time.sleep(2)


def _heartbeat_loop():
    """Register with app server; apply portal Start/Stop/channel to Chromium."""
    global _last_socks_down, _last_socks_up, _last_socks_t
    last_cmd_channel = ""
    last_cmd_state = ""
    while True:
        try:
            socks = PDU_SOCKS.stats()
            now = time.time()
            bd = int(socks.get("bytes_down") or 0)
            bu = int(socks.get("bytes_up") or 0)
            bitrate_dl = 0.0
            bitrate_ul = 0.0
            if _last_socks_t > 0 and now > _last_socks_t:
                dt = now - _last_socks_t
                db = max(0, bd - _last_socks_down)
                dub = max(0, bu - _last_socks_up)
                bitrate_dl = (db * 8.0) / (dt * 1e6)
                bitrate_ul = (dub * 8.0) / (dt * 1e6)
            _last_socks_down = bd
            _last_socks_up = bu
            _last_socks_t = now

            with _lock:
                delay = _state["last_delay_ms"]
                drops = _state["dropped_frames"]
                total = _state["frames_received"]
                pdu_ip = _state["pdu_ip"]
                streaming = _state.get("streaming_enabled")
                client_st = "STREAMING" if streaming else "IDLE"
                _state["socks_bytes_down"] = bd
                _state["socks_bytes_up"] = bu
                if bitrate_dl > 0:
                    _state["rx_bitrate_mbps"] = round(bitrate_dl, 2)
                if bitrate_ul > 0:
                    _state["tx_bitrate_mbps"] = round(bitrate_ul, 2)
                bitrate = float(_state.get("rx_bitrate_mbps") or 0.0)
                fps = float(_state.get("rx_fps") or 0.0)


            data = _http_json(
                "POST",
                "/api/v1/clients/heartbeat",
                {
                    "client_id": CLIENT_ID,
                    "name": UE_NAME,
                    "ip": CONSOLE_IP,
                    "console_ip": CONSOLE_IP,
                    "console_mac": CONSOLE_MAC,
                    "pdu_ip": pdu_ip or None,
                    "state": client_st,
                    "net_delay_ms": round(delay, 1),
                    "rx_fps": round(fps, 1),
                    "rx_bitrate_mbps": round(bitrate, 2),
                    "dropped_frames": drops,
                    "total_frames": total,
                },
                timeout=10.0,
            )



            vid = str(data.get("selected_video_id") or data.get("assigned_channel") or "").strip()
            srv_state = str(data.get("state") or "").strip().upper()

            with _lock:
                if vid:
                    _state["selected_video_id"] = vid
                    _state["assigned_channel"] = vid
                if data.get("youtube_id"):
                    _state["youtube_id"] = data["youtube_id"]
                    if _state.get("play_mode") not in ("chromium_5g",):
                        _state["play_mode"] = data.get("play_mode") or "chromium_5g"
                if data.get("youtube_url"):
                    _state["youtube_url"] = data["youtube_url"]
                if data.get("embed_url"):
                    _state["embed_url"] = data["embed_url"]
                if data.get("hls_url") and (data.get("play_mode") == "mediamtx"):
                    _state["hls_url"] = _console_hls_path(
                        str(_state.get("selected_video_id") or "channel_1")
                    )
                if data.get("rtsp_url"):
                    _state["rtsp_url"] = data["rtsp_url"]
                if data.get("whep_url"):
                    _state["whep_url"] = data["whep_url"]
                _state["server_ok"] = True
                _state["server_last_ok"] = time.time()
                _state["server_error"] = ""

            # Portal desired-state only. Chromium load/play is the watchdog thread
            # so a hung CDP session cannot stall heartbeats (UEs vanishing from the portal).
            if srv_state == "STOPPED":
                if last_cmd_state != "STOPPED":
                    with _lock:
                        _state["streaming_enabled"] = False
                    last_cmd_channel = ""
                    _log_event("control", "App server STOPPED — watchdog will blank Chromium")
                last_cmd_state = "STOPPED"
            elif srv_state == "STREAMING":
                want = vid or str(os.environ.get("DEFAULT_CHANNEL") or "").strip()
                with _lock:
                    _state["streaming_enabled"] = True
                    if want:
                        _state["selected_video_id"] = want
                        _state["assigned_channel"] = want
                if want and want != last_cmd_channel:
                    _log_event("control", f"App server STREAMING — watchdog will play {want}")
                last_cmd_channel = want
                last_cmd_state = "STREAMING"
            elif srv_state:
                last_cmd_state = srv_state

        except Exception as e:
            with _lock:
                _state["server_ok"] = False
                _state["server_error"] = str(e)
            logger.warning("App-server heartbeat failed (%s): %s", SERVER_URL, e)
        _set_slo_gauges()
        time.sleep(1.5)


def _video_stream_loop():
    """Optional MediaMTX RTSP metrics path (disabled in YouTube-direct mode)."""
    if (os.environ.get("OTT_PLAY_MODE") or "youtube").lower() != "mediamtx":
        logger.info("YouTube-direct mode — skipping MediaMTX RTSP metrics pull")
        while True:
            time.sleep(60)
        return

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)

    last_calc_time = time.monotonic()
    last_frames = 0
    last_bytes = 0

    while True:
        with _lock:
            enabled = _state["streaming_enabled"]
            rtsp_url = _state["rtsp_url"]
            ch = _state["assigned_channel"]

        if not enabled or not rtsp_url:
            with _lock:
                _state["rx_fps"] = 0.0
                _state["rx_bitrate_mbps"] = 0.0
            time.sleep(1.0)
            continue

        _log_event(
            "stream_start",
            f"Connecting to MediaMTX RTSP {rtsp_url} over {_pdu_iface()}",
        )

        pipe_str = (
            f"rtspsrc location=\"{rtsp_url}\" protocols=tcp latency=0 ntp-sync=true "
            f"add-reference-timestamp-meta=true ! "
            f"rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! "
            f"appsink name=sink emit-signals=true max-buffers=4 drop=true sync=false"
        )

        try:
            pipeline = Gst.parse_launch(pipe_str)
            sink = pipeline.get_by_name("sink")

            def _on_sample(s):
                sample = s.emit("pull-sample")
                if not sample:
                    return Gst.FlowReturn.OK
                buf = sample.get_buffer()
                if not buf:
                    return Gst.FlowReturn.OK

                now = time.time()
                with _lock:
                    _state["frames_received"] += 1
                    _state["bytes_received"] += buf.get_size()
                    _state["last_frame_time"] = now

                meta = buf.get_reference_timestamp_meta(None)
                if meta and meta.timestamp > 0:
                    capture_ts = meta.timestamp / 1e9
                    delay = now - capture_ts
                    if 0 < delay < 30.0:
                        _delays.append(delay)
                        min_d = min(_delays)
                        jitter = max(5.0, (delay - min_d) * 1000.0)
                        with _lock:
                            _state["last_delay_ms"] = jitter

                return Gst.FlowReturn.OK

            if sink:
                sink.connect("new-sample", _on_sample)

            pipeline.set_state(Gst.State.PLAYING)

            while True:
                with _lock:
                    if (
                        not _state["streaming_enabled"]
                        or _state["assigned_channel"] != ch
                        or _state["rtsp_url"] != rtsp_url
                    ):
                        break

                now = time.monotonic()
                dt = now - last_calc_time
                if dt >= 1.0:
                    with _lock:
                        df = _state["frames_received"] - last_frames
                        db = _state["bytes_received"] - last_bytes
                        _state["rx_fps"] = round(df / dt, 1)
                        _state["rx_bitrate_mbps"] = round((db * 8.0) / (dt * 1e6), 2)
                        last_frames = _state["frames_received"]
                        last_bytes = _state["bytes_received"]
                    last_calc_time = now
                    _set_slo_gauges()

                time.sleep(0.5)

            pipeline.set_state(Gst.State.NULL)
        except Exception as e:
            _log_event("stream_error", f"Stream error: {e}")
            time.sleep(2.0)


def _latency_probe_loop() -> None:
    """Indirect OTT latency: small HTTP echo over the PDU.

    Chromium/YouTube fills the 5G downlink; this probe's RTT rises with queueing
    even though YouTube itself has no timestamp metadata.
    """
    if not LATENCY_PROBE_ENABLED:
        logger.info("Latency probe disabled (LATENCY_PROBE_ENABLED=0)")
        return
    period = max(0.1, LATENCY_PROBE_PERIOD_S)
    logger.info("Latency probe every %.2fs via %s", period, SERVER_URL)
    while True:
        with _lock:
            ready = bool(_state.get("pdu_ready"))
        if not ready:
            time.sleep(1.0)
            continue
        try:
            res = subprocess.run(
                ["ping", "-c", "1", "-W", "1", "10.1.137.1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                m = re.search(r"time=([0-9.]+)", res.stdout)
                if m:
                    rtt_ms = float(m.group(1))
                    with _lock:
                        _probe_rtts.append(rtt_ms)
                        avg = sum(_probe_rtts) / len(_probe_rtts)
                        _state["last_delay_ms"] = round(avg, 2)
                        _state["probe_rtt_ms"] = round(avg, 2)
                        _state["probe_ok"] = int(_state.get("probe_ok") or 0) + 1
                    _set_slo_gauges()

            else:
                with _lock:
                    _state["probe_fail"] = int(_state.get("probe_fail") or 0) + 1
        except Exception as exc:
            with _lock:
                _state["probe_fail"] = int(_state.get("probe_fail") or 0) + 1
            logger.debug("ping probe failed: %s", exc)
        time.sleep(period)



def _watchdog_reload_page(video_id: str, youtube_id: str, quality: str) -> None:
    """Navigate Chromium back to the assigned watch URL (or full select if unknown)."""
    yt_id = (youtube_id or "").strip()
    if not yt_id:
        _play_chromium(video_id, quality=quality)
        return
    yt_q = chrome_ctl.normalize_quality(quality)
    vq = chrome_ctl.youtube_vq_param(yt_q)
    play_url = f"https://www.youtube.com/watch?v={yt_id}"
    if vq:
        play_url = f"{play_url}&vq={vq}"
    chrome_ctl.navigate(play_url)
    with _lock:
        _state["chrome_url"] = play_url
        _state["embed_url"] = play_url
        _state["streaming_enabled"] = True


def _playback_watchdog_loop() -> None:
    """Dedicated thread: page loaded + video playing; reload if Chromium never loaded.

    Must not run CDP on the heartbeat thread. Never call _log_event while holding
    _lock around Chromium I/O.
    """
    period = max(1.0, WATCHDOG_PERIOD_S)
    logger.info(
        "Playback watchdog thread started (period=%.0fs ad_poll=0.75s reload_after=%s cooldown=%.0fs)",
        period,
        WATCHDOG_RELOAD_AFTER,
        WATCHDOG_RELOAD_COOLDOWN_S,
    )
    bad_ticks = 0
    stall_ticks = 0
    last_current_time = -1.0
    last_reload = 0.0
    last_blank = 0.0
    prev_action = "initialized"
    while True:
        event_msg = None
        action = "idle"
        res: Dict[str, Any] = {}
        try:
            with _lock:
                enabled = bool(_state.get("periodic_check_enabled"))
                streaming = bool(_state.get("streaming_enabled"))
                quality = str(_state.get("play_quality") or DEFAULT_PLAY_QUALITY or "4k")
                pdu_ready = bool(_state.get("pdu_ready"))
                expect_yt = str(_state.get("youtube_id") or "").strip()
                video_id = str(
                    _state.get("selected_video_id")
                    or _state.get("assigned_channel")
                    or os.environ.get("DEFAULT_CHANNEL")
                    or ""
                ).strip()

            now = time.time()
            if not enabled:
                action = "disabled"
            elif not streaming:
                action = "not_streaming"
                chrome = chrome_ctl.status()
                cur = str(chrome.get("url") or "")
                if cur and "about:blank" not in cur.lower() and (now - last_blank) > 10.0:
                    try:
                        with _cdp_lock:
                            chrome_ctl.blank()
                        last_blank = now
                        action = "blanked"
                        event_msg = "Watchdog blanked Chromium (streaming stopped)"
                    except Exception as exc:
                        action = f"blank_failed: {exc}"
            elif not pdu_ready:
                action = "waiting_pdu"
            elif not chrome_ctl.cdp_ready(timeout=1.0):
                action = "waiting_cdp"
                bad_ticks += 1
            else:
                with _cdp_lock:
                    res = chrome_ctl.check_and_heal_playback(
                        quality=quality, video_id=expect_yt or None
                    )
                url = str(res.get("url") or res.get("page_url") or "")
                url_l = url.lower()
                page_yt = str(res.get("page_video_id") or "").strip()
                page_loaded = bool(res.get("page_loaded")) and "youtube.com/watch" in url_l
                has_video = bool(res.get("has_video") or res.get("has_player"))
                playing = bool(res.get("playing"))
                paused = bool(res.get("paused"))
                chrome_error = bool(res.get("chrome_error"))
                ad = bool(res.get("ad_detected") or res.get("ad_skipped"))
                buffering = bool(res.get("waiting")) and not paused
                wrong_video = bool(expect_yt and page_yt and page_yt != expect_yt)
                ct = float(res.get("current_time") or 0.0)

                if ad or buffering:
                    stall_ticks = 0
                    last_current_time = ct if ct else last_current_time
                elif playing and page_loaded and not wrong_video:
                    if ct > 0 and last_current_time >= 0 and abs(ct - last_current_time) < 0.15:
                        stall_ticks += 1
                    else:
                        stall_ticks = 0
                    last_current_time = ct
                else:
                    stall_ticks = 0

                stalled = stall_ticks >= 4
                not_loaded = (
                    not ad
                    and not buffering
                    and (
                        not res.get("ok")
                        or chrome_error
                        or not page_loaded
                        or not has_video
                        or wrong_video
                        or stalled
                        or "youtube.com/watch" not in url_l
                    )
                )

                if ad:
                    clicked = bool(res.get("ad_skipped"))
                    action = "ad_skipped" if clicked else "ad"
                    if clicked and prev_action != "ad_skipped":
                        event_msg = "Advertisement detected — skip click sent"
                    bad_ticks = 0
                elif buffering:
                    action = "buffering"
                    bad_ticks = 0
                elif playing and page_loaded and not wrong_video and not stalled:
                    action = "playing_ok"
                    bad_ticks = 0
                elif res.get("resumed"):
                    action = "playback_resumed"
                    event_msg = "YouTube was paused — auto-resumed playback"
                    bad_ticks = 0
                elif not_loaded:
                    bad_ticks += 1
                    if chrome_error or not page_loaded:
                        action = "page_not_loaded"
                    elif stalled:
                        action = "video_stalled"
                    elif wrong_video:
                        action = "wrong_video"
                    else:
                        action = "video_not_running"
                else:
                    action = "idle"
                    bad_ticks = 0

                need_reload = (
                    not_loaded
                    and bad_ticks >= WATCHDOG_RELOAD_AFTER
                    and bool(video_id)
                    and (now - last_reload) >= WATCHDOG_RELOAD_COOLDOWN_S
                )
                if need_reload:
                    ticks_before = bad_ticks
                    try:
                        with _cdp_lock:
                            _watchdog_reload_page(video_id, expect_yt, quality)
                        last_reload = now
                        bad_ticks = 0
                        stall_ticks = 0
                        last_current_time = -1.0
                        action = "reloaded"
                        event_msg = (
                            f"Watchdog reloaded Chromium "
                            f"(url={url[:80] or 'empty'} ticks={ticks_before})"
                        )
                    except Exception as exc:
                        action = f"reload_failed: {exc}"
                        event_msg = f"Watchdog reload failed: {exc}"

            with _lock:
                _state["watchdog_last_check"] = now
                _state["watchdog_last_action"] = action
                qinfo = res.get("quality") if isinstance(res, dict) else None
                if isinstance(qinfo, dict):
                    if qinfo.get("current"):
                        _state["yt_quality"] = qinfo.get("current")
                    if qinfo.get("height"):
                        _state["video_height"] = int(qinfo.get("height") or 0)
                if action == "ad_skipped" and prev_action != "ad_skipped":
                    _state["ads_skipped_count"] = int(_state.get("ads_skipped_count") or 0) + 1
                elif action == "playback_resumed":
                    _state["resumed_count"] = int(_state.get("resumed_count") or 0) + 1
                elif action == "reloaded":
                    _state["watchdog_reload_count"] = int(_state.get("watchdog_reload_count") or 0) + 1

            if event_msg:
                _log_event("watchdog", event_msg)
            prev_action = action
        except Exception as exc:
            logger.warning("Playback watchdog tick failed: %s", exc)
            prev_action = "error"
        sleep_for = 0.75 if action in ("ad", "ad_skipped") else period
        time.sleep(sleep_for)


@app.on_event("startup")
def on_startup():
    if start_http_server is not None:
        try:
            start_http_server(METRICS_PORT, addr="0.0.0.0")
            logger.info(f"Prometheus metrics on :{METRICS_PORT}")
        except Exception as exc:
            logger.warning(f"metrics server failed: {exc}")
    # SOCKS for Chromium starts immediately; PDU IP is refreshed when discovered.
    try:
        PDU_SOCKS.start(pdu_ip="")
    except Exception as exc:
        logger.warning("PDU SOCKS start failed: %s", exc)
    threading.Thread(target=_wait_for_pdu, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    threading.Thread(target=_video_stream_loop, daemon=True).start()
    threading.Thread(target=_socks_pdu_refresh_loop, daemon=True).start()
    threading.Thread(target=_chrome_autostart_loop, daemon=True, name="chrome-autostart").start()
    threading.Thread(target=_latency_probe_loop, daemon=True, name="latency-probe").start()
    threading.Thread(
        target=_playback_watchdog_loop, daemon=True, name="ott-playback-watchdog"
    ).start()


def _socks_pdu_refresh_loop() -> None:
    while True:
        try:
            _iface, pdu_ip = _pdu_coords()
            if pdu_ip:
                PDU_SOCKS.set_pdu_ip(pdu_ip)

        except Exception:
            pass
        time.sleep(5.0)


@app.get("/api/interfaces")
def api_interfaces() -> dict:
    to_srv_iface = _discover_pdu_iface() or _pdu_iface() or TO_SERVER_IFACE_CFG
    to_srv_ip = get_interface_ip(to_srv_iface) if to_srv_iface else None
    api_if = _resolve_api_iface()
    api_ip = get_interface_ip(api_if)
    return {
        "ok": True,
        "to_server_interface": {
            "name": to_srv_iface,
            "configured": TO_SERVER_IFACE_CFG,
            "type": "ran",
            "ip": to_srv_ip,
            "ready": bool(to_srv_ip),
        },
        "api_interface": {
            "name": api_if,
            "configured": API_IFACE_CFG,
            "type": "api",
            "ip": api_ip,
            "ready": bool(api_ip),
        },
    }


@app.get("/api/status")
def get_status():
    chrome = chrome_ctl.status()
    socks = PDU_SOCKS.stats()
    to_srv_iface = _discover_pdu_iface() or _pdu_iface() or TO_SERVER_IFACE_CFG
    to_srv_ip = get_interface_ip(to_srv_iface) if to_srv_iface else None
    api_if = _resolve_api_iface()
    api_ip = get_interface_ip(api_if)
    with _lock:
        st = dict(_state)
        st["socks_bytes_up"] = socks.get("bytes_up") or 0
        st["socks_bytes_down"] = socks.get("bytes_down") or 0
        st["chrome_url"] = chrome.get("url") or st.get("chrome_url") or ""
        st["chrome_ui"] = chrome.get("chrome_ui") or st.get("chrome_ui")
        st["chrome_cdp_ready"] = chrome.get("cdp_ready")
    return {
        "ok": True,
        "ue_name": UE_NAME,
        "client_id": CLIENT_ID,
        "slice_id": SLICE_ID,
        "client_index": CLIENT_INDEX,
        "console_ip": CONSOLE_IP,
        "console_mac": CONSOLE_MAC,
        "to_server_iface": to_srv_iface,
        "to_server_ip": to_srv_ip,
        "api_iface": api_if,
        "api_ip": api_ip,
        "interfaces": {
            "to_server": {
                "name": to_srv_iface,
                "configured": TO_SERVER_IFACE_CFG,
                "type": "ran",
                "ip": to_srv_ip,
                "ready": bool(to_srv_ip),
            },
            "api": {
                "name": api_if,
                "configured": API_IFACE_CFG,
                "type": "api",
                "ip": api_ip,
                "ready": bool(api_ip),
            },
        },
        "server_url": SERVER_URL,
        "server": {
            "url": SERVER_URL,
            "ok": bool(st.get("server_ok")),
            "last_ok": st.get("server_last_ok") or 0,
            "error": st.get("server_error") or "",
            "via": "5g-pdu" if st.get("pdu_ready") else "waiting-pdu",
            "pdu_route_hosts": PDU_ROUTE_HOSTS or SERVER_RTSP_HOST,
        },
        "pdu_iface": _pdu_iface(),
        "pdu_route_hosts": PDU_ROUTE_HOSTS or SERVER_RTSP_HOST,
        "chrome": chrome,
        "socks": socks,
        "state": st,
        "recent_events": list(_recent_events),
    }


@app.get("/api/videos")
def get_videos():
    """Ask OTT server for the video list; rewrite HLS to UE /live proxy (5G path)."""
    global _video_cache
    try:
        data = _http_json("GET", "/api/v1/videos", timeout=5.0)
        videos = data.get("videos") or []
        _video_cache = videos
        return {
            "ok": True,
            "videos": _rewrite_videos_for_console(videos),
            "source": "server",
            "hls_transport": "5g-pdu-proxy",
        }
    except Exception as exc:
        if _video_cache:
            return {
                "ok": True,
                "videos": _rewrite_videos_for_console(_video_cache),
                "source": "cache",
                "warning": str(exc),
                "hls_transport": "5g-pdu-proxy",
            }
        raise HTTPException(status_code=502, detail=f"server video list unavailable: {exc}") from exc


@app.api_route("/live/{path:path}", methods=["GET", "HEAD"])
def proxy_live(path: str, request: Request):
    """Pull MediaMTX HLS over the pinned 5G PDU; browser only hits Multus console."""
    global _pdu_iface_live
    iface = _discover_pdu_iface() or _pdu_iface()
    if iface:
        _pin_server_via_pdu(iface)
        with _pdu_lock:
            _pdu_iface_live = iface
    url = f"{SERVER_URL}/live/{path}"
    # MediaMTX returns 404 for HEAD; always GET upstream and strip body for HEAD.
    upstream_method = "GET"
    req = urllib.request.Request(url, method=upstream_method)
    range_hdr = request.headers.get("range")
    if range_hdr and request.method != "HEAD":
        req.add_header("Range", range_hdr)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            # Keep playlist URIs on the UE console proxy (never Multus app IP).
            if path.endswith(".m3u8") and body:
                text = body.decode("utf-8", errors="replace")
                text = text.replace(f"{SERVER_URL}/live/", "/live/")
                text = text.replace(f"http://{SERVER_RTSP_HOST}/live/", "/live/")
                body = text.encode("utf-8")
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Access-Control-Allow-Origin": "*",
            }
            for key in ("Accept-Ranges", "Content-Range"):
                if resp.headers.get(key):
                    headers[key] = resp.headers[key]
            if request.method == "HEAD":
                headers["Content-Length"] = str(len(body))
                return Response(
                    content=b"",
                    status_code=200,
                    media_type=ctype,
                    headers=headers,
                )
            headers["Content-Length"] = str(len(body))
            return Response(
                content=body,
                status_code=resp.status,
                media_type=ctype,
                headers=headers,
            )
    except urllib.error.HTTPError as exc:
        return Response(content=exc.read() if request.method != "HEAD" else b"", status_code=exc.code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"live proxy via PDU failed: {exc}") from exc


def _pdu_coords() -> tuple[str, str]:
    iface = _discover_pdu_iface() or _pdu_iface()
    pdu_ip = ""
    if iface:
        _pin_server_via_pdu(iface)
        try:
            res = subprocess.run(
                ["ip", "-4", "addr", "show", "dev", iface],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in res.stdout.splitlines():
                if "inet " in line:
                    pdu_ip = line.strip().split()[1].split("/")[0]
                    break
        except Exception:
            pass
    if not pdu_ip:
        with _lock:
            pdu_ip = str(_state.get("pdu_ip") or "")
    return iface or "", pdu_ip


def _pick_autostart_video_id() -> str:
    with _lock:
        vid = str(_state.get("selected_video_id") or _state.get("assigned_channel") or "").strip()
    if vid:
        return vid
    # Check videos from server catalog first to allow modulo rotation across available channels
    try:
        data = _http_json("GET", "/api/v1/videos", timeout=5.0)
        videos = data.get("videos") or []
        if videos:
            idx = (max(1, CLIENT_INDEX) - 1) % len(videos)
            return str(videos[idx].get("id") or "").strip() or "channel_1"
    except Exception as exc:
        logger.debug("autostart catalog fetch failed: %s", exc)

    # Per-UE default from deploy (channel_1..N), then env override, then channel_1.
    for key in ("DEFAULT_CHANNEL", "OTT_AUTOSTART_VIDEO"):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            return raw
    return "channel_1"



def _play_chromium(video_id: str, quality: str | None = None) -> Dict[str, Any]:
    """Select a video and navigate Chromium via CDP (egress through PDU SOCKS)."""
    q_in = (quality or DEFAULT_PLAY_QUALITY or "4k").strip() or "4k"
    yt_q = chrome_ctl.normalize_quality(q_in)
    try:
        data = _http_json(
            "POST",
            f"/api/v1/clients/{CLIENT_ID}/select",
            {"video_id": video_id},
            timeout=8.0,
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    youtube_id = (
        data.get("youtube_id")
        or extract_youtube_id(data.get("youtube_url") or "")
        or extract_youtube_id(video_id)
        or ""
    )
    youtube_url = data.get("youtube_url") or (
        f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else ""
    )
    if not youtube_id:
        raise RuntimeError("No YouTube ID for this video")

    iface, pdu_ip = _pdu_coords()
    if not pdu_ip or not iface:
        raise RuntimeError("5G PDU not ready — cannot route Chromium via air")
    PDU_SOCKS.set_pdu_ip(pdu_ip)
    PDU_SOCKS.start(pdu_ip=pdu_ip)

    # Full watch page — /embed/ triggers YouTube Error 153 in this Chromium/proxy path.
    vq = chrome_ctl.youtube_vq_param(yt_q)
    play_url = f"https://www.youtube.com/watch?v={youtube_id}"
    if vq:
        play_url = f"{play_url}&vq={vq}"
    try:
        with _cdp_lock:
            nav = chrome_ctl.navigate(play_url)
            try:
                chrome_ctl.play_youtube(quality=yt_q)
            except Exception:
                pass
    except Exception as exc:
        raise RuntimeError(f"Chromium navigate failed: {exc}") from exc

    chrome_ui = f"https://{CONSOLE_IP}/chrome/"
    with _lock:
        _state["selected_video_id"] = video_id
        _state["assigned_channel"] = video_id
        _state["play_mode"] = "chromium_5g"
        _state["youtube_id"] = youtube_id
        _state["youtube_url"] = youtube_url
        _state["embed_url"] = play_url
        _state["chrome_url"] = play_url
        _state["chrome_ui"] = chrome_ui
        _state["play_quality"] = q_in
        _state["yt_quality"] = yt_q
        _state["hls_url"] = ""
        _state["rtsp_url"] = ""
        _state["whep_url"] = ""
        _state["streaming_enabled"] = True
    _log_event(
        "play",
        f"Chromium → {youtube_id} quality={q_in}/{yt_q} via SOCKS/PDU {iface}/{pdu_ip}",
    )
    return {
        "ok": True,
        "video_id": video_id,
        "play_mode": "chromium_5g",
        "youtube_id": youtube_id,
        "youtube_url": youtube_url,
        "quality": q_in,
        "yt_quality": yt_q,
        "chrome_url": play_url,
        "chrome_ui": chrome_ui,
        "nav": nav,
        "socks": PDU_SOCKS.stats(),
        "pdu_iface": iface,
        "pdu_ip": pdu_ip,
        "hls_transport": "chromium-via-5g-pdu-socks",
        "video": data.get("video"),
    }


def _chrome_autostart_loop() -> None:
    """Once CDP + PDU are up, play the assigned/first video. Frontend refresh must not re-trigger this."""
    logger.info("Autostart: waiting for Chromium CDP + 5G PDU…")
    while True:
        try:
            if not chrome_ctl.cdp_ready(timeout=2.0):
                time.sleep(2.0)
                continue
            _iface, pdu_ip = _pdu_coords()
            if not pdu_ip:
                time.sleep(2.0)
                continue

            chrome = chrome_ctl.status()
            cur = str(chrome.get("url") or "")
            with _lock:
                yt = str(_state.get("youtube_id") or "")
                streaming = bool(_state.get("streaming_enabled"))
            # Chromium may already be playing (backend restart, frontend refresh never reaches here).
            if "youtube.com/watch" in cur:
                if yt and f"watch?v={yt}" in cur and streaming:
                    _log_event("autostart", f"Chromium already on {yt}; skip navigate")
                    return
                _log_event("autostart", f"Chromium already on YouTube ({cur[:80]}); skip navigate")
                with _lock:
                    _state["streaming_enabled"] = True
                    _state["play_mode"] = "chromium_5g"
                    _state["chrome_url"] = cur
                return

            video_id = _pick_autostart_video_id()
            q = DEFAULT_PLAY_QUALITY
            _log_event("autostart", f"CDP+PDU ready — playing {video_id} quality={q}")
            _play_chromium(video_id, quality=q)
            return
        except Exception as exc:
            logger.warning("autostart retry: %s", exc)
            time.sleep(5.0)


@app.post("/api/play")
def play_video(req: PlayIn):
    """Select a video; backend navigates Chromium to YouTube (egress via PDU SOCKS)."""
    try:
        return _play_chromium(req.video_id, quality=req.quality)
    except RuntimeError as exc:
        msg = str(exc)
        if "PDU not ready" in msg:
            raise HTTPException(status_code=503, detail=msg) from exc
        if "No YouTube ID" in msg:
            raise HTTPException(status_code=400, detail=msg) from exc
        if "navigate failed" in msg:
            raise HTTPException(status_code=502, detail=msg) from exc
        raise HTTPException(status_code=502, detail=msg) from exc


@app.get("/api/chrome")
def chrome_status():
    return {"ok": True, **chrome_ctl.status(), "socks": PDU_SOCKS.stats()}


@app.post("/api/streaming/enable")
def enable_streaming():
    with _lock:
        if not (
            _state.get("youtube_id")
            or _state.get("hls_url")
            or _state.get("rtsp_url")
        ):
            raise HTTPException(status_code=400, detail="Select a video first (POST /api/play)")
        _state["streaming_enabled"] = True
    try:
        _http_json("POST", f"/api/v1/clients/{CLIENT_ID}/start", {})
    except Exception:
        pass
    _log_event("control", "Streaming enabled by UE console")
    return {"ok": True, "streaming_enabled": True}


@app.post("/api/streaming/disable")
def disable_streaming():
    with _lock:
        _state["streaming_enabled"] = False
    try:
        chrome_ctl.blank()
    except Exception:
        pass
    try:
        _http_json("POST", f"/api/v1/clients/{CLIENT_ID}/stop", {})
    except Exception:
        pass
    _log_event("control", "Streaming disabled — Chromium blanked")
    return {"ok": True, "streaming_enabled": False}


@app.post("/api/channel/set")
def set_channel(req: Dict[str, str]):
    """Compat: treat channel_id as video select."""
    video_id = req.get("channel_id") or req.get("video_id") or ""
    if not video_id:
        raise HTTPException(status_code=400, detail="channel_id required")
    quality = req.get("quality") or DEFAULT_PLAY_QUALITY
    return play_video(PlayIn(video_id=video_id, quality=quality))


@app.get("/api/watchdog")
def get_watchdog():
    with _lock:
        return {
            "ok": True,
            "periodic_check_enabled": bool(_state.get("periodic_check_enabled")),
            "interval_s": WATCHDOG_PERIOD_S,
            "watchdog_last_check": _state.get("watchdog_last_check") or 0.0,
            "watchdog_last_action": _state.get("watchdog_last_action") or "",
            "ads_skipped_count": _state.get("ads_skipped_count") or 0,
            "resumed_count": _state.get("resumed_count") or 0,
            "watchdog_reload_count": _state.get("watchdog_reload_count") or 0,
        }


@app.post("/api/watchdog/toggle")
def toggle_watchdog(req: Optional[WatchdogToggleIn] = None):
    with _lock:
        if req and req.enabled is not None:
            _state["periodic_check_enabled"] = bool(req.enabled)
        else:
            _state["periodic_check_enabled"] = not bool(_state.get("periodic_check_enabled"))
        current = bool(_state["periodic_check_enabled"])

    status_str = "ENABLED" if current else "DISABLED"
    _log_event("watchdog", f"Periodic 5s video playback & ad-skipping check {status_str}")
    return {
        "ok": True,
        "periodic_check_enabled": current,
        "msg": f"Watchdog periodic check {status_str}",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=BACKEND_PORT, log_level="info")

