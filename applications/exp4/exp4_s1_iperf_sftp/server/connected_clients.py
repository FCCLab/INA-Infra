"""Thread-safe connected-client registry for Exp4 servers.

UEs POST /api/v1/clients/heartbeat; the server console lists whoever is still alive.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

TTL_S = float(os.environ.get("EXP4_CLIENT_TTL_S", "15"))
UNSTABLE_S = float(os.environ.get("EXP4_CLIENT_UNSTABLE_S", "8"))
PRUNE_S = float(os.environ.get("EXP4_CLIENT_PRUNE_S", "60"))

_LOCK = threading.Lock()
_CLIENTS: dict[str, dict[str, Any]] = {}


class HeartbeatIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    name: str = ""
    console_ip: str = ""
    console_url: str = ""
    to_server_ip: str = ""
    to_server_iface: str = ""
    detail: str = ""


def touch(
    *,
    client_id: str,
    name: str = "",
    console_ip: str = "",
    console_url: str = "",
    to_server_ip: str = "",
    to_server_iface: str = "",
    detail: str = "",
) -> dict[str, Any]:
    cid = str(client_id or "").strip()
    if not cid:
        raise ValueError("client_id required")
    now = time.time()
    url = str(console_url or "")
    if not url and console_ip:
        url = f"http://{console_ip}"
    with _LOCK:
        prev = _CLIENTS.get(cid) or {}
        row = {
            "id": cid,
            "name": str(name or prev.get("name") or cid),
            "console_ip": str(console_ip or prev.get("console_ip") or ""),
            "console_url": url or str(prev.get("console_url") or ""),
            "to_server_ip": str(to_server_ip or prev.get("to_server_ip") or ""),
            "to_server_iface": str(to_server_iface or prev.get("to_server_iface") or ""),
            "detail": str(detail or ""),
            "connected_at": prev.get("connected_at") or now,
            "last_heartbeat": now,
        }
        _CLIENTS[cid] = row
        return dict(row)


def snapshot(*, ttl_s: Optional[float] = None, prune_s: Optional[float] = None) -> list[dict[str, Any]]:
    ttl = float(TTL_S if ttl_s is None else ttl_s)
    prune = float(PRUNE_S if prune_s is None else prune_s)
    now = time.time()
    out: list[dict[str, Any]] = []
    with _LOCK:
        dead = [
            k
            for k, v in _CLIENTS.items()
            if now - float(v.get("last_heartbeat") or 0) > prune
        ]
        for k in dead:
            del _CLIENTS[k]
        for row in _CLIENTS.values():
            ago = now - float(row.get("last_heartbeat") or 0)
            item = dict(row)
            item["last_heartbeat_ago"] = round(ago, 1)
            if ago <= UNSTABLE_S:
                item["connection_status"] = "Connected"
                item["active"] = True
                item["is_alive"] = True
            elif ago <= ttl:
                item["connection_status"] = "Unstable"
                item["active"] = True
                item["is_alive"] = True
            else:
                item["connection_status"] = "Disconnected"
                item["active"] = False
                item["is_alive"] = False
            out.append(item)
    out.sort(key=lambda r: (str(r.get("name") or ""), str(r.get("id") or "")))
    return out


def attach(app: FastAPI) -> None:
    @app.post("/api/v1/clients/heartbeat")
    @app.post("/api/clients/heartbeat")
    def _heartbeat(body: HeartbeatIn) -> dict[str, Any]:
        row = touch(
            client_id=body.client_id,
            name=body.name,
            console_ip=body.console_ip,
            console_url=body.console_url,
            to_server_ip=body.to_server_ip,
            to_server_iface=body.to_server_iface,
            detail=body.detail,
        )
        return {"ok": True, "client_id": row["id"], "console_url": row["console_url"]}

    @app.get("/api/v1/clients")
    @app.get("/api/clients")
    def _list_clients() -> dict[str, Any]:
        clients = snapshot()
        return {"ok": True, "count": len(clients), "clients": clients}
