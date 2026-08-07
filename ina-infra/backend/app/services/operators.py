"""In-memory registry for connected RAN operator agents + compute resource targets.

Operator agents connect over WebSocket (`/api/v1/operators/ws`), declare NFs +
controllable kinds, and receive pushed `desired` updates. UI still uses HTTP
to list agents and set resources.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import (
    OperatorApplyReport,
    OperatorDesiredOut,
    OperatorListOut,
    OperatorNfOut,
    OperatorOut,
    OperatorRegisterRequest,
    OperatorResourceSetRequest,
    OperatorResourceTarget,
    OPERATOR_RESOURCE_KEYS,
)

# Fallback online window if an agent briefly loses its WebSocket.
STALE_AFTER_SEC = 30

_lock = threading.RLock()
# id -> state dict
_operators: Dict[str, dict] = {}
# id -> (event loop, outbound queue) for the active WebSocket session
_ws: Dict[str, Tuple[asyncio.AbstractEventLoop, "asyncio.Queue[dict]"]] = {}


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
    """Drop operators past the offline grace window (caller must hold ``_lock``).

    Reconnect re-registers via WebSocket ``declare`` and the pane reappears.
    """
    now = now or datetime.now(timezone.utc)
    for oid in [oid for oid, cur in _operators.items() if not _is_online(cur, now)]:
        _ws.pop(oid, None)
        _operators.pop(oid, None)


def attach_ws(
    operator_id: str, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[dict]"
) -> None:
    """Bind an operator id to a WebSocket outbound queue (replaces prior session)."""
    with _lock:
        _ws[operator_id] = (loop, queue)
        cur = _operators.get(operator_id)
        if cur is None:
            cur = {"id": operator_id, "targets": {}, "apply": {}, "reported": {}}
            _operators[operator_id] = cur
        cur["ws_connected"] = True
        cur["last_seen"] = _now()
        cur["message"] = "websocket connected"


def detach_ws(operator_id: str, queue: "asyncio.Queue[dict]") -> None:
    """Clear WebSocket binding if it still matches this queue."""
    with _lock:
        entry = _ws.get(operator_id)
        if entry and entry[1] is queue:
            del _ws[operator_id]
            cur = _operators.get(operator_id)
            if cur is not None:
                cur["ws_connected"] = False
                cur["message"] = "websocket disconnected"
                cur["last_seen"] = _now()


def push_desired(operator_id: str) -> None:
    """Push current desired targets to the connected agent (no-op if offline)."""
    try:
        payload = {"type": "desired", **desired(operator_id).model_dump(mode="json")}
    except KeyError:
        return
    with _lock:
        entry = _ws.get(operator_id)
    if not entry:
        return
    loop, queue = entry
    try:
        loop.call_soon_threadsafe(queue.put_nowait, payload)
    except RuntimeError:
        pass


def register(
    body: OperatorRegisterRequest, *, seed_desired_from_reported: bool = False
) -> OperatorOut:
    """Declare / refresh NF inventory (HTTP back-compat and WS `declare` handler).

    When ``seed_desired_from_reported`` is true (first WS declare of a session),
    replace desired targets with the live values from the agent so reconnect
    does not re-apply stale UI generations. Seeded targets use generation 0
    (agent skips apply).
    """
    with _lock:
        cur = _operators.get(body.id) or {
            "id": body.id,
            "targets": {},
            "apply": {},
        }
        reported = {nf.name: nf for nf in body.nfs}
        cur.update(
            {
                "id": body.id,
                "cluster": body.cluster,
                "namespace": body.namespace,
                "version": body.version,
                "message": body.message,
                "last_seen": _now(),
                "reported": {
                    name: nf.model_dump() for name, nf in reported.items()
                },
            }
        )
        if seed_desired_from_reported:
            seeded: Dict[str, dict] = {}
            for name, nf in reported.items():
                seeded[name] = OperatorResourceTarget(
                    cpu_limit=nf.cpu_limit,
                    cpu_request=nf.cpu_request,
                    memory_limit=nf.memory_limit,
                    memory_request=nf.memory_request,
                    gpu_limit=nf.gpu_limit,
                    gpu_request=nf.gpu_request,
                    vram_limit=nf.vram_limit,
                    vram_request=nf.vram_request,
                    changed_fields=[],
                    generation=0,
                    updated_at=_now(),
                ).model_dump(mode="json")
            cur["targets"] = seeded
            cur["apply"] = {}
            cur["message"] = (
                body.message or "websocket declare"
            ) + " (desired seeded from live)"
        _operators[body.id] = cur
        out = _to_out(cur)
    # After declare, push desired so the agent does not need to poll.
    # Generation 0 seeds are ignored by the agent apply loop.
    push_desired(body.id)
    return out


def list_operators() -> OperatorListOut:
    with _lock:
        _prune_stale_locked()
        items = [_to_out(c) for c in sorted(_operators.values(), key=lambda x: x["id"])]
    return OperatorListOut(operators=items, stale_after_sec=STALE_AFTER_SEC)


def get_operator(operator_id: str) -> OperatorOut:
    with _lock:
        _prune_stale_locked()
        cur = _operators.get(operator_id)
        if not cur:
            raise KeyError(operator_id)
        return _to_out(cur)


# Resource field → controllable kind advertised by the agent.
_RESOURCE_KIND = {
    "cpu_limit": "cpu",
    "cpu_request": "cpu",
    "memory_limit": "memory",
    "memory_request": "memory",
    "gpu_limit": "gpu",
    "gpu_request": "gpu",
    "vram_limit": "vram",
    "vram_request": "vram",
}


def _nf_controllable(reported_nf: dict) -> List[str]:
    ctrl = reported_nf.get("controllable")
    if isinstance(ctrl, list) and ctrl:
        return [str(x).lower() for x in ctrl]
    return ["cpu", "memory"]


def set_resources(
    operator_id: str, nf: str, body: OperatorResourceSetRequest
) -> OperatorOut:
    with _lock:
        cur = _operators.get(operator_id)
        if not cur:
            raise KeyError(operator_id)
        reported = (cur.get("reported") or {}).get(nf) or {}
        controllable = set(_nf_controllable(reported))
        changed = [
            key for key in OPERATOR_RESOURCE_KEYS if getattr(body, key) is not None
        ]
        if not changed:
            raise ValueError("set at least one resource field")
        blocked = sorted(
            {
                _RESOURCE_KIND[key]
                for key in changed
                if _RESOURCE_KIND.get(key) not in controllable
            }
        )
        if blocked:
            raise ValueError(
                f"NF {nf!r} does not control {', '.join(blocked)} "
                f"(controllable: {', '.join(sorted(controllable)) or 'none'})"
            )
        targets: dict = cur.setdefault("targets", {})
        prev = targets.get(nf) or {}
        gen = int(prev.get("generation") or 0) + 1
        merged = {}
        for key in OPERATOR_RESOURCE_KEYS:
            incoming = getattr(body, key)
            merged[key] = incoming if incoming is not None else prev.get(key)
        targets[nf] = OperatorResourceTarget(
            **merged,
            changed_fields=changed,
            generation=gen,
            updated_at=_now(),
        ).model_dump()
        apply = cur.setdefault("apply", {})
        apply[nf] = {
            "generation": 0,
            "ok": False,
            "message": "pending",
            **merged,
        }
        out = _to_out(cur)
    push_desired(operator_id)
    return out


# Back-compat
set_cpu = set_resources


def desired(operator_id: str) -> OperatorDesiredOut:
    with _lock:
        cur = _operators.get(operator_id)
        if not cur:
            raise KeyError(operator_id)
        targets = {
            name: OperatorResourceTarget(**t)
            for name, t in (cur.get("targets") or {}).items()
        }
        return OperatorDesiredOut(id=operator_id, targets=targets)


def report_apply(operator_id: str, body: OperatorApplyReport) -> OperatorOut:
    with _lock:
        cur = _operators.get(operator_id)
        if not cur:
            raise KeyError(operator_id)
        cur.setdefault("apply", {})[body.nf] = {
            "generation": body.generation,
            "ok": body.ok,
            "message": body.message,
            **{k: getattr(body, k) for k in OPERATOR_RESOURCE_KEYS},
        }
        cur["last_seen"] = _now()
        return _to_out(cur)


def delete_operator(operator_id: str) -> bool:
    with _lock:
        _ws.pop(operator_id, None)
        return _operators.pop(operator_id, None) is not None


def _to_out(cur: dict) -> OperatorOut:
    reported: Dict[str, dict] = cur.get("reported") or {}
    targets: Dict[str, dict] = cur.get("targets") or {}
    apply: Dict[str, dict] = cur.get("apply") or {}
    names = sorted(set(reported) | set(targets))
    nfs: List[OperatorNfOut] = []
    for name in names:
        r = reported.get(name) or {}
        t = targets.get(name)
        a = apply.get(name) or {}
        desired_t = OperatorResourceTarget(**t) if t else None
        nfs.append(
            OperatorNfOut(
                name=name,
                kind=r.get("kind") or "",
                namespace=r.get("namespace") or cur.get("namespace") or "",
                controllable=_nf_controllable(r),
                reported_cpu_limit=r.get("cpu_limit"),
                reported_cpu_request=r.get("cpu_request"),
                reported_memory_limit=r.get("memory_limit"),
                reported_memory_request=r.get("memory_request"),
                reported_gpu_limit=r.get("gpu_limit"),
                reported_gpu_request=r.get("gpu_request"),
                reported_vram_limit=r.get("vram_limit"),
                reported_vram_request=r.get("vram_request"),
                ready_replicas=int(r.get("ready_replicas") or 0),
                replicas=int(r.get("replicas") or 0),
                desired=desired_t,
                applied_generation=int(a.get("generation") or 0),
                apply_status=(
                    "ok"
                    if a.get("ok")
                    else (
                        "pending"
                        if a.get("message") == "pending"
                        else ("error" if a else "")
                    )
                ),
                apply_message=str(a.get("message") or ""),
            )
        )
    last_seen = cur.get("last_seen") or ""
    return OperatorOut(
        id=cur["id"],
        cluster=cur.get("cluster") or "",
        namespace=cur.get("namespace") or "",
        version=cur.get("version") or "",
        online=_is_online(cur),
        last_seen=last_seen,
        message=cur.get("message") or "",
        nfs=nfs,
    )
