#!/usr/bin/env python3
"""Compare live Exp4 runs: delay, throughput, violation, server usage, and OPEX.

Server CPU/RAM/GPU/VRAM: S0/S1 from scheme.py peak requests; S2/S3 from
Influx. OPEX is $ / hour: central list prices × site ρ (edge=4,
regional=2, central=1). See ``paper/exp4/cost_model.py``.

Violation score = delay overshoot max(0, d/D̄−1) plus rate shortfall
max(0, 1 − rate/(0.95·T̄)). Zero means the sample meets both budgets.
D̄/T̄ are the contended five-UE bars in exp_start.SLICES (not SX isolated means).

Usage:
  python3 paper/exp4/compare_schemes.py
  python3 paper/exp4/compare_schemes.py --schemes s0 s1 s2 s3
  python3 paper/exp4/compare_schemes.py --s0-run-id 20260909-102816_300s \\
      --s1-run-id 20260909-110400_300s --s2-run-id 20260909-121245_300s
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cost_model import (  # noqa: E402
    PRICE_CPU_CORE_H,
    PRICE_GPU_H,
    PRICE_RAM_GB_H,
    PRICE_VRAM_GB_H,
    SITE_PRICE,
    allocated_usage,
    app_site,
    cost_breakdown,
    slice_cost,
    uses_allocated,
)
from exp_plot import load_samples_csv, load_summary_json, resolve_run_dir  # noqa: E402
from exp_start import SLICES, parse_rfc3339, query_series, violation_score, write_points_csv  # noqa: E402

SCHEME_COLORS = {
    "s0": "#7A7A7A",
    "s1": "#1F77B4",
    "s2": "#E67E22",
    "s3": "#2E8B57",
}
SCHEME_LABELS = {
    "s0": "S0 static",
    "s1": "S1 +PL",
    "s2": "S2 +PL+PM",
    "s3": "S3 +PL+PM+PS",
}

# (csv stem, dict key, axis label, bar annotation format)
COST_FIELDS = (
    ("cpu_m", "cpu_m", "CPU (millicores)", "{:.0f}"),
    ("mem_mb", "mem_mb", "RAM (MiB)", "{:.0f}"),
    ("gpu_pct", "gpu_pct", "GPU (%)", "{:.1f}"),
    ("vram_mb", "vram_mb", "VRAM (MiB)", "{:.0f}"),
)


def _short(scheme: str) -> str:
    s = scheme.strip().lower()
    return s.replace("exp4-", "") if s.startswith("exp4-") else s


def _ps(summary: dict, sid: int) -> dict:
    ps = summary.get("per_slice") or {}
    return ps.get(sid) or ps.get(str(sid)) or {}


def _mean(row: dict, key: str) -> float:
    v = row.get(key)
    if v is None:
        return float("nan")
    return float(v)


def _viol_pct(run: "SchemeRun", sid: int) -> float:
    return violation_pct_from_samples(run.samples, sid)


def _strict_sla_pct(run: "SchemeRun") -> float:
    return _strict_sla_pct_from_samples(run.samples)


def _viol_score(run: "SchemeRun", sid: int) -> float:
    return run.score_mean.get(sid, float("nan"))


def mean_violation_scores(samples: List[dict]) -> Tuple[Dict[int, float], float]:
    """Per-slice and strict-index mean violation scores from samples.csv.

    Uses current ``SLICES`` D̄/T̄ (contended five-UE bars), not bars baked into the CSV.
    """
    sums: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    strict_sum = 0.0
    strict_n = 0
    for r in samples:
        try:
            sid = int(r["slice"])
            spec = SLICES[sid]
            score = violation_score(
                delay_ms=float(r["delay_ms"]),
                rate_mbps=float(r["throughput_dl_mbps"]),
                d_bar_ms=float(spec["d_bar_ms"]),
                t_bar_mbps=float(spec["t_bar_mbps"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        sums[sid] = sums.get(sid, 0.0) + score
        counts[sid] = counts.get(sid, 0) + 1
        if spec.get("strict_sla"):
            strict_sum += score
            strict_n += 1
    per_slice = {sid: sums[sid] / counts[sid] for sid in sums if counts[sid]}
    strict = (strict_sum / strict_n) if strict_n else float("nan")
    return per_slice, strict


def violation_pct_from_samples(samples: List[dict], sid: int) -> float:
    """Percent of samples that miss current D̄ or 0.95·T̄."""
    from exp_start import RATE_FLOOR

    spec = SLICES[sid]
    d_bar = float(spec["d_bar_ms"])
    floor = RATE_FLOOR * float(spec["t_bar_mbps"])
    n = 0
    v = 0
    for r in samples:
        try:
            if int(r["slice"]) != sid:
                continue
            delay = float(r["delay_ms"])
            rate = float(r["throughput_dl_mbps"])
        except (KeyError, TypeError, ValueError):
            continue
        n += 1
        if delay > d_bar or rate < floor:
            v += 1
    if not n:
        return float("nan")
    return 100.0 * v / n


def _strict_sla_pct_from_samples(samples: List[dict]) -> float:
    from exp_start import RATE_FLOOR

    n = 0
    v = 0
    for r in samples:
        try:
            sid = int(r["slice"])
            spec = SLICES[sid]
            if not spec.get("strict_sla"):
                continue
            delay = float(r["delay_ms"])
            rate = float(r["throughput_dl_mbps"])
        except (KeyError, TypeError, ValueError):
            continue
        n += 1
        if delay > float(spec["d_bar_ms"]) or rate < RATE_FLOOR * float(spec["t_bar_mbps"]):
            v += 1
    if not n:
        return float("nan")
    return 100.0 * v / n


def _csv_mean(path: Path, value_col: str) -> float:
    vals = load_metric_series(path, value_col)[1]
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def load_metric_series(path: Path, value_col: str) -> Tuple[List[float], List[float]]:
    xs: List[float] = []
    ys: List[float] = []
    if not path.is_file():
        return xs, ys
    t0: Optional[float] = None
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["t_unix_s"])
                v = float(row[value_col])
            except (KeyError, TypeError, ValueError):
                continue
            if t0 is None:
                t0 = t
            xs.append(t - t0)
            ys.append(v)
    return xs, ys


def cost_series(run: SchemeRun, sid: int) -> Tuple[List[float], List[float]]:
    """OPEX vs time. S0/S1 are flat (allocated requests); S2/S3 follow usage."""
    spec = SLICES[sid]
    path = run.path / "metrics" / spec["app_type"] / "server_cpu_m.csv"
    xs, _ = load_metric_series(path, "cpu_m")
    if not xs:
        xs, _, _ = samples_series(run.samples, sid)
    if not xs:
        return [], []
    if uses_allocated(run.short):
        c = slice_cost(run.short, sid)
        return xs, [c] * len(xs)
    import numpy as np

    series: Dict[str, Tuple[List[float], List[float]]] = {}
    for stem, key, _label, _fmt in COST_FIELDS:
        p = run.path / "metrics" / spec["app_type"] / f"server_{stem}.csv"
        series[key] = load_metric_series(path if stem == "cpu_m" else p, stem)
    xq = np.array(xs, dtype=float)

    def interp(key: str) -> np.ndarray:
        tx, ys = series[key]
        if not tx:
            return np.zeros_like(xq)
        return np.interp(xq, tx, ys)

    ys = [
        slice_cost(
            run.short,
            sid,
            {
                "cpu_m": float(c),
                "mem_mb": float(r),
                "gpu_pct": float(g),
                "vram_mb": float(v),
            },
        )
        for c, r, g, v in zip(interp("cpu_m"), interp("mem_mb"), interp("gpu_pct"), interp("vram_mb"))
    ]
    return list(xq), ys


def backfill_vram(run: Path, scheme: str, summary: dict) -> None:
    """Download server vram_mb if the CSV is empty (older exp_start runs)."""
    start_s = summary.get("start_utc") or ""
    stop_s = summary.get("stop_utc") or ""
    if not start_s or not stop_s:
        meta = run / "meta.json"
        if meta.is_file():
            meta_doc = load_summary_json(meta)
            start_s = start_s or meta_doc.get("start_utc") or ""
            stop_s = stop_s or meta_doc.get("stop_utc") or ""
    if not start_s or not stop_s:
        return
    start = parse_rfc3339(start_s)
    stop = parse_rfc3339(stop_s)
    for sid, spec in SLICES.items():
        path = run / "metrics" / spec["app_type"] / f"server_vram_mb.csv"
        if path.is_file() and path.stat().st_size > 80:
            continue
        pts = query_series(
            start=start,
            stop=stop,
            app_type=spec["app_type"],
            scheme=scheme,
            field="vram_mb",
            origin="server",
        )
        write_points_csv(path, pts, "vram_mb")
        print(f"  backfill {path.relative_to(run)} n={len(pts)}")


@dataclass
class SchemeRun:
    short: str
    path: Path
    summary: dict
    samples: List[dict]
    scheme_id: str
    usage_mean: Dict[int, Dict[str, float]] = field(default_factory=dict)
    cost_mean: Dict[int, float] = field(default_factory=dict)
    score_mean: Dict[int, float] = field(default_factory=dict)
    strict_score: float = float("nan")


def load_scheme(*, scheme: str, run: str = "", run_id: str = "") -> SchemeRun:
    out = resolve_run_dir(run=run, scheme=scheme, run_id=run_id)
    if not (out / "summary.json").is_file():
        raise FileNotFoundError(f"missing {out}/summary.json")
    summary = load_summary_json(out / "summary.json")
    samples = load_samples_csv(out / "samples.csv")
    sid = summary.get("scheme") or f"exp4-{_short(scheme)}"
    backfill_vram(out, sid, summary)
    short = _short(scheme)
    usage_mean: Dict[int, Dict[str, float]] = {}
    cost_mean: Dict[int, float] = {}
    for slice_id, spec in SLICES.items():
        if uses_allocated(short):
            row = allocated_usage(short, slice_id)
        else:
            row = {}
            for stem, key, _label, _fmt in COST_FIELDS:
                path = out / "metrics" / spec["app_type"] / f"server_{stem}.csv"
                row[key] = _csv_mean(path, stem)
        usage_mean[slice_id] = row
        cost_mean[slice_id] = slice_cost(short, slice_id, row)
    score_mean, strict_score = mean_violation_scores(samples)
    return SchemeRun(
        short=short,
        path=out,
        summary=summary,
        samples=samples,
        scheme_id=sid,
        usage_mean=usage_mean,
        cost_mean=cost_mean,
        score_mean=score_mean,
        strict_score=strict_score,
    )


def samples_series(samples: List[dict], sid: int) -> Tuple[List[float], List[float], List[float]]:
    xs: List[float] = []
    delays: List[float] = []
    thr: List[float] = []
    t0: Optional[float] = None
    for r in samples:
        try:
            if int(r["slice"]) != sid:
                continue
            t = float(r["t_unix_s"])
            d = float(r["delay_ms"])
            mbps = float(r["throughput_dl_mbps"])
        except (KeyError, TypeError, ValueError):
            continue
        if t0 is None:
            t0 = t
        xs.append(t - t0)
        delays.append(d)
        thr.append(mbps)
    return xs, delays, thr


def write_table_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _grouped_bars(ax, loaded: List[SchemeRun], values_fn, ylabel: str, title: str, fmt: str, log: bool = False) -> None:
    import numpy as np

    sids = sorted(SLICES)
    n_sch = len(loaded)
    width = 0.72 / max(n_sch, 1)
    x = np.arange(len(sids))
    labels = [f"{sid}\n{SLICES[sid]['name']}" for sid in sids]
    ymax = 1.0
    for i, run in enumerate(loaded):
        offset = (i - (n_sch - 1) / 2) * width
        vals = [values_fn(run, sid) for sid in sids]
        color = SCHEME_COLORS.get(run.short, "#444444")
        plot_vals = []
        for v in vals:
            if v != v:
                plot_vals.append(1e-3 if log else 0.0)
            elif log:
                plot_vals.append(max(v, 1e-3))
            else:
                plot_vals.append(v)
        ax.bar(
            x + offset,
            plot_vals,
            width * 0.92,
            color=color,
            edgecolor="#333",
            lw=0.5,
            label=SCHEME_LABELS.get(run.short, run.short.upper()),
            zorder=3,
        )
        for xi, v in zip(x + offset, vals):
            if v == v:
                ax.text(xi, v, fmt.format(v), ha="center", va="bottom", fontsize=6.5, color=color)
                ymax = max(ymax, v)
    ax.set_xticks(x, labels)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if log:
        ax.set_yscale("log")
    else:
        ax.set_ylim(0, ymax * 1.22 if ymax > 0 else 1)
    ax.legend(fontsize=7.5)
    ax.grid(True, axis="y", zorder=0)


def plot_means_sla(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(2, 3, figsize=(16.8, 7.8), dpi=160)
    ax_d, ax_t, ax_c = axes[0]
    ax_v, ax_s, ax_i = axes[1]
    sids = sorted(SLICES)
    n_sch = len(loaded)
    width = 0.72 / max(n_sch, 1)
    x = np.arange(len(sids))
    labels = [
        f"{sid}\n{SLICES[sid]['name']}" + ("*" if SLICES[sid]["strict_sla"] else "")
        for sid in sids
    ]

    def bars(ax, vals_fn, fmt: str, log: bool = False) -> None:
        ymax = 1e-9
        for i, run in enumerate(loaded):
            offset = (i - (n_sch - 1) / 2) * width
            vals = [vals_fn(run, sid) for sid in sids]
            color = SCHEME_COLORS.get(run.short, "#444")
            label = SCHEME_LABELS.get(run.short, run.short.upper())
            plot_vals = [1e-3 if (v != v or v <= 0) and log else (0.0 if v != v else v) for v in vals]
            ax.bar(
                x + offset,
                plot_vals,
                width * 0.92,
                color=color,
                edgecolor="#333",
                lw=0.5,
                label=label,
                zorder=3,
            )
            for xi, v in zip(x + offset, vals):
                if v == v:
                    ax.text(xi, max(v, 1e-3) if log else v, fmt.format(v), ha="center", va="bottom", fontsize=6.5, color=color)
                    ymax = max(ymax, v)
        ax.set_xticks(x, labels)
        ax.legend(fontsize=7, loc="best")
        ax.grid(True, axis="y", zorder=0)
        if log:
            ax.set_yscale("log")
        else:
            ax.set_ylim(0, ymax * 1.22 if ymax > 0 else 1)

    bars(ax_d, lambda run, sid: _mean(_ps(run.summary, sid), "mean_delay_ms"), "{:.0f}", log=True)
    bars(ax_t, lambda run, sid: _mean(_ps(run.summary, sid), "mean_throughput_mbps"), "{:.1f}")
    bars(ax_c, lambda run, sid: run.cost_mean.get(sid, float("nan")), "{:.2f}")
    bars(ax_v, lambda run, sid: _viol_pct(run, sid), "{:.0f}")
    bars(ax_s, lambda run, sid: _viol_score(run, sid), "{:.2f}", log=True)

    ax_d.scatter(x, [float(SLICES[s]["d_bar_ms"]) for s in sids], marker="_", s=280, color="#C44E52", zorder=4, label="D̄")
    ax_t.scatter(x, [float(SLICES[s]["t_bar_mbps"]) for s in sids], marker="_", s=280, color="#2E8B57", zorder=4, label="T̄")
    ax_d.legend(fontsize=7, loc="upper left")
    ax_t.legend(fontsize=7, loc="upper right")
    t_bar = [float(SLICES[sid]["t_bar_mbps"]) for sid in sids]
    ymax_t = max(
        t_bar
        + [
            _mean(_ps(r.summary, sid), "mean_throughput_mbps")
            for r in loaded
            for sid in sids
            if _mean(_ps(r.summary, sid), "mean_throughput_mbps")
            == _mean(_ps(r.summary, sid), "mean_throughput_mbps")
        ]
        + [1]
    )
    ax_t.set_ylim(0, ymax_t * 1.22)
    ax_v.set_ylim(0, 105)
    ax_d.set_ylabel("ms")
    ax_t.set_ylabel("Mbps")
    ax_c.set_ylabel("$/h")
    ax_v.set_ylabel("%")
    ax_s.set_ylabel("score")
    ax_d.set_title("Mean E2E delay")
    ax_t.set_title("Mean DL throughput")
    ax_c.set_title("Mean OPEX ($/h)")
    ax_v.set_title("Violation rate (* = strict)")
    ax_s.set_title("Violation score")

    xi = np.arange(n_sch)
    scores = [run.strict_score for run in loaded]
    colors = [SCHEME_COLORS.get(run.short, "#444") for run in loaded]
    sch_labels = [SCHEME_LABELS.get(run.short, run.short.upper()) for run in loaded]
    ax_i.bar(xi, [0.0 if v != v else v for v in scores], color=colors, edgecolor="#333", lw=0.5, zorder=3)
    ax_i.set_xticks(xi, sch_labels)
    for x0, v in zip(xi, scores):
        if v == v:
            ax_i.text(x0, v, f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    finite = [v for v in scores if v == v]
    ax_i.set_ylabel("score")
    ax_i.set_title("Strict violation score (slices 2/3/5)")
    ax_i.set_ylim(0, (max(finite) if finite else 1) * 1.22)
    ax_i.grid(True, axis="y", zorder=0)

    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 live SLA + OPEX $/h: {names}", fontsize=12)
    fig.tight_layout()
    written: List[Path] = []
    stem = "means_delay_throughput_violation_opex"
    for ext in (".png", ".pdf"):
        p = plots_dir / f"{stem}{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    for ext in (".png", ".pdf"):
        old = plots_dir / f"means_delay_throughput_opex{ext}"
        if old.is_file():
            old.unlink()
    return written


def plot_means_usage(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11.2, 7.2), dpi=160)
    specs = (
        (axes[0][0], "cpu_m", "CPU (millicores)", "{:.0f}"),
        (axes[0][1], "mem_mb", "RAM (MiB)", "{:.0f}"),
        (axes[1][0], "gpu_pct", "GPU (%)", "{:.1f}"),
        (axes[1][1], "vram_mb", "VRAM (MiB)", "{:.0f}"),
    )
    for ax, key, title, fmt in specs:
        _grouped_bars(
            ax,
            loaded,
            lambda run, sid, k=key: run.usage_mean.get(sid, {}).get(k, float("nan")),
            title,
            title,
            fmt,
        )
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 server resources (S0/S1 scheme.py peak; S2/S3 usage): {names}", fontsize=12)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"means_usage{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


def plot_evaluation(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    """One-page: delay, throughput, violation rate/score, CPU, RAM, GPU, VRAM, OPEX."""
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(5, 2, figsize=(11.2, 16.0), dpi=160)
    panels = [
        (axes[0][0], "delay", "E2E delay (ms)", "{:.0f}", True),
        (axes[0][1], "thr", "DL throughput (Mbps)", "{:.1f}", False),
        (axes[1][0], "viol", "Violation rate (%)", "{:.0f}", False),
        (axes[1][1], "score", "Violation score", "{:.2f}", True),
        (axes[2][0], "cpu_m", "CPU (millicores)", "{:.0f}", False),
        (axes[2][1], "mem_mb", "RAM (MiB)", "{:.0f}", False),
        (axes[3][0], "gpu_pct", "GPU (%)", "{:.1f}", False),
        (axes[3][1], "vram_mb", "VRAM (MiB)", "{:.0f}", False),
        (axes[4][0], "cost", "OPEX ($/h)", "{:.2f}", False),
        (axes[4][1], "total", "Total OPEX ($/h)", "{:.2f}", False),
    ]

    def val(run: SchemeRun, sid: int, key: str) -> float:
        if key == "delay":
            return _mean(_ps(run.summary, sid), "mean_delay_ms")
        if key == "thr":
            return _mean(_ps(run.summary, sid), "mean_throughput_mbps")
        if key == "viol":
            return _viol_pct(run, sid)
        if key == "score":
            return _viol_score(run, sid)
        if key == "cost":
            return run.cost_mean.get(sid, float("nan"))
        if key in ("total", "sla"):
            return float("nan")
        return run.usage_mean.get(sid, {}).get(key, float("nan"))

    def _scheme_bars(ax, values: List[float], ylabel: str, title: str, fmt: str) -> None:
        n_sch = len(loaded)
        x = np.arange(n_sch)
        colors = [SCHEME_COLORS.get(run.short, "#444") for run in loaded]
        labels = [SCHEME_LABELS.get(run.short, run.short.upper()) for run in loaded]
        ax.bar(x, values, color=colors, edgecolor="#333", lw=0.5, zorder=3)
        ax.set_xticks(x, labels)
        for xi, v in zip(x, values):
            if v == v:
                ax.text(xi, v, fmt.format(v), ha="center", va="bottom", fontsize=8)
        finite = [v for v in values if v == v]
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if "SLA" in title:
            ax.set_ylim(0, 105)
        else:
            ax.set_ylim(0, (max(finite) if finite else 1) * 1.22)
        ax.grid(True, axis="y", zorder=0)

    for ax, key, title, fmt, log in panels:
        if key == "total":
            totals = [sum(run.cost_mean.get(sid, 0.0) for sid in SLICES) for run in loaded]
            _scheme_bars(ax, totals, "OPEX", title, fmt)
            continue
        if key == "sla":
            slas = [_strict_sla_pct(run) for run in loaded]
            _scheme_bars(ax, slas, "%", title, fmt)
            continue
        _grouped_bars(ax, loaded, lambda run, sid, k=key: val(run, sid, k), title, title, fmt, log=log)
        if key == "viol":
            ax.set_ylim(0, 105)
        if key == "delay":
            sids = sorted(SLICES)
            ax.scatter(
                np.arange(len(sids)),
                [float(SLICES[s]["d_bar_ms"]) for s in sids],
                marker="_",
                s=220,
                color="#C44E52",
                zorder=4,
                label="D̄",
            )
            ax.legend(fontsize=7)
        if key == "thr":
            sids = sorted(SLICES)
            ax.scatter(
                np.arange(len(sids)),
                [float(SLICES[s]["t_bar_mbps"]) for sid in sids for s in [sid]],
                marker="_",
                s=220,
                color="#2E8B57",
                zorder=4,
                label="T̄",
            )
            ax.legend(fontsize=7)
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 evaluation: {names}", fontsize=13)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"evaluation{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


def plot_timeseries_sla(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(5, 2, figsize=(11.2, 10.4), sharex="col", dpi=160)
    for sid in range(1, 6):
        ax_d = axes[sid - 1][0]
        ax_t = axes[sid - 1][1]
        spec = SLICES[sid]
        for run in loaded:
            xs, delays, thr = samples_series(run.samples, sid)
            color = SCHEME_COLORS.get(run.short, "#444")
            label = SCHEME_LABELS.get(run.short, run.short.upper())
            if xs:
                ax_d.plot(xs, delays, color=color, lw=1.05, label=label)
                ax_t.plot(xs, thr, color=color, lw=1.05, label=label)
        ax_d.axhline(spec["d_bar_ms"], color="#C44E52", ls="--", lw=1.0, label=f"D̄={spec['d_bar_ms']:g} ms")
        ax_t.axhline(spec["t_bar_mbps"], color="#2E8B57", ls="--", lw=1.0, label=f"T̄={spec['t_bar_mbps']:g} Mbps")
        ax_d.set_ylabel(f"S{sid} ms")
        ax_t.set_ylabel(f"S{sid} Mbps")
        ax_d.grid(True)
        ax_t.grid(True)
        if sid == 1:
            ax_d.set_title("E2E delay")
            ax_t.set_title("DL throughput")
            ax_d.legend(loc="upper right", fontsize=7, ncol=2)
            ax_t.legend(loc="upper right", fontsize=7, ncol=2)
        if sid == 5:
            ax_d.set_xlabel("time (s)")
            ax_t.set_xlabel("time (s)")
            ax_d.set_yscale("log")
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 timeseries (delay / throughput): {names}", fontsize=12, y=1.01)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"timeseries_throughput_latency{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


def plot_timeseries_usage(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(5, 4, figsize=(13.2, 10.6), sharex="col", dpi=160)
    titles = ("CPU (m)", "RAM (MiB)", "GPU (%)", "VRAM (MiB)")
    stems = ("cpu_m", "mem_mb", "gpu_pct", "vram_mb")
    for sid in range(1, 6):
        spec = SLICES[sid]
        for col, (stem, title) in enumerate(zip(stems, titles)):
            ax = axes[sid - 1][col]
            for run in loaded:
                color = SCHEME_COLORS.get(run.short, "#444")
                label = SCHEME_LABELS.get(run.short, run.short.upper())
                if uses_allocated(run.short):
                    xs, _, _ = samples_series(run.samples, sid)
                    if not xs:
                        path = run.path / "metrics" / spec["app_type"] / f"server_{stem}.csv"
                        xs, _ = load_metric_series(path, stem)
                    cfg = run.usage_mean.get(sid, {})
                    y = cfg.get(stem, float("nan"))
                    if xs and y == y:
                        ax.plot(xs, [y] * len(xs), color=color, lw=0.95, label=label)
                    continue
                path = run.path / "metrics" / spec["app_type"] / f"server_{stem}.csv"
                xs, ys = load_metric_series(path, stem)
                if xs:
                    ax.plot(xs, ys, color=color, lw=0.95, label=label)
            ax.grid(True)
            if sid == 1:
                ax.set_title(title)
                if col == 3:
                    ax.legend(loc="upper right", fontsize=6.5)
            if col == 0:
                ax.set_ylabel(f"S{sid}")
            if sid == 5:
                ax.set_xlabel("time (s)")
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 timeseries (S0/S1 config alloc; S2/S3 usage): {names}", fontsize=12, y=1.01)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"timeseries_usage{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


SHARE_COLORS = {"cpu": "#4C78A8", "ram": "#54A24B", "gpu": "#E45756", "vram": "#F2CF5B"}


def plot_means_cost(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    fig, (ax_c, ax_s) = plt.subplots(1, 2, figsize=(11.2, 4.2), dpi=160)
    _grouped_bars(
        ax_c,
        loaded,
        lambda run, sid: run.cost_mean.get(sid, float("nan")),
        "OPEX ($/h)",
        "Per-slice OPEX ($/h)",
        "{:.2f}",
    )
    sids = sorted(SLICES)
    n_sch = len(loaded)
    width = 0.72 / max(n_sch, 1)
    x = np.arange(len(sids))
    labels = [f"{sid}\n{SLICES[sid]['name']}" for sid in sids]
    keys = ("cpu", "ram", "gpu", "vram")
    ymax = 1e-6
    for i, run in enumerate(loaded):
        offset = (i - (n_sch - 1) / 2) * width
        parts = {k: [] for k in keys}
        for sid in sids:
            bd = cost_breakdown(run.short, sid, run.usage_mean.get(sid, {}))
            for k in keys:
                parts[k].append(bd[k])
        bottom = np.zeros(len(sids))
        for k in keys:
            vals = np.array(parts[k])
            ax_s.bar(
                x + offset,
                vals,
                width * 0.92,
                bottom=bottom,
                color=SHARE_COLORS[k],
                edgecolor="#333",
                lw=0.4,
                label=k.upper() if i == 0 else None,
                zorder=3,
            )
            bottom = bottom + vals
        ymax = max(ymax, float(bottom.max()) if len(bottom) else 0)
    ax_s.set_xticks(x, labels)
    ax_s.set_ylabel("$/h")
    ax_s.set_title("OPEX by resource class")
    ax_s.set_ylim(0, ymax * 1.22)
    ax_s.legend(fontsize=7.5, ncol=4, loc="upper right")
    ax_s.grid(True, axis="y", zorder=0)
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    rho = ", ".join(f"{k[0].upper()}={v:g}" for k, v in SITE_PRICE.items())
    prices = (
        f"cpu={PRICE_CPU_CORE_H:g}/vCPU-h  ram={PRICE_RAM_GB_H:g}/GiB-h  "
        f"gpu={PRICE_GPU_H:g}/GPU-h  vram={PRICE_VRAM_GB_H:g}/GiB-h"
    )
    fig.suptitle(f"Exp4 OPEX $/h ({rho}; {prices}): {names}", fontsize=11)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"means_cost{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


def plot_timeseries_cost(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(5, 1, figsize=(11.2, 10.0), sharex=True, dpi=160)
    for sid in range(1, 6):
        ax = axes[sid - 1]
        spec = SLICES[sid]
        for run in loaded:
            xs, ys = cost_series(run, sid)
            color = SCHEME_COLORS.get(run.short, "#444")
            site = app_site(run.short, sid)
            label = f"{SCHEME_LABELS.get(run.short, run.short.upper())} ({site})"
            if xs:
                ax.plot(xs, ys, color=color, lw=1.05, label=label)
        ax.set_ylabel(f"S{sid} {spec['name']}")
        ax.grid(True)
        if sid == 1:
            ax.set_title("OPEX $/h (S0/S1 peak alloc; S2/S3 PM usage)")
            ax.legend(loc="upper right", fontsize=7, ncol=2)
        if sid == 5:
            ax.set_xlabel("time (s)")
    names = " vs ".join(SCHEME_LABELS.get(r.short, r.short.upper()) for r in loaded)
    fig.suptitle(f"Exp4 timeseries (OPEX): {names}", fontsize=12, y=1.01)
    fig.tight_layout()
    written: List[Path] = []
    for ext in (".png", ".pdf"):
        p = plots_dir / f"timeseries_cost{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)
    return written


def comparison_rows(loaded: List[SchemeRun]) -> List[dict]:
    shorts = [r.short for r in loaded]
    rows: List[dict] = []
    for sid in sorted(SLICES):
        row: Dict[str, object] = {
            "slice": sid,
            "name": SLICES[sid]["name"],
            "d_bar_ms": SLICES[sid]["d_bar_ms"],
            "t_bar_mbps": SLICES[sid]["t_bar_mbps"],
        }
        for run in loaded:
            ps = _ps(run.summary, sid)
            row[f"{run.short}_delay_ms"] = round(_mean(ps, "mean_delay_ms"), 2)
            row[f"{run.short}_thr_mbps"] = round(_mean(ps, "mean_throughput_mbps"), 3)
            row[f"{run.short}_viol_pct"] = round(_viol_pct(run, sid), 2)
            sc = run.score_mean.get(sid, float("nan"))
            row[f"{run.short}_viol_score"] = round(sc, 4) if sc == sc else None
            cm = run.usage_mean.get(sid, {})
            row[f"{run.short}_cpu_m"] = round(cm.get("cpu_m", float("nan")), 1)
            row[f"{run.short}_mem_mb"] = round(cm.get("mem_mb", float("nan")), 1)
            row[f"{run.short}_gpu_pct"] = round(cm.get("gpu_pct", float("nan")), 2)
            row[f"{run.short}_vram_mb"] = round(cm.get("vram_mb", float("nan")), 1)
            al = allocated_usage(run.short, sid)
            row[f"{run.short}_alloc_cpu_m"] = round(al["cpu_m"], 1)
            row[f"{run.short}_alloc_mem_mb"] = round(al["mem_mb"], 1)
            row[f"{run.short}_alloc_gpu_pct"] = round(al["gpu_pct"], 1)
            row[f"{run.short}_alloc_vram_mb"] = round(al["vram_mb"], 1)
            row[f"{run.short}_site"] = app_site(run.short, sid)
            row[f"{run.short}_rho"] = SITE_PRICE[app_site(run.short, sid)]
            row[f"{run.short}_cost"] = round(run.cost_mean.get(sid, float("nan")), 4)
        pairs = list(zip(shorts, shorts[1:]))
        if ("s0", "s1") in pairs:

            def delta(key: str, nd: int = 2) -> None:
                a = row[f"s0_{key}"]
                b = row[f"s1_{key}"]
                if a is None or b is None:
                    row[f"delta_{key}"] = None
                    return
                row[f"delta_{key}"] = round(float(b) - float(a), nd)

            delta("delay_ms")
            delta("thr_mbps", 3)
            delta("viol_pct", 2)
            delta("viol_score", 4)
            delta("cpu_m", 1)
            delta("mem_mb", 1)
            delta("gpu_pct", 2)
            delta("vram_mb", 1)
            delta("cost", 4)
        for a, b in pairs:
            if (a, b) == ("s0", "s1"):
                continue

            def delta_ab(key: str, nd: int = 2, left: str = a, right: str = b) -> None:
                va = row[f"{left}_{key}"]
                vb = row[f"{right}_{key}"]
                if va is None or vb is None:
                    row[f"delta_{left}_{right}_{key}"] = None
                    return
                row[f"delta_{left}_{right}_{key}"] = round(float(vb) - float(va), nd)

            delta_ab("delay_ms")
            delta_ab("thr_mbps", 3)
            delta_ab("viol_pct", 2)
            delta_ab("viol_score", 4)
            delta_ab("cpu_m", 1)
            delta_ab("mem_mb", 1)
            delta_ab("gpu_pct", 2)
            delta_ab("vram_mb", 1)
            delta_ab("cost", 4)
        rows.append(row)
    return rows


def print_table(rows: List[dict], loaded: List[SchemeRun]) -> None:
    shorts = [r.short for r in loaded]
    print()
    print("SLA (delay / throughput / violation rate / violation score)")
    hdr = f"{'SLICE':<6} {'NAME':<10}"
    for s in shorts:
        hdr += f" {s.upper()+' d ms':>11} {s.upper()+' Mbps':>10} {s.upper()+' viol%':>10} {s.upper()+' score':>10}"
    print(hdr)
    for r in rows:
        line = f"{r['slice']:<6} {str(r['name']):<10}"
        for s in shorts:
            vp = r.get(f"{s}_viol_pct")
            sc = r.get(f"{s}_viol_score")
            vp_s = f"{float(vp):10.1f}" if vp is not None else f"{'—':>10}"
            sc_s = f"{float(sc):10.2f}" if sc is not None else f"{'—':>10}"
            line += f" {r[f'{s}_delay_ms']:11.1f} {r[f'{s}_thr_mbps']:10.2f}{vp_s}{sc_s}"
        print(line)
    print()
    print("Server metrics (CPU millicores / RAM MiB / GPU % / VRAM MiB)")
    hdr = f"{'SLICE':<6} {'NAME':<10}"
    for s in shorts:
        hdr += f" {s.upper()+' CPU':>10} {s.upper()+' RAM':>10} {s.upper()+' GPU%':>9} {s.upper()+' VRAM':>10}"
    print(hdr)
    for r in rows:
        line = f"{r['slice']:<6} {str(r['name']):<10}"
        for s in shorts:
            line += (
                f" {r[f'{s}_cpu_m']:10.0f} {r[f'{s}_mem_mb']:10.0f}"
                f" {r[f'{s}_gpu_pct']:9.1f} {r[f'{s}_vram_mb']:10.0f}"
            )
        print(line)
    print()
    print("OPEX $/h: S0/S1 peak allocation; S2/S3 CPU/RAM usage (capped at S1 peak); GPU at S1 alloc")
    print(
        f"      C = ρ · ( {PRICE_CPU_CORE_H:g}·vCPU + {PRICE_RAM_GB_H:g}·GiB RAM"
        f" + {PRICE_GPU_H:g}·GPU + {PRICE_VRAM_GB_H:g}·GiB VRAM )"
    )
    print("      ρ: central=1  regional=2  edge=4")
    hdr = f"{'SLICE':<6} {'NAME':<10}"
    for s in shorts:
        hdr += f" {s.upper()+' CPU':>8} {s.upper()+' RAM':>8} {s.upper()+' C':>8}"
    print(hdr)
    for r in rows:
        line = f"{r['slice']:<6} {str(r['name']):<10}"
        for s in shorts:
            line += (
                f" {r[f'{s}_alloc_cpu_m']:8.0f} {r[f'{s}_alloc_mem_mb']:8.0f}"
                f" {r[f'{s}_cost']:8.3f}"
            )
        print(line)
    print()
    tot_hdr = "  total OPEX $/h "
    for run in loaded:
        tot = sum(run.cost_mean.get(sid, 0.0) for sid in SLICES)
        tot_hdr += f"  {SCHEME_LABELS.get(run.short, run.short)}={tot:.3f}"
    print(tot_hdr)
    print()
    for run in loaded:
        sla = _strict_sla_pct(run)
        sla_s = f"{sla:.2f}%" if sla == sla else "n/a"
        sc = run.strict_score
        sc_s = f"{sc:.2f}" if sc == sc else "n/a"
        print(
            f"  {SCHEME_LABELS.get(run.short, run.short)}  "
            f"strict SLA viol={sla_s}  score={sc_s}  run={run.path.name}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--schemes", nargs="+", default=["s0", "s1", "s2"], help="scheme ids (default: s0 s1 s2)")
    p.add_argument("--s0-run-id", default="", help="folder under s0/data/")
    p.add_argument("--s1-run-id", default="", help="folder under s1/data/")
    p.add_argument("--s2-run-id", default="", help="folder under s2/data/")
    p.add_argument("--s3-run-id", default="", help="folder under s3/data/")
    p.add_argument("--s0-run", default="", help="full path to S0 run dir")
    p.add_argument("--s1-run", default="", help="full path to S1 run dir")
    p.add_argument("--s2-run", default="", help="full path to S2 run dir")
    p.add_argument("--s3-run", default="", help="full path to S3 run dir")
    p.add_argument("--out", default="", help="output directory (default: paper/exp4/plots/<a>_vs_<b>)")
    args = p.parse_args(argv)

    schemes = [_short(s) for s in args.schemes]
    run_id_for = {
        "s0": args.s0_run_id,
        "s1": args.s1_run_id,
        "s2": args.s2_run_id,
        "s3": args.s3_run_id,
    }
    run_for = {
        "s0": args.s0_run,
        "s1": args.s1_run,
        "s2": args.s2_run,
        "s3": args.s3_run,
    }

    loaded: List[SchemeRun] = []
    try:
        for sch in schemes:
            loaded.append(load_scheme(scheme=sch, run=run_for.get(sch, ""), run_id=run_id_for.get(sch, "")))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    stem = "_vs_".join(r.short for r in loaded)
    if args.out:
        plots_dir = Path(args.out).expanduser()
        if not plots_dir.is_absolute():
            plots_dir = (HERE / plots_dir).resolve()
    else:
        plots_dir = (HERE / "plots" / stem).resolve()
    plots_dir.mkdir(parents=True, exist_ok=True)

    try:
        import matplotlib

        matplotlib.use("Agg")
    except ImportError as exc:
        print(f"error: matplotlib required ({exc})", file=sys.stderr)
        return 1

    rows = comparison_rows(loaded)
    write_table_csv(plots_dir / "means.csv", rows)
    print_table(rows, loaded)

    print(f"Plotting → {plots_dir}")
    written = []
    written += plot_evaluation(loaded, plots_dir)
    written += plot_means_sla(loaded, plots_dir)
    written += plot_means_usage(loaded, plots_dir)
    written += plot_means_cost(loaded, plots_dir)
    written += plot_timeseries_sla(loaded, plots_dir)
    written += plot_timeseries_usage(loaded, plots_dir)
    written += plot_timeseries_cost(loaded, plots_dir)
    for path in written:
        print(f"  wrote {path}")
    print(f"  wrote {plots_dir / 'means.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
