"""Benchmark traffic helpers — per-UE desired via WebSocket control plane.

Prefer ``POST /ues/desired`` with ``id``. These endpoints apply to a single
online UE when exactly one is connected (lab convenience).
"""

from __future__ import annotations

from app.schemas import BenchmarkTrafficOut, BenchmarkTrafficRequest, UeDesiredRequest
from app.services import benchmark_log, ues


def _single_online_ue_id() -> str:
    listed = ues.list_ues()
    online = [u for u in listed.ues if u.online]
    if not online:
        raise RuntimeError("no connected UE agents — wait for iperf3-client WebSocket")
    if len(online) > 1:
        raise RuntimeError(
            f"{len(online)} UEs connected — set config per UE via POST /ues/desired with id"
        )
    return online[0].id


def get_traffic() -> BenchmarkTrafficOut:
    listed = ues.list_ues()
    online = [u for u in listed.ues if u.online]
    if not online:
        return BenchmarkTrafficOut(
            ok=True,
            protocol="udp",
            applied=False,
            generation=0,
            connected=0,
            message="no connected UEs",
        )
    if len(online) == 1:
        d = online[0].desired
        return BenchmarkTrafficOut(
            ok=True,
            protocol=(d.protocol if d else online[0].protocol) or "udp",
            applied=True,
            generation=int(d.generation) if d else 0,
            connected=1,
            message=f"UE {online[0].id} PROTOCOL={(d.protocol if d else online[0].protocol)}",
        )
    return BenchmarkTrafficOut(
        ok=True,
        protocol="udp",
        applied=False,
        generation=0,
        connected=len(online),
        message=f"{len(online)} UEs — select one for per-UE config",
    )


def set_traffic(body: BenchmarkTrafficRequest) -> BenchmarkTrafficOut:
    protocol = ues.normalize_protocol(body.protocol)
    ue_id = _single_online_ue_id()
    d = ues.set_desired(UeDesiredRequest(id=ue_id, protocol=protocol, action="start"))
    msg = f"PROTOCOL={d.protocol} gen={d.generation} → {ue_id}"
    benchmark_log.write(f"traffic {msg}", source="traffic")
    return BenchmarkTrafficOut(
        ok=True,
        protocol=d.protocol,
        applied=True,
        generation=d.generation,
        connected=1,
        message=msg,
    )
