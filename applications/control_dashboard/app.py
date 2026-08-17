#!/usr/bin/env python3
"""In-pod control UI for OTT / IoT application servers (status, config, restart)."""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

NS_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
TOKEN_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
CA_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

APP_KIND = os.environ.get("APP_KIND", "ott").lower()
DEPLOY_NAME = os.environ.get("HF_DEPLOY_NAME") or os.environ.get("DEPLOY_NAME") or "application-ott"
TARGET_CONTAINER = os.environ.get("TARGET_CONTAINER", "ott-server")
METRICS_URL = os.environ.get("METRICS_URL", "http://127.0.0.1:9103/metrics")
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1884"))
N6_IP = os.environ.get("MULTUS_IP", "")
RTSP_PORT = os.environ.get("RTSP_PORT", "8554")
STREAM_PATH = os.environ.get("STREAM_PATH", "live/hd")
BROKER_PORT = os.environ.get("BROKER_PORT", "1883")
STATIC_DIR = Path(os.environ.get("DASHBOARD_STATIC", "/app/static")) / APP_KIND
CONFIG_KEYS = [
    k.strip()
    for k in os.environ.get(
        "CONFIG_KEYS",
        "STREAM_PATH,BITRATE_KBPS,FPS,WIDTH,HEIGHT" if APP_KIND == "ott" else "DL_FAST_PERIOD_S,DL_SLOW_PERIOD_S,DL_PAYLOAD_BYTES,MQTT_QOS",
    ).split(",")
    if k.strip()
]

app = FastAPI(title=f"{APP_KIND} control dashboard", docs_url="/api/docs")


class ConfigIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class MqttIn(BaseModel):
    topic: str = Field(..., min_length=1)
    payload: str = Field("")
    qos: int = Field(0, ge=0, le=2)


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


@app.get("/")
def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail=f"dashboard UI missing for {APP_KIND}")
    return FileResponse(page)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
