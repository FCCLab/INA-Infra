#!/usr/bin/env python3
"""In-pod control UI for OTT / IoT application servers (status, config, restart)."""
from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

NS_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
TOKEN_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
CA_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

APP_KIND = os.environ.get("APP_KIND", "ott").lower()
DEPLOY_NAME = os.environ.get("HF_DEPLOY_NAME") or os.environ.get("DEPLOY_NAME") or "application-ott"
TARGET_CONTAINER = os.environ.get("TARGET_CONTAINER", "application-backend")
METRICS_URL = os.environ.get("METRICS_URL", "http://127.0.0.1:9103/metrics")
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1884"))
MQTT_SUB_TOPIC = os.environ.get("MQTT_SUB_TOPIC", "slice_d/#")
MQTT_MSG_LIMIT = int(os.environ.get("MQTT_MSG_LIMIT", "120"))
MQTT_STAT_WINDOW_S = float(os.environ.get("MQTT_STAT_WINDOW_S", "30"))
N6_IP = os.environ.get("MULTUS_IP", "")
RTSP_PORT = os.environ.get("RTSP_PORT", "8554")
STREAM_PATH = os.environ.get("STREAM_PATH", "live/hd")
BROKER_PORT = os.environ.get("BROKER_PORT", "1883")
SLICE_ID = int(os.environ.get("SLICE_ID", "4" if APP_KIND == "iot" else "3"))
UE_CONSOLE_PORT = int(os.environ.get("UE_CONSOLE_PORT", "80"))
UE_SCAN_MAX = int(os.environ.get("UE_SCAN_MAX", "8"))
STATIC_DIR = Path(os.environ.get("DASHBOARD_STATIC", "/app/static")) / APP_KIND
CONFIG_KEYS = [
    k.strip()
    for k in os.environ.get(
        "CONFIG_KEYS",
        "STREAM_PATH,BITRATE_KBPS,FPS,WIDTH,HEIGHT" if APP_KIND == "ott" else "DL_FAST_PERIOD_S,DL_SLOW_PERIOD_S,DL_PAYLOAD_BYTES,MQTT_QOS",
    ).split(",")
    if k.strip()
]

_mqtt_lock = threading.Lock()
_mqtt_msgs: deque[dict[str, Any]] = deque(maxlen=max(20, MQTT_MSG_LIMIT))
_mqtt_sub = {
    "connected": False,
    "subscribed": False,
    "topic": MQTT_SUB_TOPIC,
    "error": None,
    "count": 0,
}
_mqtt_topics: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _mqtt_direction(topic: str) -> str:
    if "/ul/" in topic:
        return "uplink"
    if "/dl/" in topic:
        return "downlink"
    return "other"


def _ue_id_from_topic(topic: str) -> str:
    parts = [p for p in (topic or "").split("/") if p]
    return parts[-1] if parts else ""


def _idx_from_ue_id(ue: str) -> Optional[int]:
    m = re.search(r"(\d+)$", ue or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _freq_hz(times: list[float]) -> float:
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


def _note_mqtt_topic(topic: str, nbytes: int) -> None:
    if not topic:
        return
    now = time.monotonic()
    st = _mqtt_topics.setdefault(
        topic,
        {"count": 0, "bytes": 0, "last_ts": None, "times": deque(maxlen=500)},
    )
    st["count"] = int(st["count"] or 0) + 1
    st["bytes"] = int(st["bytes"] or 0) + int(nbytes or 0)
    st["last_ts"] = _now_iso()
    times = st["times"]
    times.append(now)
    while times and now - times[0] > MQTT_STAT_WINDOW_S:
        times.popleft()


def _topic_row(topic: str, st: dict[str, Any], now: float) -> dict[str, Any]:
    times = [t for t in (st.get("times") or []) if now - t <= MQTT_STAT_WINDOW_S]
    ue = _ue_id_from_topic(topic)
    return {
        "topic": topic,
        "ue": ue,
        "client_index": _idx_from_ue_id(ue),
        "direction": _mqtt_direction(topic),
        "count": int(st.get("count") or 0),
        "bytes": int(st.get("bytes") or 0),
        "last_ts": st.get("last_ts"),
        "window_s": MQTT_STAT_WINDOW_S,
        "window_count": len(times),
        "freq_hz": _freq_hz(times),
        "avg_freq_hz": _avg_freq_hz(times, now, MQTT_STAT_WINDOW_S),
        "_times": times,
    }


def _mqtt_stats_snapshot() -> dict[str, Any]:
    now = time.monotonic()
    topics = [_topic_row(topic, st, now) for topic, st in _mqtt_topics.items()]
    topics.sort(key=lambda r: r["topic"])
    by_ue: dict[Any, dict[str, Any]] = {}
    for row in topics:
        key = row["client_index"] if row["client_index"] is not None else (row["ue"] or "unknown")
        bucket = by_ue.setdefault(
            key,
            {
                "ue": row["ue"],
                "client_index": row["client_index"],
                "topic_count": 0,
                "topics": [],
                "rx_count": 0,
                "_times": [],
                "_ul_times": [],
            },
        )
        bucket["topics"].append(row)
        bucket["topic_count"] = len(bucket["topics"])
        bucket["rx_count"] = int(bucket["rx_count"] or 0) + int(row["count"] or 0)
        times = list(row.get("_times") or [])
        bucket["_times"].extend(times)
        if row.get("direction") == "uplink":
            bucket["_ul_times"].extend(times)
    ues = []
    for bucket in by_ue.values():
        all_times = bucket.pop("_times", [])
        ul_times = bucket.pop("_ul_times", [])
        for row in bucket["topics"]:
            row.pop("_times", None)
        bucket["avg_freq_hz"] = _avg_freq_hz(all_times, now, MQTT_STAT_WINDOW_S)
        bucket["ul_avg_freq_hz"] = _avg_freq_hz(ul_times, now, MQTT_STAT_WINDOW_S)
        ues.append(bucket)
    ues.sort(
        key=lambda u: (u.get("client_index") is None, u.get("client_index") or 0, u.get("ue") or ""),
    )
    return {
        "window_s": MQTT_STAT_WINDOW_S,
        "topic_count": len(topics),
        "topics": topics,
        "ues": ues,
        "avg_freq_hz": round(sum(float(u.get("avg_freq_hz") or 0) for u in ues), 4),
    }


def _stats_for_ue(idx: int) -> dict[str, Any]:
    snap = _mqtt_stats_snapshot()
    for u in snap["ues"]:
        if u.get("client_index") == idx or u.get("ue") == f"ue{idx}":
            return u
    return {
        "ue": f"ue{idx}",
        "client_index": idx,
        "topic_count": 0,
        "topics": [],
        "rx_count": 0,
        "avg_freq_hz": 0.0,
        "ul_avg_freq_hz": 0.0,
    }


def _mqtt_sub_loop() -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        with _mqtt_lock:
            _mqtt_sub["error"] = "paho-mqtt not installed"
        return

    def on_connect(client, _userdata, _flags, reason_code, _properties=None):
        ok = int(getattr(reason_code, "value", reason_code) or 0) == 0
        with _mqtt_lock:
            _mqtt_sub["connected"] = ok
            _mqtt_sub["error"] = None if ok else f"connect failed: {reason_code}"
        if ok:
            client.subscribe(MQTT_SUB_TOPIC, qos=0)
            with _mqtt_lock:
                _mqtt_sub["subscribed"] = True

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties=None):
        with _mqtt_lock:
            _mqtt_sub["connected"] = False
            _mqtt_sub["subscribed"] = False
            _mqtt_sub["error"] = f"disconnected: {reason_code}"

    def on_message(_client, _userdata, msg):
        raw = msg.payload or b""
        try:
            parsed: Any = json.loads(raw)
        except Exception:
            parsed = raw.decode("utf-8", errors="replace")[:800]
        entry = {
            "ts": _now_iso(),
            "topic": msg.topic,
            "direction": _mqtt_direction(msg.topic),
            "bytes": len(raw),
            "payload": parsed,
        }
        with _mqtt_lock:
            _mqtt_msgs.appendleft(entry)
            _note_mqtt_topic(msg.topic, len(raw))
            _mqtt_sub["count"] = int(_mqtt_sub.get("count") or 0) + 1
            _mqtt_sub["connected"] = True
            _mqtt_sub["subscribed"] = True
            _mqtt_sub["error"] = None

    cid = f"dash-sub-{os.getpid()}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    except AttributeError:
        client = mqtt.Client(client_id=cid)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    while True:
        try:
            client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
            client.loop_forever()
        except Exception as exc:
            with _mqtt_lock:
                _mqtt_sub["connected"] = False
                _mqtt_sub["subscribed"] = False
                _mqtt_sub["error"] = str(exc)
            time.sleep(2)


app = FastAPI(title=f"{APP_KIND} control console", docs_url="/api/docs")


class ConfigIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class MqttIn(BaseModel):
    topic: str = Field(..., min_length=1)
    payload: str = Field("")
    qos: int = Field(0, ge=0, le=2)


class UeControlIn(BaseModel):
    send_enabled: Optional[bool] = None


class UeMessageIn(BaseModel):
    id: Optional[str] = None
    period_s: Optional[float] = None
    frequency_hz: Optional[float] = None
    payload: Optional[Any] = None


class UeConfigIn(BaseModel):
    messages: list[UeMessageIn] = Field(default_factory=list)


def _ns() -> str:
    return os.environ.get("POD_NAMESPACE") or (NS_FILE.read_text().strip() if NS_FILE.is_file() else "ina-infra")


def _k8s_ctx() -> tuple[str, ssl.SSLContext, dict[str, str]]:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host or not TOKEN_FILE.is_file():
        raise HTTPException(status_code=503, detail="in-cluster Kubernetes credentials not available")
    token = TOKEN_FILE.read_text().strip()
    ctx = ssl.create_default_context()
    if CA_FILE.is_file():
        ctx.load_verify_locations(str(CA_FILE))
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return f"https://{host}:{port}", ctx, headers


def _k8s(method: str, path: str, body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> tuple[int, Any]:
    base, ctx, headers = _k8s_ctx()
    hdrs = dict(headers)
    if extra_headers:
        hdrs.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:400]}
        return exc.code, parsed


def _parse_metrics(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " " not in line:
            continue
        name, val = line.rsplit(" ", 1)
        try:
            out[name] = float(val)
        except ValueError:
            continue
    return out


def _metrics() -> dict[str, float]:
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=3) as resp:
            return _parse_metrics(resp.read().decode(errors="replace"))
    except Exception:
        return {}


def _restart_deploy(ns: str) -> bool:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"ina.lab/restartedAt": stamp},
                }
            }
        }
    }
    code, _ = _k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{ns}/deployments/{DEPLOY_NAME}",
        patch,
        extra_headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    return code in (200, 201)


def _deploy_env(ns: str) -> dict[str, str]:
    code, body = _k8s("GET", f"/apis/apps/v1/namespaces/{ns}/deployments/{DEPLOY_NAME}")
    if code != 200:
        return {}
    containers = (((body or {}).get("spec") or {}).get("template") or {}).get("spec", {}).get("containers") or []
    for c in containers:
        if c.get("name") == TARGET_CONTAINER:
            out = {}
            for item in c.get("env") or []:
                if "name" in item and "value" in item:
                    out[item["name"]] = str(item["value"])
            return out
    return {}


@app.get("/api/status")
def api_status() -> dict:
    metrics = _metrics()
    live = _deploy_env(_ns())
    cfg = {k: live.get(k) or os.environ.get(k, "") for k in CONFIG_KEYS}
    stream_path = cfg.get("STREAM_PATH") or STREAM_PATH
    rtsp = f"rtsp://{N6_IP or '0.0.0.0'}:{RTSP_PORT}/{stream_path}" if APP_KIND == "ott" else None
    mqtt = f"mqtt://{N6_IP or '0.0.0.0'}:{BROKER_PORT}" if APP_KIND == "iot" else None
    with _mqtt_lock:
        sub = dict(_mqtt_sub)
        recent = int(sub.get("count") or 0)
        stats = _mqtt_stats_snapshot()
    return {
        "ok": True,
        "kind": APP_KIND,
        "namespace": _ns(),
        "deploy": DEPLOY_NAME,
        "metrics_live": bool(metrics),
        "metrics": metrics,
        "n6_ip": N6_IP,
        "rtsp": rtsp,
        "mqtt": mqtt,
        "mqtt_sub": sub,
        "mqtt_count": recent,
        "mqtt_stats": {"topic_count": stats["topic_count"], "window_s": stats["window_s"]},
        "config": cfg,
    }


@app.post("/api/config")
def api_config(body: ConfigIn) -> dict:
    values = {k: str(v).strip() for k, v in (body.values or {}).items() if k in CONFIG_KEYS and str(v).strip()}
    if not values:
        raise HTTPException(status_code=400, detail="no supported config keys")
    ns = _ns()
    env_patch = [{"name": k, "value": v} for k, v in values.items()]
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": TARGET_CONTAINER, "env": env_patch},
                    ]
                }
            }
        }
    }
    code, resp = _k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{ns}/deployments/{DEPLOY_NAME}",
        patch,
        extra_headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    if code not in (200, 201):
        raise HTTPException(status_code=400, detail=(resp or {}).get("message", f"patch failed ({code})"))
    return {"ok": True, "applied": values, "message": "Config patched; pod will roll."}


@app.post("/api/restart")
def api_restart() -> dict:
    ok = _restart_deploy(_ns())
    if not ok:
        raise HTTPException(status_code=400, detail="restart patch failed")
    return {"ok": True, "message": "Restart requested."}


@app.post("/api/mqtt/publish")
def api_mqtt(body: MqttIn) -> dict:
    if APP_KIND != "iot":
        raise HTTPException(status_code=404, detail="MQTT publish is IoT-only")
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="paho-mqtt not installed") from exc
    cid = f"dash-{os.getpid()}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    except AttributeError:
        client = mqtt.Client(client_id=cid)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 5)
        info = client.publish(body.topic, body.payload.encode(), qos=body.qos)
        client.loop(timeout=2.0)
        client.disconnect()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MQTT publish failed: {exc}") from exc
    return {"ok": True, "topic": body.topic, "rc": getattr(info, "rc", None)}


@app.get("/api/messages")
def api_messages(limit: int = 80) -> dict:
    if APP_KIND != "iot":
        raise HTTPException(status_code=404, detail="MQTT subscribe is IoT-only")
    lim = max(1, min(int(limit), MQTT_MSG_LIMIT))
    with _mqtt_lock:
        items = list(_mqtt_msgs)[:lim]
        sub = dict(_mqtt_sub)
    return {"ok": True, "topic": MQTT_SUB_TOPIC, "subscriber": sub, "items": items}


@app.get("/api/stats")
def api_stats() -> dict:
    if APP_KIND != "iot":
        raise HTTPException(status_code=404, detail="MQTT stats are IoT-only")
    with _mqtt_lock:
        snap = _mqtt_stats_snapshot()
        sub = dict(_mqtt_sub)
    return {"ok": True, "subscriber": sub, **snap}


def _require_iot() -> None:
    if APP_KIND != "iot":
        raise HTTPException(status_code=404, detail="UE console APIs are IoT-only")


def _http_url(host: str, port: int = UE_CONSOLE_PORT) -> str:
    if int(port) == 80:
        return f"http://{host}/"
    return f"http://{host}:{port}/"


def _console_ip(idx: int) -> str:
    # Keep in sync with site_ips.ue_console_ip (docs/ip_plan.md).
    octet = 220 + (int(SLICE_ID) - 1) * 10 + max(int(idx), 1) - 1
    if octet >= 255:
        octet = 200 + (octet - 255)
    return f"10.1.137.{octet}"


def _console_mac(idx: int) -> str:
    return f"02:0a:40:{int(SLICE_ID):02x}:00:{max(int(idx), 1):02x}"


def _ue_idx_from_name(name: str) -> Optional[int]:
    prefix = f"oai-ue-slice-{SLICE_ID}-client-"
    if not name.startswith(prefix):
        return None
    try:
        return int(name[len(prefix) :])
    except ValueError:
        return None


def _ue_http(
    host: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: int = 5,
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    path = path if path.startswith("/") else f"/{path}"
    url = f"http://{host}{path}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:400]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:400]}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 503, {"detail": str(exc.reason)}


def _ue_upstreams(idx: int) -> list[str]:
    name = f"oai-ue-slice-{SLICE_ID}-client-{idx}"
    hosts = [
        f"{_console_ip(idx)}:{UE_CONSOLE_PORT}",
        f"{name}:{UE_CONSOLE_PORT}",
        f"{name}.{_ns()}.svc:{UE_CONSOLE_PORT}",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _ue_http_idx(
    idx: int,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: int = 8,
) -> tuple[int, Any]:
    last: tuple[int, Any] = (503, {"detail": "unreachable"})
    for host in _ue_upstreams(idx):
        code, resp = _ue_http(host, path, method=method, body=body, timeout=timeout)
        if code != 503:
            return code, resp
        last = (code, resp)
    return last


def _probe_ue(idx: int) -> Optional[dict]:
    ip = _console_ip(idx)
    mac = _console_mac(idx)
    sc, live = _ue_http_idx(idx, "/api/status", timeout=2)
    connected = sc == 200
    if not connected and sc == 503:
        return None
    if sc != 200:
        live = {"ok": False, "http_status": sc, **(live if isinstance(live, dict) else {})}
    return {
        "name": f"oai-ue-slice-{SLICE_ID}-client-{idx}",
        "client_index": idx,
        "ready": 1 if connected else 0,
        "desired": 1,
        "pod_phase": "Running" if connected else "Unknown",
        "console_ip": ip,
        "console_mac": mac,
        "console_url": _http_url(ip),
        "dashboard_url": f"/ue/{idx}/",
        "connected": connected,
        "status": live if isinstance(live, dict) else {},
    }


@app.get("/api/ues")
def api_ues() -> dict:
    _require_iot()
    found: dict[int, dict] = {}
    try:
        code, body = _k8s("GET", f"/apis/apps/v1/namespaces/{_ns()}/deployments")
        if code == 200:
            for dep in (body or {}).get("items") or []:
                name = ((dep.get("metadata") or {}).get("name")) or ""
                idx = _ue_idx_from_name(name)
                if idx is None:
                    continue
                sc, live = _ue_http_idx(idx, "/api/status", timeout=2)
                live_d = live if isinstance(live, dict) else {}
                if sc != 200:
                    live = {"ok": False, "http_status": sc, **live_d}
                ip = str(live_d.get("console_ip") or labels.get("ina.lab/console-ip") or _console_ip(idx))
                raw_mac = labels.get("ina.lab/console-mac") or ""
                mac = raw_mac.replace("-", ":") if raw_mac else _console_mac(idx)
                st = dep.get("status") or {}
                ready = int(st.get("readyReplicas") or 0)
                c_url = str(live_d.get("console_url") or (_http_url(ip) if ip else ""))
                found[idx] = {
                    "name": name,
                    "client_index": idx,
                    "ready": ready,
                    "desired": int(st.get("replicas") or 1),
                    "pod_phase": "Running" if ready else "Pending",
                    "console_ip": ip,
                    "console_mac": mac,
                    "console_url": c_url,
                    "dashboard_url": f"/ue/{idx}/",
                    "connected": ready >= 1 or sc == 200,
                    "status": live if isinstance(live, dict) else {},
                }
    except HTTPException:
        pass
    except Exception:
        pass
    try:
        with ThreadPoolExecutor(max_workers=max(1, UE_SCAN_MAX)) as pool:
            futs = [
                pool.submit(_probe_ue, i)
                for i in range(1, max(1, UE_SCAN_MAX) + 1)
                if i not in found or not found[i].get("connected")
            ]
            for fut in as_completed(futs):
                probed = fut.result()
                if probed:
                    found[int(probed["client_index"])] = probed
    except Exception:
        pass
    with _mqtt_lock:
        snap = _mqtt_stats_snapshot()
    by_idx = {u.get("client_index"): u for u in snap["ues"] if u.get("client_index") is not None}
    for idx, u_stat in by_idx.items():
        if idx not in found:
            ip = _console_ip(idx)
            mac = _console_mac(idx)
            found[idx] = {
                "name": f"oai-ue-slice-{SLICE_ID}-client-{idx}",
                "client_index": idx,
                "ready": 1,
                "desired": 1,
                "pod_phase": "Running",
                "console_ip": ip,
                "console_mac": mac,
                "console_url": _http_url(ip),
                "dashboard_url": f"/ue/{idx}/",
                "connected": True,
                "status": {"ok": True, "pdu_ready": True, "mqtt_connected": True},
            }
    ues = [found[k] for k in sorted(found)]
    for ue in ues:
        idx = int(ue.get("client_index") or 0)
        ue["stats"] = by_idx.get(idx) or {
            "ue": f"ue{idx}",
            "client_index": idx,
            "topic_count": 0,
            "topics": [],
            "rx_count": 0,
            "avg_freq_hz": 0.0,
            "ul_avg_freq_hz": 0.0,
        }
    return {"ok": True, "slice_id": SLICE_ID, "ues": ues, "topic_count": snap["topic_count"]}


@app.get("/api/ues/{idx}/status")
def api_ue_status(idx: int) -> dict:
    _require_iot()
    code, body = _ue_http_idx(idx, "/api/status", timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=body)
    return body


@app.get("/api/ues/{idx}/stats")
def api_ue_stats(idx: int) -> dict:
    _require_iot()
    with _mqtt_lock:
        broker = _stats_for_ue(idx)
    code, live = _ue_http_idx(idx, "/api/stats", timeout=4)
    ue_live = live if code == 200 and isinstance(live, dict) else {}
    return {"ok": True, "client_index": idx, "broker": broker, "ue": ue_live}


@app.get("/api/ues/{idx}/exchanges")
def api_ue_exchanges(idx: int, limit: int = 40) -> dict:
    _require_iot()
    code, body = _ue_http_idx(idx, f"/api/exchanges?limit={int(limit)}", timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=body)
    return body


@app.post("/api/ues/{idx}/control")
def api_ue_control(idx: int, body: UeControlIn) -> dict:
    _require_iot()
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    code, resp = _ue_http_idx(idx, "/api/control", method="POST", body=payload, timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=resp)
    return resp


@app.post("/api/ues/{idx}/config")
def api_ue_config(idx: int, body: UeConfigIn) -> dict:
    _require_iot()
    payload = {"messages": [m.model_dump(exclude_none=True) for m in (body.messages or [])]}
    code, resp = _ue_http_idx(idx, "/api/config", method="POST", body=payload, timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=resp)
    return resp


@app.get("/ue/{idx}")
def ue_dash_redir(idx: int) -> RedirectResponse:
    _require_iot()
    return RedirectResponse(url=f"/ue/{idx}/", status_code=307)


@app.api_route("/ue/{idx}/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def ue_dash_root(idx: int, request: Request) -> Response:
    return await ue_dash_proxy(idx, "", request)


@app.api_route("/ue/{idx}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def ue_dash_proxy(idx: int, path: str, request: Request) -> Response:
    _require_iot()
    suffix = path if path else ""
    q = request.url.query
    data = None
    if request.method not in ("GET", "HEAD"):
        data = await request.body()
    headers = {}
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    last_err = "unreachable"
    last_host = ""
    raw = b""
    media = "application/octet-stream"
    status = 503
    for host in _ue_upstreams(idx):
        last_host = host
        target = f"http://{host}/{suffix}"
        if q:
            target = f"{target}?{q}"
        req = urllib.request.Request(target, data=data, headers=headers, method=request.method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                media = resp.headers.get("Content-Type", "application/octet-stream")
                status = resp.status
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            media = exc.headers.get("Content-Type", "application/octet-stream") if exc.headers else "application/octet-stream"
            status = exc.code
            break
        except urllib.error.URLError as exc:
            last_err = str(exc.reason)
            continue
    else:
        raise HTTPException(status_code=503, detail=f"UE {idx} dashboard unreachable ({last_host}): {last_err}")
    if "text/html" in (media or "") and raw:
        html = raw.decode("utf-8", errors="replace")
        html = html.replace('fetch("/api', f'fetch("/ue/{idx}/api')
        html = html.replace('req("/api', f'req("/ue/{idx}/api')
        html = html.replace("req(`/api", f"req(`/ue/{idx}/api")
        raw = html.encode("utf-8")
        media = "text/html; charset=utf-8"
    return Response(content=raw, status_code=status, media_type=media)


@app.on_event("startup")
def _startup() -> None:
    if APP_KIND == "iot":
        threading.Thread(target=_mqtt_sub_loop, name="mqtt-sub", daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail=f"console UI missing for {APP_KIND}")
    return FileResponse(page)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
