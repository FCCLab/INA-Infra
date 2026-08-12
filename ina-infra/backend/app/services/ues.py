"""In-memory registry for UE iperf3-client WebSocket agents.

Clients connect to ``/api/v1/ues/ws``, declare identity + status, and receive
per-UE pushed ``desired`` (protocol / config / start / stop). Benchmark UI
lists agents and sets desired on a selected UE id.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import (
    UeApplyReport,
    UeDeclare,
    UeDesiredOut,
    UeDesiredRequest,
    UeListOut,
    UeOut,
)

STALE_AFTER_SEC = 30

_lock = threading.RLock()
_ues: Dict[str, dict] = {}
_ws: Dict[str, Tuple[asyncio.AbstractEventLoop, "asyncio.Queue[dict]"]] = {}
# Survives prune/offline so reconnect does not wipe UI-applied desired.
_desired_cache: Dict[str, dict] = {}

_DEFAULT_DESIRED: Dict[str, Any] = {
    "protocol": "udp",
    "action": "start",
    "bandwidth": "50M",
    "parallel": 5,
    "tcp_bandwidth": "",
    "server": "",
    "reverse": True,
    "duration": 0,
    "interval": 1.0,
    "generation": 0,
    "updated_at": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_online(cur: dict, now: Optional[datetime] = None) -> bool:
    if cur.get("ws_connected"):
        return True
    t = _parse_ts(cur.get("last_seen") or "")
    if t is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - t).total_seconds() <= STALE_AFTER_SEC


def _prune_stale_locked(now: Optional[datetime] = None) -> None:
    """Drop offline agents from the live map, but keep their desired config.

    Reconnect restores that desired instead of reseeding DL defaults.
    """
    now = now or datetime.now(timezone.utc)
    for uid in [uid for uid, cur in _ues.items() if not _is_online(cur, now)]:
        cur = _ues.get(uid) or {}
        desired = cur.get("desired")
        if isinstance(desired, dict) and int(desired.get("generation") or 0) > 0:
            _desired_cache[uid] = dict(desired)
        _ws.pop(uid, None)
        _ues.pop(uid, None)


def normalize_protocol(raw: str) -> str:
    p = (raw or "udp").strip().lower()
    if p not in ("udp", "tcp"):
        raise ValueError(f"protocol must be udp or tcp, got: {raw!r}")
    return p


def normalize_action(raw: str) -> str:
    a = (raw or "set").strip().lower()
    if a not in ("start", "stop", "set"):
        raise ValueError(f"action must be start|stop|set, got: {raw!r}")
    return a


def _desired_dict(raw: Optional[dict] = None) -> Dict[str, Any]:
    out = dict(_DEFAULT_DESIRED)
    if raw:
        out.update(raw)
    # Port is client-local; never keep it in desired.
    out.pop("port", None)
    return out


def _desired_out(raw: Optional[dict]) -> UeDesiredOut:
    d = _desired_dict(raw)
    return UeDesiredOut(
        protocol=str(d.get("protocol") or "udp"),
        action=str(d.get("action") or "start"),
        bandwidth=str(d.get("bandwidth") or "50M"),
        parallel=int(d.get("parallel") or 5),
        tcp_bandwidth=str(d.get("tcp_bandwidth") or ""),
        server=str(d.get("server") or ""),
        reverse=bool(d.get("reverse", True)),
        duration=int(d.get("duration") or 0),
        interval=float(d.get("interval") or 1.0),
        generation=int(d.get("generation") or 0),
        updated_at=str(d.get("updated_at") or ""),
    )


def attach_ws(
    ue_id: str, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[dict]"
) -> None:
    with _lock:
        _ws[ue_id] = (loop, queue)
        cur = _ues.get(ue_id)
        if cur is None:
            cur = {"id": ue_id, "desired": _desired_dict()}
            _ues[ue_id] = cur
        cur["ws_connected"] = True
        cur["last_seen"] = _now()
        cur["message"] = "websocket connected"


def detach_ws(ue_id: str, queue: "asyncio.Queue[dict]") -> None:
    with _lock:
        entry = _ws.get(ue_id)
        if entry and entry[1] is queue:
            del _ws[ue_id]
            cur = _ues.get(ue_id)
            if cur is not None:
                cur["ws_connected"] = False
                cur["message"] = "websocket disconnected"
                cur["last_seen"] = _now()


def push_desired(ue_id: str) -> None:
    with _lock:
        cur = _ues.get(ue_id)
        if not cur:
            return
        desired = _desired_dict(cur.get("desired"))
        entry = _ws.get(ue_id)
    if not entry:
        return
    payload = {"type": "desired", **_desired_out(desired).model_dump(mode="json")}
    loop, queue = entry
    try:
        loop.call_soon_threadsafe(queue.put_nowait, payload)
    except RuntimeError:
        pass


def _seed_desired_locked(cur: dict, body: UeDeclare) -> None:
    """Keep UI-set desired across reconnect; otherwise seed from live declare.

    Never invent DL defaults over a live UL/TCP session — use the agent's
    reported protocol / reverse / bandwidth / parallel when seeding.
    """
    prev = cur.get("desired")
    if isinstance(prev, dict) and int(prev.get("generation") or 0) > 0:
        return
    cached = _desired_cache.get(cur["id"])
    if isinstance(cached, dict) and int(cached.get("generation") or 0) > 0:
        cur["desired"] = _desired_dict(cached)
        return
    action = "start"
    if body.status and str(body.status).strip().lower() in ("idle", "stopped"):
        action = "stop"
    seed: Dict[str, Any] = {
        "protocol": normalize_protocol(body.protocol or "udp"),
        "action": action,
        "server": body.server or "",
        "generation": 0,
        "updated_at": _now(),
    }
    if body.bandwidth is not None and str(body.bandwidth).strip() != "":
        seed["bandwidth"] = str(body.bandwidth).strip()
    if body.parallel is not None and int(body.parallel) >= 1:
        seed["parallel"] = int(body.parallel)
    if body.tcp_bandwidth is not None:
        seed["tcp_bandwidth"] = str(body.tcp_bandwidth)
    if body.reverse is not None:
        seed["reverse"] = bool(body.reverse)
    cur["desired"] = _desired_dict(seed)


def register(body: UeDeclare) -> UeOut:
    with _lock:
        cur = _ues.get(body.id) or {"id": body.id}
        # Prefer cached UI desired over a gen-0 seed left from a prior session.
        cached = _desired_cache.get(body.id)
        prev_gen = int((cur.get("desired") or {}).get("generation") or 0)
        if (
            isinstance(cached, dict)
            and int(cached.get("generation") or 0) > 0
            and prev_gen <= 0
        ):
            cur["desired"] = dict(cached)
        cur.update(
            {
                "id": body.id,
                "cluster": body.cluster,
                "namespace": body.namespace,
                "pod": body.pod,
                "ue_name": body.ue_name,
                "version": body.version,
                "protocol": normalize_protocol(body.protocol)
                if body.protocol
                else cur.get("protocol") or "udp",
                "status": body.status or cur.get("status") or "idle",
                "server": body.server or cur.get("server") or "",
                "port": int(body.port or cur.get("port") or 0),
                "mbits_per_second": float(
                    body.mbits_per_second
                    if body.mbits_per_second is not None
                    else cur.get("mbits_per_second") or 0.0
                ),
                "message": body.message or cur.get("message") or "",
                "last_seen": _now(),
            }
        )
        _seed_desired_locked(cur, body)
        # Refresh cache whenever we have a real desired.
        des = cur.get("desired")
        if isinstance(des, dict) and int(des.get("generation") or 0) > 0:
            _desired_cache[body.id] = dict(des)
        _ues[body.id] = cur
        out = _to_out(cur)
    # Always re-push desired on declare/reconnect so the agent re-syncs.
    push_desired(body.id)
    return out


def report_status(ue_id: str, data: dict) -> UeOut:
    with _lock:
        cur = _ues.get(ue_id)
        if not cur:
            raise KeyError(ue_id)
        if "protocol" in data and data["protocol"] is not None:
            cur["protocol"] = normalize_protocol(str(data["protocol"]))
        if "status" in data and data["status"] is not None:
            cur["status"] = str(data["status"])
        if "server" in data and data["server"] is not None:
            cur["server"] = str(data["server"])
        if "port" in data and data["port"] is not None:
            cur["port"] = int(data["port"])
        if "mbits_per_second" in data and data["mbits_per_second"] is not None:
            cur["mbits_per_second"] = float(data["mbits_per_second"])
        if "message" in data and data["message"] is not None:
            cur["message"] = str(data["message"])
        cur["last_seen"] = _now()
        return _to_out(cur)


def report_apply(ue_id: str, body: UeApplyReport) -> UeOut:
    with _lock:
        cur = _ues.get(ue_id)
        if not cur:
            raise KeyError(ue_id)
        cur["applied_generation"] = int(body.generation)
        cur["apply_ok"] = bool(body.ok)
        cur["apply_message"] = body.message or ""
        if body.protocol:
            cur["protocol"] = normalize_protocol(body.protocol)
        if body.status:
            cur["status"] = body.status
        cur["last_seen"] = _now()
        cur["message"] = body.message or cur.get("message") or ""
        return _to_out(cur)


def set_desired(body: UeDesiredRequest) -> UeDesiredOut:
    """Update desired for one UE and push over its WebSocket."""
    ue_id = (body.id or "").strip()
    if not ue_id:
        raise ValueError("id is required — desired config is per connected UE")

    with _lock:
        cur = _ues.get(ue_id)
        if not cur:
            raise KeyError(ue_id)
        prev = _desired_dict(cur.get("desired"))
        proto = (
            normalize_protocol(body.protocol)
            if body.protocol is not None
            else str(prev.get("protocol") or "udp")
        )
        action = normalize_action(body.action) if body.action else "set"
        if action == "set":
            action = str(prev.get("action") or "start")
            if action == "stop" and (
                body.protocol is not None
                or body.bandwidth is not None
                or body.parallel is not None
            ):
                action = "start"
        bw = (
            body.bandwidth
            if body.bandwidth is not None
            else str(prev.get("bandwidth") or "50M")
        )
        parallel = (
            int(body.parallel)
            if body.parallel is not None
            else int(prev.get("parallel") or 5)
        )
        tcp_bw = (
            body.tcp_bandwidth
            if body.tcp_bandwidth is not None
            else str(prev.get("tcp_bandwidth") or "")
        )
        server = (
            str(body.server).strip()
            if body.server is not None
            else str(prev.get("server") or "")
        )
        reverse = (
            bool(body.reverse)
            if body.reverse is not None
            else bool(prev.get("reverse", True))
        )
        duration = (
            int(body.duration)
            if body.duration is not None
            else int(prev.get("duration") or 0)
        )
        interval = (
            float(body.interval)
            if body.interval is not None
            else float(prev.get("interval") or 1.0)
        )
        gen = int(prev.get("generation") or 0) + 1
        merged = {
            "protocol": proto,
            "action": action,
            "bandwidth": bw,
            "parallel": max(1, parallel),
            "tcp_bandwidth": tcp_bw,
            "server": server,
            "reverse": reverse,
            "duration": max(0, duration),
            "interval": max(0.1, min(60.0, interval)),
            "generation": gen,
            "updated_at": _now(),
        }
        cur["desired"] = merged
        _desired_cache[ue_id] = dict(merged)
        out = _desired_out(merged)

    push_desired(ue_id)
    return out


def get_desired(ue_id: str) -> UeDesiredOut:
    with _lock:
        cur = _ues.get(ue_id)
        if not cur:
            raise KeyError(ue_id)
        return _desired_out(cur.get("desired"))


def list_ues() -> UeListOut:
    with _lock:
        _prune_stale_locked()
        items = [_to_out(c) for c in sorted(_ues.values(), key=lambda x: x["id"])]
    return UeListOut(ues=items, stale_after_sec=STALE_AFTER_SEC)


def get_ue(ue_id: str) -> UeOut:
    with _lock:
        _prune_stale_locked()
        cur = _ues.get(ue_id)
        if not cur:
            raise KeyError(ue_id)
        return _to_out(cur)


def delete_ue(ue_id: str) -> bool:
    with _lock:
        _ws.pop(ue_id, None)
        _desired_cache.pop(ue_id, None)
        return _ues.pop(ue_id, None) is not None


def _to_out(cur: dict) -> UeOut:
    return UeOut(
        id=cur["id"],
        cluster=str(cur.get("cluster") or ""),
        namespace=str(cur.get("namespace") or ""),
        pod=str(cur.get("pod") or ""),
        ue_name=str(cur.get("ue_name") or ""),
        version=str(cur.get("version") or ""),
        online=_is_online(cur),
        ws_connected=cur.get("id") in _ws,
        protocol=str(cur.get("protocol") or "udp"),
        status=str(cur.get("status") or "idle"),
        server=str(cur.get("server") or ""),
        port=int(cur.get("port") or 0),
        mbits_per_second=float(cur.get("mbits_per_second") or 0.0),
        applied_generation=int(cur.get("applied_generation") or 0),
        apply_ok=cur.get("apply_ok"),
        apply_message=str(cur.get("apply_message") or ""),
        last_seen=str(cur.get("last_seen") or ""),
        message=str(cur.get("message") or ""),
        desired=_desired_out(cur.get("desired")),
    )
