#!/usr/bin/env python3
"""Exp4 S5 server backend control API (broker + DL publish)."""

from __future__ import annotations

import os
import socket
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from connected_clients import attach as attach_clients
from connected_clients import snapshot as snapshot_clients

app = FastAPI(title="Exp4 S5 backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
attach_clients(app)

MQTT_HOST = os.environ.get("LOCAL_BROKER_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("LOCAL_BROKER_PORT", "1884"))
OTA_PORT = int(os.environ.get("OTA_BROKER_PORT", "1883"))


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


class PublishIn(BaseModel):
    topic: str = Field(..., min_length=1)
    payload: str = ""
    qos: int = Field(0, ge=0, le=2)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/e2e")
def e2e() -> dict:
    """t_send is stamped before the broker listen check."""
    t_send = time.time()
    broker_ok = _listening(MQTT_HOST, MQTT_PORT) or _listening("127.0.0.1", OTA_PORT)
    return {"ok": True, "t_send": t_send, "broker_ok": broker_ok}


@app.get("/api/status")
def status() -> dict:
    broker_ok = _listening(MQTT_HOST, MQTT_PORT) or _listening("127.0.0.1", OTA_PORT)
    clients = snapshot_clients()
    return {
        "ok": True,
        "app": "exp4-s5",
        "role": "server-backend",
        "broker_ok": broker_ok,
        "local_broker": f"{MQTT_HOST}:{MQTT_PORT}",
        "ota_port": OTA_PORT,
        "n6_ip": os.environ.get("MULTUS_IP") or "",
        "dl_fast_period_s": float(os.environ.get("DL_FAST_PERIOD_S", "0.05")),
        "dl_slow_period_s": float(os.environ.get("DL_SLOW_PERIOD_S", "1.0")),
        "device_count": len(clients),
        "clients": clients,
        "clients_count": len(clients),
        "topic_prefix": os.environ.get("MQTT_TOPIC_PREFIX", "slice_5"),
    }


@app.post("/api/publish")
def publish(body: PublishIn) -> dict:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="paho-mqtt missing") from exc
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="exp4-s5-console")
    except AttributeError:
        client = mqtt.Client(client_id="exp4-s5-console")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 5)
        info = client.publish(body.topic, body.payload.encode(), qos=body.qos)
        client.loop(timeout=2.0)
        client.disconnect()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "topic": body.topic, "rc": getattr(info, "rc", None)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BACKEND_PORT", "8080")))
