"""Application-server OPEX for live Exp4 runs ($ / hour).

List prices are **central**. Site ρ scales them (edge is scarce MEC):

    C = ρ_site · ( p_cpu · vCPU + p_ram · GiB + p_gpu · GPU + p_vram · GiB_vram )

Central prices match ``simulate_exp4.py`` ``p_c[2]``, ``p_r[2]``, ``p_g[2]``.
GPU occupancy plus a full A40 VRAM reservation (48 GiB) equals that GPU
price (1.20 + 0.30 = 1.50).

S0/S1 bill frozen **peak requests** (max allocation, not Influx usage).
S2/S3 (PM) bill measured CPU/RAM usage, capped at the S1 peak so PM
cannot cost more than the no-PM allocation. GPU/VRAM stay at the S1
allocation (the A40 is not fractional).
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Dict, Mapping, Optional

HERE = Path(__file__).resolve().parent

SITE_PRICE = {
    "central": 1.0,
    "regional": 2.0,
    "edge": 4.0,
}

# Central list prices ($ / hour). Edge/regional = ρ × these.
PRICE_CPU_CORE_H = 0.80
PRICE_RAM_GB_H = 0.08
PRICE_GPU_H = 1.20
PRICE_VRAM_GB_H = 0.00625  # 48 GiB A40 → 0.30 $/h; GPU+VRAM = 1.50

A40_VRAM_MIB = 48.0 * 1024.0

# No PM: bill requests. PM schemes use measured usage.
# S0/S1: bill peak requests. S2/S3: bill usage (PM), not those peaks.
ALLOCATED_SCHEMES = frozenset({"s0", "s1"})
PEAK_SCHEME = "s1"

APP_SITE = {
    "s0": {1: "edge", 2: "edge", 3: "edge", 4: "edge", 5: "edge"},
    "s1": {1: "central", 2: "edge", 3: "regional", 4: "central", 5: "central"},
    "s2": {1: "central", 2: "edge", 3: "regional", 4: "central", 5: "central"},
    "s3": {1: "central", 2: "edge", 3: "regional", 4: "central", 5: "central"},
}


def _short(scheme: str) -> str:
    s = (scheme or "").strip().lower()
    return s.replace("exp4-", "") if s.startswith("exp4-") else s


def uses_allocated(scheme: str) -> bool:
    """True for S0/S1: OPEX from peak requests, not Influx usage."""
    return _short(scheme) in ALLOCATED_SCHEMES


def _finite(v: Optional[float], default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    return x


def parse_mem_mib(text: object) -> float:
    raw = str(text).strip().upper()
    if raw.endswith("GI"):
        return float(raw[:-2]) * 1024.0
    if raw.endswith("G"):
        return float(raw[:-1]) * 1024.0
    if raw.endswith("MI"):
        return float(raw[:-2])
    if raw.endswith("M"):
        return float(raw[:-1])
    return float(raw)


@lru_cache(maxsize=8)
def scheme_slices(scheme: str) -> dict:
    short = _short(scheme)
    path = HERE / short / "scheme.py"
    spec = importlib.util.spec_from_file_location(f"exp4_{short}_scheme", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"missing {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.SLICES)


def app_site(scheme: str, slice_id: int) -> str:
    table = APP_SITE.get(_short(scheme)) or APP_SITE["s0"]
    return table.get(int(slice_id), "central")


def site_price(scheme: str, slice_id: int) -> float:
    return SITE_PRICE[app_site(scheme, slice_id)]


def allocated_usage(scheme: str, slice_id: int) -> Dict[str, float]:
    """Frozen K8s requests for one slice (S0/S1 have no PM)."""
    s = scheme_slices(scheme)[int(slice_id)]
    gpu = _finite(s.get("gpu_app"))
    return {
        "cpu_m": _finite(s.get("cpu_app")) * 1000.0,
        "mem_mb": parse_mem_mib(s.get("mem_app") or 0),
        "gpu_pct": gpu * 100.0,
        "vram_mb": A40_VRAM_MIB * gpu,
    }


def resource_cost_h(
    *,
    cpu_m: float = 0.0,
    mem_mb: float = 0.0,
    gpu_pct: float = 0.0,
    vram_mb: float = 0.0,
) -> Dict[str, float]:
    """Central $/h for one slice before site ρ."""
    return {
        "cpu": (_finite(cpu_m) / 1000.0) * PRICE_CPU_CORE_H,
        "ram": (_finite(mem_mb) / 1024.0) * PRICE_RAM_GB_H,
        "gpu": (_finite(gpu_pct) / 100.0) * PRICE_GPU_H,
        "vram": (_finite(vram_mb) / 1024.0) * PRICE_VRAM_GB_H,
    }


def resource_shares(
    *,
    cpu_m: float = 0.0,
    mem_mb: float = 0.0,
    gpu_pct: float = 0.0,
    vram_mb: float = 0.0,
) -> Dict[str, float]:
    """Alias of ``resource_cost_h`` (legacy name used by plots)."""
    return resource_cost_h(cpu_m=cpu_m, mem_mb=mem_mb, gpu_pct=gpu_pct, vram_mb=vram_mb)


def billed_usage(scheme: str, slice_id: int, usage: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    """Resources billed for OPEX.

    S0/S1: peak K8s requests. S2/S3: Influx CPU/RAM (fallback: PM requests in
    scheme.py), each capped at S1 peak. GPU/VRAM stay at S1 allocation.
    """
    short = _short(scheme)
    if uses_allocated(short):
        return allocated_usage(short, slice_id)
    peak = allocated_usage(PEAK_SCHEME, slice_id)
    u = usage or {}
    cpu = _finite(u.get("cpu_m"))
    mem = _finite(u.get("mem_mb"))
    if cpu <= 0 and mem <= 0:
        pm = allocated_usage(short, slice_id)
        cpu, mem = pm["cpu_m"], pm["mem_mb"]
    return {
        "cpu_m": min(cpu, peak["cpu_m"]) if peak["cpu_m"] > 0 else cpu,
        "mem_mb": min(mem, peak["mem_mb"]) if peak["mem_mb"] > 0 else mem,
        "gpu_pct": peak["gpu_pct"],
        "vram_mb": peak["vram_mb"],
    }


def mix_from_usage(usage: Mapping[str, float]) -> Dict[str, float]:
    return resource_cost_h(
        cpu_m=usage.get("cpu_m", 0.0),
        mem_mb=usage.get("mem_mb", 0.0),
        gpu_pct=usage.get("gpu_pct", 0.0),
        vram_mb=usage.get("vram_mb", 0.0),
    )


def compute_cost(
    *,
    scheme: str,
    slice_id: int,
    cpu_m: float = 0.0,
    mem_mb: float = 0.0,
    gpu_pct: float = 0.0,
    vram_mb: float = 0.0,
) -> float:
    parts = resource_cost_h(cpu_m=cpu_m, mem_mb=mem_mb, gpu_pct=gpu_pct, vram_mb=vram_mb)
    rho = site_price(scheme, slice_id)
    return rho * (parts["cpu"] + parts["ram"] + parts["gpu"] + parts["vram"])


def cost_from_usage(scheme: str, slice_id: int, usage: Mapping[str, float]) -> float:
    return slice_cost(scheme, slice_id, usage)


def slice_cost(scheme: str, slice_id: int, usage: Optional[Mapping[str, float]] = None) -> float:
    u = billed_usage(scheme, slice_id, usage)
    return compute_cost(
        scheme=scheme,
        slice_id=slice_id,
        cpu_m=u["cpu_m"],
        mem_mb=u["mem_mb"],
        gpu_pct=u["gpu_pct"],
        vram_mb=u["vram_mb"],
    )


def cost_breakdown(scheme: str, slice_id: int, usage: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    rho = site_price(scheme, slice_id)
    parts = mix_from_usage(billed_usage(scheme, slice_id, usage))
    return {k: rho * v for k, v in parts.items()}
