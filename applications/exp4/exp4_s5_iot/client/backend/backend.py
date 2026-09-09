#!/usr/bin/env python3
"""IoT UE backend: configurable MQTT publishers over the 5G PDU."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import types
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import sys

for _p in ("/usr/local/bin", "/app/backend", "/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from to_server import detect_to_server_iface, ensure_pin_watch, pin_to_server
except ImportError:
    detect_to_server_iface = None  # type: ignore
    ensure_pin_watch = None  # type: ignore
    pin_to_server = None  # type: ignore
from ue_control import (
    Iperf3Client,
    LogBuffer,
    app_fields,
    attach_app_routes,
    attach_iperf_routes,
)

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore

try:
    from prometheus_client import Gauge, start_http_server
except ImportError:  # pragma: no cover
    Gauge = None  # type: ignore
    start_http_server = None  # type: ignore

SLICE_ID = int(os.environ.get("SLICE_ID", "5"))
CLIENT_INDEX = int(os.environ.get("CLIENT_INDEX", "1"))
UE_NAME = os.environ.get("UE_NAME", f"oai-ue-slice-{SLICE_ID}-client-{CLIENT_INDEX}")
DEVICE_ID = os.environ.get("DEVICE_ID") or f"ue{CLIENT_INDEX}"
CONSOLE_IP = os.environ.get("CONSOLE_IP", "")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", "")
BROKER_HOST = (
    os.environ.get("BROKER_HOST")
    or os.environ.get("TARGET_SERVER_IP")
    or "10.1.137.215"
)
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))
MQTT_QOS = int(os.environ.get("MQTT_QOS", "0"))
# ---------------------------------------------------------------------------
# Two ifaces: TO_SERVER = sim 5G / PDU data; CONSOLE = Multus UI
# Dual Multus (SCHEME_ID=exp4-no5g): TO_SERVER=net1, CONSOLE=net2.
# With 5G: TO_SERVER=oaitun_ue*, CONSOLE=net1.
# ---------------------------------------------------------------------------
_NO5G = (
    os.environ.get("SCHEME_ID") == "exp4-no5g"
    or os.environ.get("EXP4_NO5G", "").lower() in ("1", "true", "yes")
)
TO_SERVER_IFACE_CFG = (
    os.environ.get("TO_SERVER_IFACE")
    or os.environ.get("SERVER_IFACE")
    or os.environ.get("RAN_IFACE")
    or os.environ.get("PDU_IFACE")
    or ("net1" if _NO5G else f"oaitun_ue{SLICE_ID}")
)
API_IFACE_CFG = (
    os.environ.get("CONSOLE_IFACE")
    or os.environ.get("API_IFACE")
    or ("net2" if _NO5G else "net1")
)
PDU_IFACE_CFG = TO_SERVER_IFACE_CFG
PDU_ROUTE_HOSTS = os.environ.get("PDU_ROUTE_HOSTS", "") or BROKER_HOST
PDU_WAIT_TIMEOUT = int(os.environ.get("PDU_WAIT_TIMEOUT", "0"))  # 0 = never give up
PDU_POLL_S = float(os.environ.get("PDU_POLL_S", "2"))
LOG_LIMIT = int(os.environ.get("PUBLISH_LOG_LIMIT", "80"))
STAT_WINDOW_S = float(os.environ.get("MQTT_STAT_WINDOW_S", "30"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9106"))
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8090"))
UE_ID = os.environ.get("APP_NAME") or UE_NAME
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "slice_5")
UL_TOPIC = os.environ.get("UL_TOPIC") or f"{MQTT_TOPIC_PREFIX}/ul/{DEVICE_ID}"
DL_TOPIC = os.environ.get("DL_TOPIC") or f"{MQTT_TOPIC_PREFIX}/dl/{DEVICE_ID}"
LATENCY_TOPIC = os.environ.get("LATENCY_TOPIC") or f"{MQTT_TOPIC_PREFIX}/latency/{DEVICE_ID}"
PROBE_TOPIC = os.environ.get("PROBE_TOPIC") or f"{MQTT_TOPIC_PREFIX}/probe/{DEVICE_ID}"
PROBE_ACK_TOPIC = os.environ.get("PROBE_ACK_TOPIC") or f"{MQTT_TOPIC_PREFIX}/probe-ack/{DEVICE_ID}"
LATENCY_PROBE_PERIOD_S = float(os.environ.get("LATENCY_PROBE_PERIOD_S") or "0.5")
# DL-only default: no UL probe publish from UE client → server.
LATENCY_PROBE_ENABLED = os.environ.get("LATENCY_PROBE_ENABLED", "0") not in ("0", "false", "False")
# Kernel receive buffer so bursts from Mosquitto are not window-limited.
MQTT_SO_RCVBUF = max(65536, int(os.environ.get("MQTT_SO_RCVBUF", str(4 * 1024 * 1024))))
# Log / latency-file sample rate for bulk DL (every Nth message). 1 = every msg.
MQTT_DL_LOG_EVERY = max(1, int(os.environ.get("MQTT_DL_LOG_EVERY", "20")))
_T_SEND_RE = re.compile(rb'"t_send":([0-9.]+)')
_SEQ_RE = re.compile(rb'"seq":([0-9]+)')
_dl_rx_n = 0

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
    APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
    APP_THROUGHPUT_MBPS = Gauge(
        "app_throughput_mbps", "Aggregated application throughput (Mbps)"
    )
else:
    APP_UE_LATENCY_MS = APP_UE_RTT_MS = APP_UE_THROUGHPUT_MBPS = APP_LATENCY_MS = APP_THROUGHPUT_MBPS = None


_lock = threading.Lock()
_exchanges: deque[dict[str, Any]] = deque(maxlen=LOG_LIMIT)
_topic_stats: dict[str, dict[str, Any]] = {}
_pub_stop = threading.Event()
_mqtt_client: Any = None
_mqtt_connected = False
_seq = 0
_probe_seq = 0
_bytes_window = 0
_window_t0 = time.monotonic()
_probe_rtts: deque[float] = deque(maxlen=8)

MIN_FREQ_HZ = 0.01
MAX_FREQ_HZ = float(os.environ.get("MAX_FREQ_HZ", "500"))  # allow high-rate MQTT

# Target ~2 Mbit/s application MQTT (SLA T_bar). One UL stream: freq × payload ≈ target.
TARGET_MBPS = float(os.environ.get("TARGET_MBPS", "2"))
# Prefer a sustainable rate; pad bytes so TARGET_MBPS holds even if Hz is lower.
UL_FREQ_HZ = float(os.environ.get("UL_FREQ_HZ", "50"))
_ul_bytes_default = max(256, int(TARGET_MBPS * 1e6 / 8.0 / max(UL_FREQ_HZ, 1.0)))
UL_PAYLOAD_BYTES = int(os.environ.get("UL_PAYLOAD_BYTES", str(_ul_bytes_default)))

DEFAULT_MESSAGES = [
    {
        "id": "ul-bulk",
        "frequency_hz": UL_FREQ_HZ,
        "period_s": round(1.0 / UL_FREQ_HZ, 6),
        "payload": "",
        "payload_bytes": UL_PAYLOAD_BYTES,
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _freq_from_times(times: list[float]) -> float:
    if len(times) < 2:
        return 0.0
    dt = times[-1] - times[0]
    if dt <= 0:
        return 0.0
    return round((len(times) - 1) / dt, 4)


def _avg_freq_hz(times: list[float], now: float, window_s: float) -> float:
    samples = [t for t in times if now - t <= window_s]
    if not samples:
        return 0.0
    span = min(window_s, now - samples[0])
    if span < 0.5:
        return 0.0
    return round(len(samples) / span, 4)


def _note_topic(topic: str, nbytes: int = 0, direction: str = "") -> None:
    if not topic:
        return
    now = time.monotonic()
    st = _topic_stats.setdefault(
        topic,
        {
            "count": 0,
            "bytes": 0,
            "last_ts": None,
            "direction": direction or "other",
            "times": deque(maxlen=8000),
            "win_bytes": deque(maxlen=8000),
        },
    )
    if direction:
        st["direction"] = direction
    st["count"] = int(st["count"] or 0) + 1
    st["bytes"] = int(st.get("bytes") or 0) + int(nbytes or 0)
    # Rolling window bytes for Mbps
    win = st.setdefault("win_bytes", deque(maxlen=8000))
    win.append((now, int(nbytes or 0)))
    while win and now - win[0][0] > STAT_WINDOW_S:
        win.popleft()
    st["last_ts"] = _now()
    times = st["times"]
    times.append(now)
    while times and now - times[0] > STAT_WINDOW_S:
        times.popleft()


def _ensure_known_topics() -> None:
    for topic, direction in ((UL_TOPIC, "uplink"), (DL_TOPIC, "downlink")):
        _topic_stats.setdefault(
            topic,
            {
                "count": 0,
                "bytes": 0,
                "last_ts": None,
                "direction": direction,
                "times": deque(maxlen=8000),
                "win_bytes": deque(maxlen=8000),
            },
        )


def _window_mbps(win: deque) -> float:
    now = time.monotonic()
    samples = [(t, b) for t, b in win if now - t <= STAT_WINDOW_S]
    if len(samples) < 2:
        return 0.0
    span = max(0.2, samples[-1][0] - samples[0][0])
    total = sum(b for _, b in samples)
    return round((total * 8.0) / (span * 1e6), 4)


def _stats_snapshot() -> dict[str, Any]:
    now = time.monotonic()
    _ensure_known_topics()
    topics = []
    ul_times: list[float] = []
    dl_times: list[float] = []
    all_times: list[float] = []
    ul_mbps = 0.0
    dl_mbps = 0.0
    for topic, st in sorted(_topic_stats.items()):
        times = [t for t in (st.get("times") or []) if now - t <= STAT_WINDOW_S]
        direction = st.get("direction") or (
            "uplink" if "/ul/" in topic else "downlink" if "/dl/" in topic else "other"
        )
        all_times.extend(times)
        mbps = _window_mbps(st.get("win_bytes") or deque())
        if direction == "uplink":
            ul_times.extend(times)
            ul_mbps += mbps
        elif direction == "downlink":
            dl_times.extend(times)
            dl_mbps += mbps
        topics.append(
            {
                "topic": topic,
                "direction": direction,
                "count": int(st.get("count") or 0),
                "bytes": int(st.get("bytes") or 0),
                "last_ts": st.get("last_ts"),
                "window_s": STAT_WINDOW_S,
                "window_count": len(times),
                "freq_hz": _freq_from_times(times),
                "avg_freq_hz": _avg_freq_hz(times, now, STAT_WINDOW_S),
                "mbps": mbps,
            }
        )
    configured = 0.0
    for m in _messages:
        try:
            configured += float(m.get("frequency_hz") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "ue": UE_NAME,
        "device_id": DEVICE_ID,
        "client_index": CLIENT_INDEX,
        "topic_count": len(topics),
        "topics": topics,
        "rx_count": sum(int(t["count"] or 0) for t in topics),
        "window_s": STAT_WINDOW_S,
        "avg_freq_hz": _avg_freq_hz(all_times, now, STAT_WINDOW_S),
        "ul_avg_freq_hz": _avg_freq_hz(ul_times, now, STAT_WINDOW_S),
        "dl_avg_freq_hz": _avg_freq_hz(dl_times, now, STAT_WINDOW_S),
        "ul_mbps": round(ul_mbps, 4),
        "dl_mbps": round(dl_mbps, 4),
        "target_mbps": TARGET_MBPS,
        "ul_payload_bytes": UL_PAYLOAD_BYTES,
        "ul_freq_hz": UL_FREQ_HZ,
        "configured_hz": round(configured, 4),
    }


def _freq_and_period(m: dict[str, Any]) -> tuple[float, float, int]:
    hz: Optional[float] = None
    if m.get("frequency_hz") is not None:
        try:
            hz = float(m.get("frequency_hz"))
        except (TypeError, ValueError):
            hz = None
    if hz is None or hz <= 0:
        try:
            period = float(m.get("period_s") or 0)
        except (TypeError, ValueError):
            period = 0.0
        hz = (1.0 / period) if period > 0 else UL_FREQ_HZ
    hz = min(MAX_FREQ_HZ, max(MIN_FREQ_HZ, hz))
    try:
        payload_bytes = int(m.get("payload_bytes") or 0)
    except (TypeError, ValueError):
        payload_bytes = 0
    if payload_bytes <= 0:
        payload_bytes = UL_PAYLOAD_BYTES
    return round(hz, 6), round(1.0 / hz, 6), max(64, payload_bytes)


def _normalize_messages(raw: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        raw = []
    for i, m in enumerate(raw, 1):
        if not isinstance(m, dict):
            continue
        hz, period_s, payload_bytes = _freq_and_period(m)
        payload = m.get("payload")
        if payload is None:
            payload = ""
        elif not isinstance(payload, str):
            payload = json.dumps(payload)
        mid = str(m.get("id") or f"msg-{i}").strip() or f"msg-{i}"
        items.append(
            {
                "id": mid,
                "frequency_hz": hz,
                "period_s": period_s,
                "payload": payload,
                "payload_bytes": payload_bytes,
            }
        )
    return items or [dict(x) for x in DEFAULT_MESSAGES]


def _messages_from_env() -> list[dict[str, Any]]:
    raw = os.environ.get("IOT_MESSAGES") or os.environ.get("MESSAGES_JSON") or ""
    if raw.strip():
        return _normalize_messages(raw)
    n = int(os.environ.get("NUM_MESSAGES") or "0")
    if n > 0:
        items = []
        for i in range(1, n + 1):
            row: dict[str, Any] = {
                "id": f"msg-{i}",
                "payload": os.environ.get(f"MSG{i}_PAYLOAD") or f'{{"sensor":{i}}}',
            }
            freq = os.environ.get(f"MSG{i}_FREQ_HZ") or os.environ.get(f"MSG{i}_FREQUENCY_HZ")
            if freq:
                row["frequency_hz"] = float(freq)
            else:
                row["period_s"] = float(os.environ.get(f"MSG{i}_PERIOD_S") or os.environ.get("FAST_PERIOD_S") or 5)
            items.append(row)
        return _normalize_messages(items)
    return [dict(x) for x in DEFAULT_MESSAGES]


_messages = _messages_from_env()
APP_AUTOSTART = os.environ.get("APP_AUTOSTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_APP_WANTED = APP_AUTOSTART
_mqtt_subscribed = False
APP_LOG = LogBuffer(kind="mqtt")
IPERF = Iperf3Client()
_state = {
    # DL-only default: UE client receives MQTT; does not publish UL to server.
    "send_enabled": os.environ.get("SEND_ENABLED", "0") not in ("0", "false", "False"),
    "pdu_ready": False,
    "pdu_iface": "",
    "last_error": None,
    "loop_alive": False,
    "mqtt_connected": False,
    "mqtt_subscribed": False,
    "published": 0,
    "probe_rtt_ms": 0.0,
    "probe_owd_ms": 0.0,
    "last_delay_ms": 0.0,
    "probe_ok": 0,
    "probe_fail": 0,
}


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
    for cand in [API_IFACE_CFG, "net1", "eth0"]:
        if cand and Path(f"/sys/class/net/{cand}").is_dir():
            _api_iface_live = cand
            return cand
    return API_IFACE_CFG


def _log(msg: str) -> None:
    print(f"[exp4-s5] {msg}", flush=True)
    APP_LOG.append(msg)


def _discover_pdu_iface() -> Optional[str]:
    """Return to-server iface once it has IPv4. OAI creates oaitun_ue1 after PDU."""
    if detect_to_server_iface is not None:
        return detect_to_server_iface()
    candidates = []
    preferred = (TO_SERVER_IFACE_CFG, PDU_IFACE_CFG, "net1") if _NO5G else (
        TO_SERVER_IFACE_CFG,
        PDU_IFACE_CFG,
        "oaitun_ue1",
        f"oaitun_ue{SLICE_ID}",
        "oaitun_ue2",
        "oaitun_ue3",
        "oaitun_ue4",
        "oaitun_ue5",
    )
    for raw in preferred:
        if raw and raw not in candidates:
            candidates.append(raw)
    if not _NO5G:
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


def _server_hosts() -> list[str]:
    hosts: list[str] = ["10.1.137.1"]
    for h in str(PDU_ROUTE_HOSTS).split(","):
        h = h.strip()
        if h and h not in hosts:
            hosts.append(h)
    if BROKER_HOST and BROKER_HOST not in hosts:
        hosts.append(BROKER_HOST)
    return hosts


def _ping_loop() -> None:
    while True:
        try:
            with _lock:
                ready = bool(_state.get("pdu_ready"))
            if ready:
                res = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", "10.1.137.1"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    import re

                    m = re.search(r"time=([0-9.]+)\s*ms", res.stdout)
                    if m:
                        rtt = float(m.group(1))
                        if APP_UE_RTT_MS is not None:
                            APP_UE_RTT_MS.labels(ue_id=UE_ID).set(rtt)
        except Exception:
            pass
        time.sleep(1.0)



def _pin_pdu() -> bool:
    global _pdu_iface_live
    if pin_to_server is not None:
        iface = pin_to_server()
        if iface:
            with _pdu_lock:
                _pdu_iface_live = iface
            return True
        return False
    hosts = _server_hosts()
    if not hosts:
        return True
    iface = _discover_pdu_iface()
    if not iface:
        return False
    ok = False
    for host in hosts:
        r = subprocess.run(
            ["ip", "route", "replace", f"{host}/32", "dev", iface],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            ok = True
    if ok:
        with _pdu_lock:
            _pdu_iface_live = iface
    return ok


def _wait_pdu() -> None:
    """Block until the to-server iface exists (optional timeout). 0 = forever."""
    elapsed = 0.0
    while PDU_WAIT_TIMEOUT <= 0 or elapsed < PDU_WAIT_TIMEOUT:
        if _pin_pdu():
            with _lock:
                _state["pdu_ready"] = True
                _state["pdu_iface"] = _pdu_iface_live
                _state["last_error"] = None
            _log(f"to-server ready on {_pdu_iface_live}")
            return
        time.sleep(PDU_POLL_S)
        elapsed += PDU_POLL_S
    with _lock:
        _state["pdu_ready"] = False
        _state["last_error"] = f"PDU (prefer {PDU_IFACE_CFG}) not ready after {PDU_WAIT_TIMEOUT}s"


def _mqtt_reset() -> None:
    """Drop the broker TCP so the next connect uses the PDU /32, not net2."""
    global _mqtt_client, _mqtt_connected, _mqtt_subscribed
    client = _mqtt_client
    _mqtt_client = None
    _mqtt_connected = False
    _mqtt_subscribed = False
    with _lock:
        _state["mqtt_connected"] = False
        _state["mqtt_subscribed"] = False
    if client is None:
        return
    try:
        client.loop_stop()
    except Exception:
        pass
    try:
        client.disconnect()
    except Exception:
        pass


def _dl_topics() -> tuple[str, ...]:
    return (DL_TOPIC, LATENCY_TOPIC, PROBE_ACK_TOPIC)


def _set_subscribed(ok: bool) -> None:
    global _mqtt_subscribed
    _mqtt_subscribed = bool(ok)
    with _lock:
        _state["mqtt_subscribed"] = bool(ok)


def _mqtt_subscribe_topics(client: Any) -> None:
    for topic in _dl_topics():
        client.subscribe(topic, qos=MQTT_QOS)
    _set_subscribed(True)
    _log(f"subscribed {', '.join(_dl_topics())}")


def _mqtt_unsubscribe_topics(client: Any) -> None:
    for topic in _dl_topics():
        try:
            client.unsubscribe(topic)
        except Exception:
            pass
    _set_subscribed(False)
    _log(f"unsubscribed {', '.join(_dl_topics())}")


def _watch_pdu_loop() -> None:
    """UE only creates oaitun after PDU setup. Keep looking and re-pin / reconnect."""
    was_ready = False
    while True:
        ok = _pin_pdu()
        with _lock:
            _state["pdu_ready"] = ok
            _state["pdu_iface"] = _pdu_iface_live if ok else ""
            if ok:
                _state["last_error"] = None
            else:
                _state["last_error"] = f"waiting for to-server iface {PDU_IFACE_CFG}"
        if ok and not was_ready:
            _log(f"to-server {_pdu_iface_live} up; pinning {','.join(_server_hosts())} and reconnecting MQTT")
            _mqtt_reset()
            if _APP_WANTED:
                try:
                    _ensure_mqtt()
                except Exception as exc:
                    with _lock:
                        _state["last_error"] = str(exc)
                    _log(f"MQTT connect after PDU failed: {exc}")
        elif not ok and was_ready:
            _log(f"to-server iface gone; waiting for PDU ({PDU_IFACE_CFG})")
            _mqtt_reset()
        was_ready = ok
        time.sleep(PDU_POLL_S)


def _payload_body(msg: dict[str, Any], seq: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "seq": seq,
        "tier": msg.get("id") or "msg",
        "msg_id": msg.get("id") or "msg",
        "ue": UE_NAME,
        "app_name": UE_ID,
        "client_index": CLIENT_INDEX,
        "console_ip": CONSOLE_IP or get_interface_ip(_resolve_api_iface()) or "",
        "console_url": f"http://{CONSOLE_IP or get_interface_ip(_resolve_api_iface())}:80" if (CONSOLE_IP or get_interface_ip(_resolve_api_iface())) else "",
    }
    raw = msg.get("payload") or ""
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            body["payload"] = parsed
        except json.JSONDecodeError:
            body["payload"] = raw
    elif isinstance(raw, (dict, list)):
        body["payload"] = raw
    return body


def _encode_payload(msg: dict[str, Any], seq: int) -> bytes:
    """Stamp ``t_send`` and pad to ``payload_bytes`` for target Mbps."""
    body = _payload_body(msg, seq)
    body["pad"] = ""
    body["t_send"] = time.time()
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    try:
        target = int(msg.get("payload_bytes") or UL_PAYLOAD_BYTES)
    except (TypeError, ValueError):
        target = UL_PAYLOAD_BYTES
    target = max(len(raw), target)
    pad_len = max(0, target - len(raw))
    if pad_len:
        body["pad"] = "x" * pad_len
        # Re-stamp after pad sizing so t_send is close to publish.
        body["t_send"] = time.time()
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    return raw


def _record(entry: dict[str, Any]) -> None:
    global _bytes_window, _window_t0
    with _lock:
        _exchanges.appendleft(entry)
        if entry.get("ok"):
            if entry.get("direction") == "uplink":
                _state["published"] = int(_state.get("published") or 0) + 1
            _state["last_error"] = None
        else:
            _state["last_error"] = entry.get("error")
        nbytes = int(entry.get("bytes") or 0)
        _bytes_window += nbytes
        if entry.get("ok"):
            _note_topic(str(entry.get("topic") or ""), nbytes, str(entry.get("direction") or ""))
        now = time.monotonic()
        dt = max(0.2, now - _window_t0)
        mbps = (_bytes_window * 8.0) / (dt * 1e6)
        if dt >= 5.0:
            _bytes_window = 0
            _window_t0 = now
    if APP_LATENCY_MS is not None:
        APP_UE_THROUGHPUT_MBPS.labels(ue_id=UE_ID).set(mbps)
        APP_THROUGHPUT_MBPS.set(mbps)
        # Path latency comes from the dedicated probe thread (RTT / OWD).
        if entry.get("direction") == "probe" and entry.get("latency_ms") is not None:
            lat = float(entry.get("latency_ms") or 0.0)
            APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(lat)
            APP_LATENCY_MS.set(lat)
        elif entry.get("direction") == "latency" and entry.get("latency_ms") is not None:
            lat = float(entry.get("latency_ms") or 0.0)
            APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(lat)
            APP_LATENCY_MS.set(lat)


def _mqtt_on_connect(client, _userdata, _flags, reason_code, _properties=None):
    global _mqtt_connected
    ok = int(getattr(reason_code, "value", reason_code) or 0) == 0
    _mqtt_connected = ok
    with _lock:
        _state["mqtt_connected"] = ok
        if not ok:
            _state["last_error"] = f"MQTT connect failed: {reason_code}"
            _state["mqtt_subscribed"] = False
    if not ok:
        _set_subscribed(False)
        return
    # Re-subscribe after reconnect only when the console wants DL.
    if _APP_WANTED:
        try:
            _mqtt_subscribe_topics(client)
        except Exception as exc:
            _set_subscribed(False)
            _log(f"subscribe after connect failed: {exc}")
    else:
        _set_subscribed(False)


def _mqtt_on_disconnect(_client, _userdata, _flags, reason_code, _properties=None):
    global _mqtt_connected
    _mqtt_connected = False
    with _lock:
        _state["mqtt_connected"] = False
        _state["mqtt_subscribed"] = False
    _set_subscribed(False)


def _paho_loop_read_drain(self, max_packets: int = 1):
    """Drain the broker socket until EAGAIN.

    paho-mqtt 2.x ``loop_read`` overwrites max_packets with the QoS 1/2 inflight
    count, so QoS 0 reads **one** MQTT packet per select() and leaves Recv-Q in
    the kernel. Keep reading until the socket is empty so the TCP window stays
    open and Mosquitto can push as fast as the path allows.
    """
    if self._sock is None:
        return mqtt.MQTT_ERR_NO_CONN
    again = mqtt.MQTT_ERR_AGAIN
    success = mqtt.MQTT_ERR_SUCCESS
    while True:
        if self._sock is None:
            return mqtt.MQTT_ERR_NO_CONN
        rc = self._packet_read()
        if rc == again:
            return success
        if rc != success:
            if rc > 0:
                return self._loop_rc_handle(rc)
            return rc


def _mqtt_on_socket_open(_client, _userdata, sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, MQTT_SO_RCVBUF)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
    except (OSError, AttributeError):
        pass


def _extract_re_float(raw: bytes, cre: re.Pattern[bytes]) -> Optional[float]:
    m = cre.search(raw)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _on_dl_fast(topic: str, raw: bytes, nbytes: int, recv: float) -> None:
    """Count DL bytes and OWD without json.loads of the pad."""
    global _dl_rx_n, _bytes_window, _window_t0
    t_send = _extract_re_float(raw, _T_SEND_RE)
    lat = max(0.0, (recv - t_send) * 1000.0) if t_send else None
    _dl_rx_n += 1
    log_it = (_dl_rx_n % MQTT_DL_LOG_EVERY) == 0
    mbps = 0.0
    with _lock:
        _note_topic(topic, nbytes, "downlink")
        _bytes_window += nbytes
        now = time.monotonic()
        dt = max(0.2, now - _window_t0)
        mbps = (_bytes_window * 8.0) / (dt * 1e6)
        if dt >= 5.0:
            _bytes_window = 0
            _window_t0 = now
        if lat is not None:
            _state["last_delay_ms"] = round(lat, 2)
        if log_it:
            seq = None
            sm = _SEQ_RE.search(raw)
            if sm:
                try:
                    seq = int(sm.group(1))
                except ValueError:
                    seq = None
            _exchanges.appendleft(
                {
                    "ts": _now(),
                    "direction": "downlink",
                    "ok": True,
                    "topic": topic,
                    "bytes": nbytes,
                    "latency_ms": round(lat, 2) if lat is not None else None,
                    "seq": seq,
                }
            )
            _state["last_error"] = None
    if APP_LATENCY_MS is not None:
        APP_UE_THROUGHPUT_MBPS.labels(ue_id=UE_ID).set(mbps)
        APP_THROUGHPUT_MBPS.set(mbps)
        if lat is not None:
            APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(lat)
            APP_LATENCY_MS.set(lat)
    if log_it and lat is not None:
        try:
            open("/tmp/exp4_app_latency_ms", "w", encoding="utf-8").write(f"{lat:.3f}\n")
        except OSError:
            pass


def _mqtt_on_message(_client, _userdata, msg):
    recv = time.time()
    raw = msg.payload or b""
    topic = str(msg.topic or "")
    try:
        if topic == DL_TOPIC or "/dl/" in topic:
            _on_dl_fast(topic, raw, len(raw), recv)
            return
    except Exception:
        return
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if topic == PROBE_ACK_TOPIC or topic.startswith("slice_d/probe-ack/"):
        t_send = parsed.get("t_send") if isinstance(parsed, dict) else None
        t_recv = parsed.get("t_recv") if isinstance(parsed, dict) else None
        rtt_ms = None
        owd_ms = None
        if isinstance(t_send, (int, float)) and t_send > 0:
            rtt_ms = max(0.0, (recv - float(t_send)) * 1000.0)
        if isinstance(t_send, (int, float)) and isinstance(t_recv, (int, float)):
            owd_ms = max(0.0, (float(t_recv) - float(t_send)) * 1000.0)
        if rtt_ms is not None:
            with _lock:
                _probe_rtts.append(rtt_ms)
                avg = sum(_probe_rtts) / len(_probe_rtts)
                _state["probe_rtt_ms"] = round(avg, 2)
                if owd_ms is not None:
                    _state["probe_owd_ms"] = round(owd_ms, 2)
                _state["last_delay_ms"] = round(avg, 2)
                _state["probe_ok"] = int(_state.get("probe_ok") or 0) + 1
            if APP_LATENCY_MS is not None:
                APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(avg)
                APP_LATENCY_MS.set(avg)


            # Keep the console log readable: sample probes, always log spikes.
            if (_probe_seq % 10 == 0) or (rtt_ms >= 40.0):
                _record(
                    {
                        "ts": _now(),
                        "direction": "probe",
                        "ok": True,
                        "topic": topic,
                        "bytes": len(raw),
                        "latency_ms": round(rtt_ms, 2),
                        "owd_ms": round(owd_ms, 2) if owd_ms is not None else None,
                        "seq": parsed.get("seq") if isinstance(parsed, dict) else None,
                        "payload": parsed if parsed is not None else None,
                    }
                )
        return
    if topic == LATENCY_TOPIC or topic.startswith("slice_d/latency/"):
        lat = None
        if isinstance(parsed, dict) and parsed.get("latency_ms") is not None:
            try:
                lat = float(parsed["latency_ms"])
            except (TypeError, ValueError):
                lat = None
        if lat is not None:
            try:
                open("/tmp/exp4_e2e_latency_ms", "w", encoding="utf-8").write(f"{lat:.3f}\n")
            except OSError:
                pass
        _record(
            {
                "ts": _now(),
                "direction": "latency",
                "ok": True,
                "topic": topic,
                "bytes": len(raw),
                "latency_ms": lat,
                "seq": parsed.get("seq") if isinstance(parsed, dict) else None,
                "payload": parsed if parsed is not None else raw[:200].decode("utf-8", "replace"),
            }
        )
        return
    t_send = None
    if isinstance(parsed, dict):
        t_send = parsed.get("t_send")
    lat = None
    if isinstance(t_send, (int, float)) and t_send > 0:
        lat = max(0.0, (recv - float(t_send)) * 1000.0)
    _record(
        {
            "ts": _now(),
            "direction": "downlink",
            "ok": True,
            "topic": topic,
            "bytes": len(raw),
            "latency_ms": lat,
            "payload": parsed if parsed is not None else raw[:200].decode("utf-8", "replace"),
        }
    )


def _ensure_mqtt() -> Any:
    global _mqtt_client
    if mqtt is None:
        raise RuntimeError("paho-mqtt is not installed")
    if _mqtt_client is not None:
        return _mqtt_client
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"iot-{DEVICE_ID}",
        protocol=mqtt.MQTTv311,
        clean_session=True,
    )
    client.on_connect = _mqtt_on_connect
    client.on_disconnect = _mqtt_on_disconnect
    client.on_message = _mqtt_on_message
    client.on_socket_open = _mqtt_on_socket_open
    try:
        client.loop_read = types.MethodType(_paho_loop_read_drain, client)
    except Exception:
        pass
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    bind = ""
    if not _NO5G:
        iface = _discover_pdu_iface()
        bind = get_interface_ip(iface) if iface else ""
        if not bind:
            raise RuntimeError("MQTT deferred until to-server iface has IPv4")
    client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60, bind_address=bind)
    client.loop_start()
    _mqtt_client = client
    _log(
        f"MQTT connecting to {BROKER_HOST}:{BROKER_PORT} bind={bind or 'any'} "
        f"drain=until-empty rcvbuf={MQTT_SO_RCVBUF}"
    )
    return client


def _publish_one(msg: dict[str, Any]) -> dict[str, Any]:
    global _seq
    with _lock:
        _seq += 1
        seq = _seq
    payload = _encode_payload(msg, seq)
    try:
        client = _ensure_mqtt()
        info = client.publish(UL_TOPIC, payload, qos=MQTT_QOS)
        ok = info.rc == mqtt.MQTT_ERR_SUCCESS
        err = None if ok else f"publish rc={info.rc}"
    except Exception as exc:
        ok = False
        err = str(exc)
    entry = {
        "ts": _now(),
        "direction": "uplink",
        "ok": ok,
        "topic": UL_TOPIC,
        "msg_id": msg.get("id"),
        "seq": seq,
        "bytes": len(payload),
        "period_s": msg.get("period_s"),
        "frequency_hz": msg.get("frequency_hz"),
        "error": err,
    }
    _record(entry)
    return entry


def _publish_loop(msg: dict[str, Any], stop: threading.Event) -> None:
    _, period, _ = _freq_and_period(msg)
    next_t = time.monotonic() + min(period, 0.5)
    while not stop.is_set():
        with _lock:
            enabled = bool(_state["send_enabled"])
            ready = bool(_state["pdu_ready"])
        if enabled and ready:
            try:
                _publish_one(msg)
            except Exception as exc:
                _record(
                    {
                        "ts": _now(),
                        "direction": "uplink",
                        "ok": False,
                        "msg_id": msg.get("id"),
                        "error": str(exc),
                    }
                )
        next_t += period
        delay = next_t - time.monotonic()
        if delay > 0:
            stop.wait(delay)
        else:
            next_t = time.monotonic()
        _pin_pdu()


def _publish_probe() -> None:
    """Tiny timestamped ping; server echoes so RTT tracks PDU queueing (rises under load)."""
    global _probe_seq
    with _lock:
        _probe_seq += 1
        seq = _probe_seq
    body = {
        "device_id": DEVICE_ID,
        "seq": seq,
        "kind": "probe",
        "ue": UE_NAME,
        "app_name": UE_ID,
        "client_index": CLIENT_INDEX,
        "t_send": time.time(),
    }
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    try:
        client = _ensure_mqtt()
        info = client.publish(PROBE_TOPIC, payload, qos=MQTT_QOS)
        if mqtt is None or info.rc != mqtt.MQTT_ERR_SUCCESS:
            with _lock:
                _state["probe_fail"] = int(_state.get("probe_fail") or 0) + 1
    except Exception:
        with _lock:
            _state["probe_fail"] = int(_state.get("probe_fail") or 0) + 1


def _latency_probe_loop(stop: threading.Event) -> None:
    period = max(0.1, LATENCY_PROBE_PERIOD_S)
    stop.wait(min(period, 1.0))
    while not stop.is_set():
        with _lock:
            ready = bool(_state["pdu_ready"])
            connected = bool(_state["mqtt_connected"]) or _mqtt_connected
        if ready and connected:
            try:
                _publish_probe()
            except Exception:
                pass
        stop.wait(period)


def _restart_publishers() -> None:
    global _pub_stop
    _pub_stop.set()
    time.sleep(0.05)
    stop = threading.Event()
    _pub_stop = stop
    with _lock:
        msgs = [dict(m) for m in _messages]
        alive = bool(_state.get("loop_alive"))
        enabled = bool(_state.get("send_enabled"))
    if not alive or not enabled:
        return
    for msg in msgs:
        threading.Thread(
            target=_publish_loop,
            args=(msg, stop),
            name=f"pub-{msg.get('id')}",
            daemon=True,
        ).start()


def _loop() -> None:
    with _lock:
        _state["loop_alive"] = True
        _state["last_error"] = f"waiting for to-server iface {PDU_IFACE_CFG}"
    _restart_publishers()
    if LATENCY_PROBE_ENABLED:
        threading.Thread(
            target=_latency_probe_loop,
            args=(threading.Event(),),
            name="iot-latency-probe",
            daemon=True,
        ).start()
    if _NO5G:
        _wait_pdu()
        try:
            if _APP_WANTED:
                _ensure_mqtt()
        except Exception as exc:
            with _lock:
                _state["last_error"] = str(exc)
        while True:
            time.sleep(PDU_POLL_S)
            _pin_pdu()
        return
    _log(f"watching for to-server iface {PDU_IFACE_CFG} (UE creates it after PDU)")
    _watch_pdu_loop()


app = FastAPI(title=f"IoT UE {CLIENT_INDEX} backend", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ControlIn(BaseModel):
    send_enabled: Optional[bool] = None


class MessageIn(BaseModel):
    id: Optional[str] = None
    period_s: Optional[float] = Field(None, gt=0)
    frequency_hz: Optional[float] = Field(None, gt=0)
    payload: Optional[Any] = None


class ConfigIn(BaseModel):
    messages: list[MessageIn] = Field(default_factory=list)


class PublishOnceIn(BaseModel):
    id: Optional[str] = None
    payload: Optional[str] = None


@app.get("/api/status")
def api_status() -> dict:
    with _lock:
        st = dict(_state)
        n = len(_exchanges)
        last = _exchanges[0] if _exchanges else None
        msgs = [dict(m) for m in _messages]
        stats = _stats_snapshot()
    return {
        "ok": True,
        "ue": UE_NAME,
        "device_id": DEVICE_ID,
        "slice_id": SLICE_ID,
        "client_index": CLIENT_INDEX,
        "console_ip": CONSOLE_IP,
        "console_mac": CONSOLE_MAC,
        "broker": f"mqtt://{BROKER_HOST}:{BROKER_PORT}",
        "ul_topic": UL_TOPIC,
        "dl_topic": DL_TOPIC,
        "probe_topic": PROBE_TOPIC,
        "probe_period_s": LATENCY_PROBE_PERIOD_S,
        "pdu_iface": _pdu_iface_live or PDU_IFACE_CFG,
        "to_server_iface": _pdu_iface_live or TO_SERVER_IFACE_CFG,
        "to_server_ip": get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG),
        "api_iface": _resolve_api_iface(),
        "api_ip": get_interface_ip(_resolve_api_iface()),
        "interfaces": {
            "to_server": {
                "name": _pdu_iface_live or TO_SERVER_IFACE_CFG,
                "configured": TO_SERVER_IFACE_CFG,
                "type": "ran",
                "ip": get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG),
                "ready": bool(get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG)),
            },
            "api": {
                "name": _resolve_api_iface(),
                "configured": API_IFACE_CFG,
                "type": "api",
                "ip": get_interface_ip(_resolve_api_iface()),
                "ready": bool(get_interface_ip(_resolve_api_iface())),
            },
        },
        "message_count": len(msgs),
        "messages": msgs,
        "exchanges": n,
        "last": last,
        "stats": stats,
        **st,
        "mqtt_connected": _mqtt_connected,
        "mqtt_subscribed": _mqtt_subscribed,
        "app": app_fields(
            running=bool(_mqtt_subscribed),
            wanted=bool(_APP_WANTED),
            log=APP_LOG,
            extra={"kind": "mqtt", "subscribed": bool(_mqtt_subscribed)},
        ),
        "iperf": IPERF.snapshot(),
    }


@app.get("/api/interfaces")
def api_interfaces() -> dict:
    to_srv_iface = _resolve_to_server_iface() or TO_SERVER_IFACE_CFG
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


@app.get("/api/stats")
def api_stats() -> dict:
    with _lock:
        snap = _stats_snapshot()
    return {"ok": True, **snap}


@app.get("/api/config")
def api_get_config() -> dict:
    with _lock:
        msgs = [dict(m) for m in _messages]
    return {"ok": True, "message_count": len(msgs), "messages": msgs}


@app.post("/api/config")
def api_set_config(body: ConfigIn) -> dict:
    global _messages
    msgs = _normalize_messages([m.model_dump() for m in (body.messages or [])])
    with _lock:
        _messages = msgs
    _restart_publishers()
    return {"ok": True, "message_count": len(msgs), "messages": msgs}


@app.get("/api/exchanges")
def api_exchanges(limit: int = 40) -> dict:
    lim = max(1, min(int(limit), LOG_LIMIT))
    with _lock:
        items = list(_exchanges)[:lim]
    return {"ok": True, "ue": UE_NAME, "items": items}


@app.post("/api/control")
def api_control(body: ControlIn) -> dict:
    with _lock:
        if body.send_enabled is not None:
            _state["send_enabled"] = bool(body.send_enabled)
        st = dict(_state)
        msgs = [dict(m) for m in _messages]
    return {"ok": True, "messages": msgs, **st}


@app.post("/api/publish-once")
def api_publish_once(body: Optional[PublishOnceIn] = None) -> dict:
    with _lock:
        msgs = [dict(m) for m in _messages]
    msg = msgs[0] if msgs else dict(DEFAULT_MESSAGES[0])
    if body and body.id:
        found = next((m for m in msgs if m.get("id") == body.id), None)
        if found:
            msg = found
    if body and body.payload is not None:
        msg = dict(msg)
        msg["payload"] = body.payload
    return _publish_one(msg)


def _app_start() -> None:
    """Subscribe to DL topics (connect to broker if needed)."""
    global _APP_WANTED
    _APP_WANTED = True
    try:
        client = _ensure_mqtt()
        if _mqtt_connected:
            _mqtt_subscribe_topics(client)
        else:
            _log("MQTT connecting; will subscribe on connect")
    except Exception as exc:
        _log(f"MQTT subscribe failed: {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _app_stop() -> None:
    """Unsubscribe from DL topics; keep broker TCP for a fast re-subscribe."""
    global _APP_WANTED
    _APP_WANTED = False
    client = _mqtt_client
    if client is not None and _mqtt_connected:
        try:
            _mqtt_unsubscribe_topics(client)
        except Exception as exc:
            _log(f"MQTT unsubscribe failed: {exc}")
            _set_subscribed(False)
    else:
        _set_subscribed(False)
        _log("MQTT unsubscribed (not connected)")


attach_iperf_routes(app, IPERF)
attach_app_routes(app, start_fn=_app_start, stop_fn=_app_stop, log=APP_LOG)


@app.post("/api/mqtt/subscribe")
def api_mqtt_subscribe() -> dict:
    _app_start()
    return {
        "ok": True,
        "action": "subscribe",
        "mqtt_connected": _mqtt_connected,
        "mqtt_subscribed": _mqtt_subscribed,
        "topics": list(_dl_topics()),
    }


@app.post("/api/mqtt/unsubscribe")
def api_mqtt_unsubscribe() -> dict:
    _app_stop()
    return {
        "ok": True,
        "action": "unsubscribe",
        "mqtt_connected": _mqtt_connected,
        "mqtt_subscribed": _mqtt_subscribed,
        "topics": list(_dl_topics()),
    }


@app.on_event("startup")
def _startup() -> None:
    if start_http_server is not None:
        try:
            start_http_server(METRICS_PORT, addr="0.0.0.0")
        except Exception:
            pass
    try:
        from heartbeat import start as start_heartbeat
    except ImportError:
        start_heartbeat = None  # type: ignore[assignment]

    def _hb() -> dict:
        return {
            "client_id": DEVICE_ID,
            "name": UE_NAME,
            "console_ip": CONSOLE_IP,
            "console_url": f"http://{CONSOLE_IP}" if CONSOLE_IP else "",
            "detail": f"mqtt {DEVICE_ID}",
        }

    if start_heartbeat is not None:
        start_heartbeat(payload_fn=_hb)
    if ensure_pin_watch is not None:
        ensure_pin_watch()
    IPERF.start_supervisor()
    threading.Thread(target=_ping_loop, name="iot-ue-ping-loop", daemon=True).start()
    threading.Thread(target=_loop, name="iot-ue-loop", daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=BACKEND_PORT)

