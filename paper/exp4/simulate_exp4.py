#!/usr/bin/env python3
"""Experiment 4: PL / PM / PS stepwise ablation.

Four schemes × five downlink slices. Runs S0 (static), S1 (+PL),
S2 (+PL+PM), S3 (+PL+PM+PS) on one shared diurnal + fading trace.
Application E2E delay is DL-only and includes processing time.

Outputs under paper/exp4/data/:
  exp4_scheme_metrics.csv   headline metrics (absolute + S0-normalized)
  exp4_waterfall.csv        residual % after each added layer
  exp4_per_slice.csv        per-slice delay / rate / violation
  exp4_timeseries.csv       per-step samples for appendix plots
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALG = HERE.parents[1] / "algorithm" / "new_implementation"
sys.path.insert(0, str(ALG))

from ina import Network, Slice, SliceResources  # noqa: E402
from ina.models import PsResult  # noqa: E402

LOC_NAME = {0: "edge", 1: "regional", 2: "central"}

# ---------------------------------------------------------------------------
# Workloads — application E2E definitions from the experiment brief
# ---------------------------------------------------------------------------

WORKLOADS: Dict[int, dict] = {
    1: {
        "name": "FTP",
        "label": "FTP (5MB)",
        "apps": "FTP DL",
        "t_bar": 20.0,
        "d_bar": 250.0,
        "strict_sla": False,
        "h_s": 0,
        "base_proc_ms": 2.0,
        "scales": "none",
        "file_mb": 5.0,
    },
    2: {
        "name": "YOLO",
        "label": "GPU vision (YOLO bbox DL)",
        "apps": "YOLO bbox overlay DL",
        "t_bar": 15.0,
        "d_bar": 48.0,
        "strict_sla": True,
        "h_s": 1,
        "base_proc_ms": 18.0,
        "scales": "gpu",
        "file_mb": 0.0,
    },
    3: {
        "name": "VIDEO",
        "label": "SLA video (gstreamer watch DL)",
        "apps": "gstreamer / OTT watch DL",
        "t_bar": 32.0,
        "d_bar": 58.0,
        "strict_sla": True,
        "h_s": 0,
        "base_proc_ms": 8.0,
        "scales": "cpu",
        "file_mb": 0.0,
    },
    4: {
        "name": "CPU-OFF",
        "label": "CPU offload DL (encrypt+zip+scan+LUT)",
        "apps": "5MB encrypt / zip / virus-scan / LUT then DL",
        "t_bar": 8.0,
        "d_bar": 400.0,
        "strict_sla": False,
        "h_s": 0,
        "base_proc_ms": 90.0,
        "scales": "cpu",
        "file_mb": 5.0,
    },
    5: {
        "name": "MQTT",
        "label": "MQTT Get (farm telemetry DL)",
        "apps": "MQTT Get DL",
        "t_bar": 2.0,
        "d_bar": 80.0,
        "strict_sla": True,
        "h_s": 0,
        "base_proc_ms": 3.0,
        "scales": "cpu",
        "file_mb": 0.0,
    },
}

# 24 h compressed into 24 hour-slots; each hour has PS_PER_HOUR radio samples.
PERIODS = [
    # (label, load multiplier, hours)
    ("Night", 0.20, 8),
    ("Morning", 1.00, 4),
    ("Lunch", 0.40, 2),
    ("Afternoon", 1.20, 4),
    ("Evening", 0.80, 6),
]
PS_PER_HOUR = 8
SEED = 2026

# Cross-site user-plane cost ($ / Mbps-hour). Co-located hop = 0.
P_F1 = {0: 0.00, 1: 0.04, 2: 0.08}
P_N3 = 0.06
P_N6 = 0.10


def make_network() -> Network:
    """Testbed-sized substrate: GPU at edge, headroom at regional/central."""
    return Network(
        c_n_capacity={0: 56, 1: 88, 2: 96},
        r_n_capacity={0: 72, 1: 80, 2: 96},
        c_a_capacity={0: 48, 1: 72, 2: 96},
        r_a_capacity={0: 2800, 1: 4200, 2: 8000},
        g_a_capacity={0: 24, 1: 12, 2: 8},
        # Balanced site prices so PL still prefers cheap sites when delay allows,
        # but OPEX numbers stay on a publishable scale (not 0.001 vs 2.5).
        p_c={0: 3.0, 1: 1.6, 2: 0.8},
        p_r={0: 0.30, 1: 0.16, 2: 0.08},
        p_g={0: 5.0, 1: 3.0, 2: 1.5},
        w_c=1.0,
        w_p=1000.0,
        beta_demand=0.1,
        gurobi_output=0,
    )


def make_slices() -> List[Slice]:
    eta0 = {1: 2.4, 2: 2.2, 3: 2.5, 4: 2.3, 5: 2.6}
    return [
        Slice(
            id=sid,
            t_bar=w["t_bar"],
            d_bar=w["d_bar"],
            h_s=w["h_s"],
            eta_t0=eta0[sid],
            slice_type=w["name"],
        )
        for sid, w in WORKLOADS.items()
    ]


def _needs_gpu(sid: int) -> bool:
    return WORKLOADS[sid]["scales"] == "gpu"


def peak_resources(s: Slice, net: Network, rate: float | None = None) -> SliceResources:
    """Size CU/UPF/APP for a target rate. GPU only for vision slices."""
    t = s.t_bar if rate is None else rate
    return SliceResources(
        a_c_cu=t / net.alpha_cu,
        a_r_cu=net.min_r_cu,
        a_c_upf=t / net.alpha_upf,
        a_r_upf=net.min_r_upf,
        a_c_app=t / net.gamma_c,
        a_r_app=t / net.gamma_r,
        a_g_app=(t / net.gamma_g) if _needs_gpu(s.id) else 0.0,
        b_min=None,
        b_ded=None,
    )


def compute_cap(sid: int, res: SliceResources, net: Network) -> float:
    caps = [
        net.alpha_cu * res.a_c_cu,
        net.alpha_upf * res.a_c_upf,
        net.gamma_c * res.a_c_app,
        net.gamma_r * res.a_r_app,
    ]
    if _needs_gpu(sid):
        caps.append(net.gamma_g * max(res.a_g_app, 1e-6))
    return min(caps)


def transport_delay_ms(net: Network, cu: int, upf: int, app: int) -> float:
    return net.d_rf + net.d_f1[cu] + net.d_n3[(cu, upf)] + net.d_n6[(upf, app)]


def transport_opex(cu: int, upf: int, app: int, rate_mbps: float, dt_h: float) -> float:
    cost = P_F1[cu] * rate_mbps
    if cu != upf:
        cost += P_N3 * rate_mbps
    if upf != app:
        cost += P_N6 * rate_mbps
    return cost * dt_h


def compute_opex(net: Network, cu: int, upf: int, app: int, res: SliceResources, dt_h: float) -> float:
    return dt_h * (
        res.a_c_cu * net.p_c[cu]
        + res.a_r_cu * net.p_r[cu]
        + res.a_c_upf * net.p_c[upf]
        + res.a_r_upf * net.p_r[upf]
        + res.a_c_app * net.p_c[app]
        + res.a_r_app * net.p_r[app]
        + res.a_g_app * net.p_g[app]
    )


def proc_delay_ms(sid: int, res: SliceResources, net: Network, offered: float) -> float:
    w = WORKLOADS[sid]
    base = w["base_proc_ms"]
    cap = max(compute_cap(sid, res, net), 1e-6)
    stretch = max(1.0, offered / cap)
    if w["scales"] == "gpu":
        need = max(offered, 0.05 * WORKLOADS[sid]["t_bar"]) / net.gamma_g
        gpu_ratio = max(res.a_g_app / max(need, 1e-6), 0.20)
        return base * stretch / gpu_ratio
    if w["scales"] == "cpu":
        need = max(offered, 0.05 * WORKLOADS[sid]["t_bar"]) / net.gamma_c
        cpu_ratio = max(res.a_c_app / max(need, 1e-6), 0.20)
        return base * stretch / cpu_ratio
    return base * stretch


def eta_at(rng: np.random.Generator, step: int, sid: int) -> float:
    """Pedestrian / vehicular mix + periodic deep fades on slices 2 and 3."""
    cqi = int(rng.integers(5, 20))
    eta = 0.28 + 0.13 * cqi
    fade = 0.50 + 0.50 * (0.5 + 0.5 * np.sin(2.0 * np.pi * step / 15.0 + 0.7 * sid))
    # Recurring deep-fade windows on the radio-sensitive slices.
    if sid in (2, 3) and (step % 24) in range(16, 22):
        fade *= 0.28
    return float(max(0.18, eta * fade))


def force_s0_placement(slices: List[Slice]) -> Dict[int, Tuple[int, int, int]]:
    """Uncoordinated static: CU-UP+UPF at central, APP at edge."""
    deploy = {}
    for s in slices:
        place = (2, 2, 0)
        s.placement = place
        deploy[s.id] = place
    return deploy


def _site_cost(net: Network, cu: int, upf: int, app: int, res: SliceResources, rate: float) -> float:
    return (
        res.a_c_cu * net.p_c[cu] + res.a_r_cu * net.p_r[cu]
        + res.a_c_upf * net.p_c[upf] + res.a_r_upf * net.p_r[upf]
        + res.a_c_app * net.p_c[app] + res.a_r_app * net.p_r[app]
        + res.a_g_app * net.p_g[app]
        + transport_opex(cu, upf, app, rate, 1.0)
    )


def solve_pl_heuristic(slices: List[Slice], net: Network) -> Dict[int, Tuple[int, int, int]]:
    """Delay-feasible cheapest-site PL (Gurobi academic license cannot fit the MILP).

    Strictest delay budgets are placed first. CU/UPF/APP are co-located when
    that still meets ``d_bar`` — that is the N6-hairpin removal the waterfall
    attributes to PL.
    """
    rem_cn = dict(net.c_n_capacity)
    rem_rn = dict(net.r_n_capacity)
    rem_ca = dict(net.c_a_capacity)
    rem_ra = dict(net.r_a_capacity)
    rem_ga = dict(net.g_a_capacity)
    deploy: Dict[int, Tuple[int, int, int]] = {}
    locs = list(net.locations)

    for s in sorted(slices, key=lambda x: x.d_bar):
        res = peak_resources(s, net)
        best: Tuple[int, int, int] | None = None
        best_cost = float("inf")
        for cu in locs:
            for upf in locs:
                for app in locs:
                    d_tx = transport_delay_ms(net, cu, upf, app)
                    d_pr = WORKLOADS[s.id]["base_proc_ms"]
                    if d_tx + d_pr > s.d_bar:
                        continue
                    if res.a_c_cu + (res.a_c_upf if cu == upf else 0) > rem_cn[cu] + 1e-9:
                        continue
                    if cu != upf and res.a_c_upf > rem_cn[upf] + 1e-9:
                        continue
                    if res.a_c_app > rem_ca[app] + 1e-9:
                        continue
                    if res.a_r_app > rem_ra[app] + 1e-9:
                        continue
                    if res.a_g_app > rem_ga[app] + 1e-9:
                        continue
                    cost = _site_cost(net, cu, upf, app, res, s.t_bar)
                    if cost < best_cost:
                        best_cost = cost
                        best = (cu, upf, app)
        if best is None:
            best = (0, 0, 0)
        cu, upf, app = best
        rem_cn[cu] -= res.a_c_cu
        rem_rn[cu] -= res.a_r_cu
        rem_cn[upf] -= res.a_c_upf
        rem_rn[upf] -= res.a_r_upf
        rem_ca[app] -= res.a_c_app
        rem_ra[app] -= res.a_r_app
        rem_ga[app] -= res.a_g_app
        s.placement = best
        s.resources = res
        deploy[s.id] = best
    return deploy


def solve_pm_heuristic(
    slices: List[Slice],
    deploy: Dict[int, Tuple[int, int, int]],
    net: Network,
) -> Dict[int, SliceResources]:
    """Scale compute to current offered demand at the frozen PL sites.

    Floor at 40% of planning T̄ (do not collapse to zero at night) and keep
    12% headroom so a 1.2× afternoon burst does not overflow the queue.
    """
    out: Dict[int, SliceResources] = {}
    for s in slices:
        nominal = WORKLOADS[s.id]["t_bar"]
        rate = max(s.demand, 0.40 * nominal) * 1.12
        out[s.id] = peak_resources(s, net, rate=rate)
        s.resources = out[s.id]
    return out


def solve_ps_heuristic(slices: List[Slice], net: Network) -> PsResult:
    """Give strict-SLA slices enough PRBs for eta * b >= offered; leftover to BE."""
    need = {}
    for s in slices:
        eta = max(s.eta, 0.15)
        target = s.t_bar if s.t_bar > 0 else WORKLOADS[s.id]["t_bar"]
        weight = 1.35 if WORKLOADS[s.id]["strict_sla"] else 0.55
        need[s.id] = weight * target / eta
    total = sum(need.values())
    scale = min(1.0, 0.92 * net.b_total / total) if total else 1.0
    b_min = {sid: max(2.0, raw * scale) for sid, raw in need.items()}
    reserved = sum(b_min.values())
    extra = max(0.0, net.b_total - reserved) / max(len(slices), 1)
    b_ded = {s.id: (b_min[s.id] if s.h_s else 0.0) for s in slices}
    return PsResult(
        b_min=b_min,
        b_ded=b_ded,
        extra=extra,
        b_max={sid: b_min[sid] + extra for sid in b_min},
    )


@dataclass
class SchemeResult:
    scheme: str
    sla_violations: int = 0
    sla_samples: int = 0
    relaxed_violations: int = 0
    relaxed_samples: int = 0
    opex: float = 0.0
    opex_compute: float = 0.0
    opex_transport: float = 0.0
    opex_radio: float = 0.0
    throughput_mbps_sum: float = 0.0
    offered_mbps_sum: float = 0.0
    delay_sum: float = 0.0
    delay_n: int = 0
    per_slice: Dict[int, dict] = field(default_factory=dict)
    rows: List[dict] = field(default_factory=list)
    deploy: Dict[int, Tuple[int, int, int]] = field(default_factory=dict)

    def sla_rate(self) -> float:
        return self.sla_violations / self.sla_samples if self.sla_samples else 0.0

    def mean_throughput(self) -> float:
        n = max(len(self.rows), 1)
        # rows are per-slice-per-step; convert to cell-wide mean
        steps = max({r["step"] for r in self.rows}, default=0) + 1 if self.rows else 1
        return self.throughput_mbps_sum / steps


def _init_per_slice() -> Dict[int, dict]:
    out = {}
    for sid, w in WORKLOADS.items():
        out[sid] = {
            "name": w["name"],
            "label": w["label"],
            "strict": w["strict_sla"],
            "violations": 0,
            "samples": 0,
            "delay_ms": [],
            "thr_mbps": [],
            "offered_mbps": [],
        }
    return out


def run_scheme(scheme: str, net: Network, seed: int = SEED) -> SchemeResult:
    use_pl = scheme in {"S1", "S2", "S3"}
    use_pm = scheme in {"S2", "S3"}
    use_ps = scheme == "S3"

    slices = make_slices()

    if use_pl:
        deploy = solve_pl_heuristic(slices, net)
        frozen = {s.id: replace(s.resources or peak_resources(s, net)) for s in slices}
    else:
        deploy = force_s0_placement(slices)
        frozen = {s.id: peak_resources(s, net) for s in slices}
        for s in slices:
            s.resources = frozen[s.id]

    resources: Dict[int, SliceResources] = {sid: replace(r) for sid, r in frozen.items()}
    n_prb_eq = net.b_total / len(slices)
    rng = np.random.default_rng(seed)
    acc = SchemeResult(scheme=scheme, deploy=deploy, per_slice=_init_per_slice())
    dt_h = 1.0 / PS_PER_HOUR
    step = 0

    hour_loads: List[float] = []
    for _, load, hours in PERIODS:
        hour_loads.extend([load] * hours)

    for hour, load in enumerate(hour_loads):
        offered = {s.id: s.t_bar * load for s in slices}

        if use_pm:
            for s in slices:
                s.demand = offered[s.id]
                s.t_bar = offered[s.id]
            resources = solve_pm_heuristic(slices, deploy, net)
            for s in slices:
                s.t_bar = WORKLOADS[s.id]["t_bar"]
        else:
            resources = {sid: replace(r) for sid, r in frozen.items()}

        for _ in range(PS_PER_HOUR):
            etas = {s.id: eta_at(rng, step, s.id) for s in slices}
            if use_ps:
                for s in slices:
                    s.eta = etas[s.id]
                    s.resources = resources[s.id]
                    s.t_bar = offered[s.id]
                ps_out = solve_ps_heuristic(slices, net)
                b_use = dict(ps_out.b_max)
                for s in slices:
                    s.t_bar = WORKLOADS[s.id]["t_bar"]
            else:
                b_use = {s.id: n_prb_eq for s in slices}

            for s in slices:
                sid = s.id
                cu, upf, app = deploy[sid]
                res = resources[sid]
                radio = b_use[sid] * etas[sid]
                cap = compute_cap(sid, res, net)
                off = offered[sid]
                delivered = min(radio, cap, off)
                d_tx = transport_delay_ms(net, cu, upf, app)
                d_pr = proc_delay_ms(sid, res, net, off)
                d_air = float(rng.uniform(0.0, 3.5))
                d_e2e = d_tx + d_pr + d_air

                c_comp = compute_opex(net, cu, upf, app, res, dt_h)
                c_tr = transport_opex(cu, upf, app, off, dt_h)
                c_rad = (b_use[sid] * (net.p_prb_prio if not use_ps else net.p_prb_ded * 0.4 + net.p_prb_prio * 0.6)) * dt_h * 0.02

                acc.opex += c_comp + c_tr + c_rad
                acc.opex_compute += c_comp
                acc.opex_transport += c_tr
                acc.opex_radio += c_rad
                acc.throughput_mbps_sum += delivered
                acc.offered_mbps_sum += off
                acc.delay_sum += d_e2e
                acc.delay_n += 1

                w = WORKLOADS[sid]
                rate_ok = delivered >= 0.95 * off if off > 1e-9 else True
                delay_ok = d_e2e <= w["d_bar"]
                violated = (not rate_ok) or (not delay_ok)
                if w["strict_sla"]:
                    acc.sla_samples += 1
                    acc.sla_violations += int(violated)
                else:
                    acc.relaxed_samples += 1
                    # Relaxed: only severe misses (2x delay or <50% rate).
                    severe = (d_e2e > 2.0 * w["d_bar"]) or (delivered < 0.50 * off)
                    acc.relaxed_violations += int(severe)

                ps_acc = acc.per_slice[sid]
                ps_acc["samples"] += 1
                ps_acc["violations"] += int(violated)
                ps_acc["delay_ms"].append(d_e2e)
                ps_acc["thr_mbps"].append(delivered)
                ps_acc["offered_mbps"].append(off)

                acc.rows.append({
                    "scheme": scheme,
                    "step": step,
                    "hour": hour,
                    "load": load,
                    "slice": sid,
                    "slice_name": w["name"],
                    "offered_mbps": off,
                    "delivered_mbps": delivered,
                    "radio_mbps": radio,
                    "compute_cap_mbps": cap,
                    "eta": etas[sid],
                    "d_e2e_ms": d_e2e,
                    "d_transport_ms": d_tx,
                    "d_proc_ms": d_pr,
                    "violated": int(violated),
                    "placement": f"{LOC_NAME[cu]}/{LOC_NAME[upf]}/{LOC_NAME[app]}",
                })
            step += 1

    return acc


def summarize(results: Dict[str, SchemeResult]) -> Tuple[List[dict], List[dict], List[dict]]:
    s0 = results["S0"]
    sla0 = max(s0.sla_rate(), 1e-9)
    opex0 = max(s0.opex, 1e-9)
    thr0 = max(s0.mean_throughput(), 1e-9)
    eff0 = thr0 / opex0

    scheme_rows = []
    waterfall_rows = []
    slice_rows = []

    order = ["S0", "S1", "S2", "S3"]
    labels = {
        "S0": "S0 Static",
        "S1": "S1 +PL",
        "S2": "S2 +PL +PM",
        "S3": "S3 +PL +PM +PS",
    }
    for sch in order:
        r = results[sch]
        sla = r.sla_rate()
        thr = r.mean_throughput()
        eff = thr / max(r.opex, 1e-9)
        scheme_rows.append({
            "scheme": sch,
            "label": labels[sch],
            "sla_violation_rate": sla,
            "sla_violation_norm_pct": 100.0 * sla / sla0,
            "opex": r.opex,
            "opex_norm_pct": 100.0 * r.opex / opex0,
            "opex_compute": r.opex_compute,
            "opex_transport": r.opex_transport,
            "opex_radio": r.opex_radio,
            "throughput_mbps": thr,
            "throughput_norm_pct": 100.0 * thr / thr0,
            "efficiency": eff,
            "efficiency_norm_pct": 100.0 * eff / eff0,
            "mean_e2e_ms": r.delay_sum / max(r.delay_n, 1),
            "placement": ";".join(
                f"S{sid}={LOC_NAME[p[0]]}/{LOC_NAME[p[1]]}/{LOC_NAME[p[2]]}"
                for sid, p in sorted(r.deploy.items())
            ),
        })
        waterfall_rows.append({
            "scheme": sch,
            "label": labels[sch],
            "sla_residual_pct": 100.0 * sla / sla0,
            "opex_residual_pct": 100.0 * r.opex / opex0,
            "throughput_pct": 100.0 * thr / thr0,
            "efficiency_pct": 100.0 * eff / eff0,
        })

    for sch in order:
        r = results[sch]
        for sid, acc in r.per_slice.items():
            d = np.asarray(acc["delay_ms"], dtype=float)
            t = np.asarray(acc["thr_mbps"], dtype=float)
            slice_rows.append({
                "scheme": sch,
                "slice": sid,
                "name": acc["name"],
                "label": acc["label"],
                "strict_sla": int(acc["strict"]),
                "violation_rate": acc["violations"] / max(acc["samples"], 1),
                "mean_e2e_ms": float(np.mean(d)),
                "p50_e2e_ms": float(np.percentile(d, 50)),
                "p95_e2e_ms": float(np.percentile(d, 95)),
                "p99_e2e_ms": float(np.percentile(d, 99)),
                "mean_throughput_mbps": float(np.mean(t)),
                "placement": "/".join(LOC_NAME[x] for x in r.deploy[sid]),
            })
    return scheme_rows, waterfall_rows, slice_rows


def _write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path}")


def main() -> None:
    net = make_network()
    print("Experiment 4 — PL / PM / PS synergy ablation")
    names = ", ".join(f"{i}:{WORKLOADS[i]['name']}" for i in WORKLOADS)
    print(f"  slices: {names}")
    results: Dict[str, SchemeResult] = {}
    for sch in ("S0", "S1", "S2", "S3"):
        print(f"  running {sch} ...")
        results[sch] = run_scheme(sch, net)
        r = results[sch]
        place = " ".join(
            f"S{sid}={LOC_NAME[p[0]][0]}/{LOC_NAME[p[1]][0]}/{LOC_NAME[p[2]][0]}"
            for sid, p in sorted(r.deploy.items())
        )
        print(
            f"    SLA={100*r.sla_rate():.1f}%  OPEX={r.opex:.1f}  "
            f"Tput={r.mean_throughput():.1f} Mbps  place={place}"
        )

    scheme_rows, waterfall_rows, slice_rows = summarize(results)
    _write_csv(DATA_DIR / "exp4_scheme_metrics.csv", scheme_rows)
    _write_csv(DATA_DIR / "exp4_waterfall.csv", waterfall_rows)
    _write_csv(DATA_DIR / "exp4_per_slice.csv", slice_rows)

    # Timeseries can be large; keep it — plot script may subsample.
    ts_rows = []
    for sch in ("S0", "S1", "S2", "S3"):
        ts_rows.extend(results[sch].rows)
    _write_csv(DATA_DIR / "exp4_timeseries.csv", ts_rows)

    print("\nNormalized residuals (S0 = 100%):")
    for row in waterfall_rows:
        print(
            f"  {row['label']:<16}  SLA {row['sla_residual_pct']:6.1f}%   "
            f"OPEX {row['opex_residual_pct']:6.1f}%   "
            f"Tput {row['throughput_pct']:6.1f}%   "
            f"Eff {row['efficiency_pct']:6.1f}%"
        )


if __name__ == "__main__":
    main()
