#!/usr/bin/env python3
"""IoT UE backend: configurable MQTT publishers over the 5G PDU."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore

try:
    from prometheus_client import Gauge, start_http_server
except ImportError:  # pragma: no cover
    Gauge = None  # type: ignore
    start_http_server = None  # type: ignore

SLICE_ID = int(os.environ.get("SLICE_ID", "4"))
CLIENT_INDEX = int(os.environ.get("CLIENT_INDEX", "1"))
UE_NAME = os.environ.get("UE_NAME", f"oai-ue-slice-{SLICE_ID}-client-{CLIENT_INDEX}")
DEVICE_ID = os.environ.get("DEVICE_ID") or f"ue{CLIENT_INDEX}"
CONSOLE_IP = os.environ.get("CONSOLE_IP", "")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", "")
BROKER_HOST = (
    os.environ.get("BROKER_HOST")
    or os.environ.get("TARGET_SERVER_IP")
    or "10.1.137.214"
)
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))
MQTT_QOS = int(os.environ.get("MQTT_QOS", "0"))
PDU_IFACE_CFG = os.environ.get("PDU_IFACE", f"oaitun_ue{SLICE_ID}")
PDU_ROUTE_HOSTS = os.environ.get("PDU_ROUTE_HOSTS", "") or BROKER_HOST
PDU_WAIT_TIMEOUT = int(os.environ.get("PDU_WAIT_TIMEOUT", "300"))
LOG_LIMIT = int(os.environ.get("PUBLISH_LOG_LIMIT", "80"))
STAT_WINDOW_S = float(os.environ.get("MQTT_STAT_WINDOW_S", "30"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9106"))
UE_ID = os.environ.get("APP_NAME") or UE_NAME
UL_TOPIC = os.environ.get("UL_TOPIC") or f"slice_d/ul/{DEVICE_ID}"
DL_TOPIC = os.environ.get("DL_TOPIC") or f"slice_d/dl/{DEVICE_ID}"
LATENCY_TOPIC = os.environ.get("LATENCY_TOPIC") or f"slice_d/latency/{DEVICE_ID}"
PROBE_TOPIC = os.environ.get("PROBE_TOPIC") or f"slice_d/probe/{DEVICE_ID}"
PROBE_ACK_TOPIC = os.environ.get("PROBE_ACK_TOPIC") or f"slice_d/probe-ack/{DEVICE_ID}"
LATENCY_PROBE_PERIOD_S = float(os.environ.get("LATENCY_PROBE_PERIOD_S") or "0.5")
LATENCY_PROBE_ENABLED = os.environ.get("LATENCY_PROBE_ENABLED", "1") not in ("0", "false", "False")

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
MAX_FREQ_HZ = 5.0  # 0.2 s minimum interval

DEFAULT_MESSAGES = [
    {"id": "msg-1", "frequency_hz": 2.0, "period_s": 0.5, "payload": '{"temp": 22.1, "hum": 48}'},
    {"id": "msg-2", "frequency_hz": 1.0, "period_s": 1.0, "payload": '{"accel": [0.1, 0.0, 9.8]}'},
    {"id": "msg-3", "frequency_hz": 0.5, "period_s": 2.0, "payload": '{"rssi": -67}'},
    {"id": "msg-4", "frequency_hz": 0.25, "period_s": 4.0, "payload": '{"status": "ok"}'},
    {"id": "msg-5", "frequency_hz": 0.2, "period_s": 5.0, "payload": '{"co2": 410}'},
    {"id": "msg-6", "frequency_hz": 0.1, "period_s": 10.0, "payload": '{"door": "closed"}'},
    {"id": "msg-7", "frequency_hz": round(1.0 / 15.0, 6), "period_s": 15.0, "payload": '{"battery": 87}'},
    {"id": "msg-8", "frequency_hz": 0.05, "period_s": 20.0, "payload": '{"gps": {"lat": 22.3, "lon": 114.2}}'},
    {"id": "msg-9", "frequency_hz": 0.04, "period_s": 25.0, "payload": '{"uptime_s": 3600}'},
    {"id": "msg-10", "frequency_hz": round(1.0 / 30.0, 6), "period_s": 30.0, "payload": '{"fw": "v1.0"}'},
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
        {"count": 0, "bytes": 0, "last_ts": None, "direction": direction or "other", "times": deque(maxlen=500)},
    )
    if direction:
        st["direction"] = direction
    st["count"] = int(st["count"] or 0) + 1
    st["bytes"] = int(st["bytes"] or 0) + int(nbytes or 0)
    st["last_ts"] = _now()
    times = st["times"]
    times.append(now)
    while times and now - times[0] > STAT_WINDOW_S:
        times.popleft()


def _ensure_known_topics() -> None:
    for topic, direction in ((UL_TOPIC, "uplink"), (DL_TOPIC, "downlink")):
        _topic_stats.setdefault(
            topic,
            {"count": 0, "bytes": 0, "last_ts": None, "direction": direction, "times": deque(maxlen=500)},
        )


def _stats_snapshot() -> dict[str, Any]:
    now = time.monotonic()
    _ensure_known_topics()
    topics = []
    ul_times: list[float] = []
    all_times: list[float] = []
    for topic, st in sorted(_topic_stats.items()):
        times = [t for t in (st.get("times") or []) if now - t <= STAT_WINDOW_S]
        direction = st.get("direction") or ("uplink" if "/ul/" in topic else "downlink" if "/dl/" in topic else "other")
        all_times.extend(times)
        if direction == "uplink":
            ul_times.extend(times)
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
        "configured_hz": round(configured, 4),
    }


def _freq_and_period(m: dict[str, Any]) -> tuple[float, float]:
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
        hz = (1.0 / period) if period > 0 else 0.2
    hz = min(MAX_FREQ_HZ, max(MIN_FREQ_HZ, hz))
    return round(hz, 6), round(1.0 / hz, 6)


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
        hz, period_s = _freq_and_period(m)
        payload = m.get("payload")
        if payload is None:
            payload = ""
        elif not isinstance(payload, str):
            payload = json.dumps(payload)
        mid = str(m.get("id") or f"msg-{i}").strip() or f"msg-{i}"
        items.append({"id": mid, "frequency_hz": hz, "period_s": period_s, "payload": payload})
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
_state = {
    "send_enabled": os.environ.get("SEND_ENABLED", "1") not in ("0", "false", "False"),
    "pdu_ready": False,
    "pdu_iface": "",
    "last_error": None,
    "loop_alive": False,
    "mqtt_connected": False,
    "published": 0,
    "probe_rtt_ms": 0.0,
    "probe_owd_ms": 0.0,
    "last_delay_ms": 0.0,
    "probe_ok": 0,
    "probe_fail": 0,
}


def _discover_pdu_iface() -> Optional[str]:
    candidates = []
    for raw in (PDU_IFACE_CFG, "oaitun_ue4", "oaitun_ue1", "oaitun_ue2", "oaitun_ue3", "oaitun_ue5"):
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
    elapsed = 0
    while elapsed < PDU_WAIT_TIMEOUT:
        if _pin_pdu():
            with _lock:
                _state["pdu_ready"] = True
                _state["pdu_iface"] = _pdu_iface_live
            return
        time.sleep(2)
        elapsed += 2
    with _lock:
        _state["pdu_ready"] = False
        _state["last_error"] = f"PDU (prefer {PDU_IFACE_CFG}) not ready after {PDU_WAIT_TIMEOUT}s"


def _payload_body(msg: dict[str, Any], seq: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "seq": seq,
        "tier": msg.get("id") or "msg",
        "msg_id": msg.get("id") or "msg",
        "ue": UE_NAME,
        "app_name": UE_ID,
        "client_index": CLIENT_INDEX,
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
    """Stamp ``t_send`` (unix seconds) immediately before serialize/publish."""
    body = _payload_body(msg, seq)
    body["t_send"] = time.time()
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


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
    if ok:
        try:
            client.subscribe(DL_TOPIC, qos=MQTT_QOS)
            client.subscribe(LATENCY_TOPIC, qos=MQTT_QOS)
            client.subscribe(PROBE_ACK_TOPIC, qos=MQTT_QOS)
        except Exception:
            pass


def _mqtt_on_disconnect(_client, _userdata, _flags, reason_code, _properties=None):
    global _mqtt_connected
    _mqtt_connected = False
    with _lock:
        _state["mqtt_connected"] = False


def _mqtt_on_message(_client, _userdata, msg):
    recv = time.time()
    raw = msg.payload or b""
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    topic = str(msg.topic or "")
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
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    _mqtt_client = client
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
        "payload": msg.get("payload"),
        "error": err,
    }
    _record(entry)
    return entry


def _publish_loop(msg: dict[str, Any], stop: threading.Event) -> None:
    _, period = _freq_and_period(msg)
    stop.wait(min(period, 1.0))
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
        stop.wait(period)
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
    if not alive:
        return
    for msg in msgs:
        threading.Thread(
            target=_publish_loop,
            args=(msg, stop),
            name=f"pub-{msg.get('id')}",
            daemon=True,
        ).start()


def _loop() -> None:
    _wait_pdu()
    try:
        _ensure_mqtt()
    except Exception as exc:
        with _lock:
            _state["last_error"] = str(exc)
    with _lock:
        _state["loop_alive"] = True
    _restart_publishers()
    if LATENCY_PROBE_ENABLED:
        threading.Thread(
            target=_latency_probe_loop,
            args=(threading.Event(),),
            name="iot-latency-probe",
            daemon=True,
        ).start()
    while True:
        time.sleep(5)
        _pin_pdu()


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
        "message_count": len(msgs),
        "messages": msgs,
        "exchanges": n,
        "last": last,
        "stats": stats,
        **st,
        "mqtt_connected": _mqtt_connected,
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


@app.on_event("startup")
def _startup() -> None:
    if start_http_server is not None:
        try:
            start_http_server(METRICS_PORT, addr="0.0.0.0")
        except Exception:
            pass
    threading.Thread(target=_ping_loop, name="iot-ue-ping-loop", daemon=True).start()
    threading.Thread(target=_loop, name="iot-ue-loop", daemon=True).start()

