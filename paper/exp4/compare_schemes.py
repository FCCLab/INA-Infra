#!/usr/bin/env python3
"""Compare live Exp4 runs: delay, throughput, violation, server usage, and OPEX.

Server CPU/RAM/GPU/VRAM: S0/S1 from scheme.py peak requests; S2/S3 from
Influx. OPEX is $ / hour: central list prices × site ρ (edge=4,
regional=2, central=1). See ``paper/exp4/cost_model.py``.

Headline SLA axis: strict-slice (2, 3, 5) mean violation score residual
(% of S0). Score matches paper (6-9)/(6-10) absolute slacks:
s = w_D·max(0, D_s−D^s) + w_T·max(0, T^s−T_s), with tunable
SCORE_W_* / SLICES bars in ``exp_start`` (not SX isolated means).
Binary miss rate (D_s > D^s or T_s ≤ T^s) is reported but not the journal waterfall.

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
from exp_start import SLICES, parse_rfc3339, query_series, rate_meets_sla, sla_weight, violation_score, write_points_csv  # noqa: E402

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
    Strict index is a ``sla_weight``-weighted mean over strict-slice samples.
    """
    sums: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    strict_sum = 0.0
    strict_w = 0.0
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
            w = sla_weight(sid)
            strict_sum += w * score
            strict_w += w
    per_slice = {sid: sums[sid] / counts[sid] for sid in sums if counts[sid]}
    strict = (strict_sum / strict_w) if strict_w else float("nan")
    return per_slice, strict


def violation_pct_from_samples(samples: List[dict], sid: int) -> float:
    """Percent of samples that miss current D̄ or T > T̄."""
    spec = SLICES[sid]
    d_bar = float(spec["d_bar_ms"])
    t_bar = float(spec["t_bar_mbps"])
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
        if delay > d_bar or not rate_meets_sla(rate, t_bar):
            v += 1
    if not n:
        return float("nan")
    return 100.0 * v / n


def _strict_sla_pct_from_samples(samples: List[dict]) -> float:
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
        if delay > float(spec["d_bar_ms"]) or not rate_meets_sla(rate, float(spec["t_bar_mbps"])):
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


def _draw_journal_2x2(fig, m: dict) -> None:
    """Paper Fig 4 journal board: SLA waterfall, OPEX waterfall, thr/eff, Pareto."""
    sla, opex, thr, eff = m["sla"], m["opex"], m["thr"], m["eff"]
    labels_short = ["S0 Static", "S1 +PL", "S2 +PL +PM", "S3 +PL +PM +PS"]
    xs = list(range(4))
    w = 0.36
    gs = fig.add_gridspec(2, 2, hspace=0.38, wspace=0.28)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])
    _waterfall_bars(ax_a, sla, "SLA violation score residual (% of S0)", "A. SLA violation reduction")
    _waterfall_bars(ax_b, opex, "OPEX residual (% of S0)", "B. OPEX reduction")
    ax_c.bar([i - w / 2 for i in xs], thr, w, label="Throughput", color="#4C78A8", edgecolor="#333", lw=0.5)
    ax_c.bar([i + w / 2 for i in xs], eff, w, label="Efficiency", color="#54A24B", edgecolor="#333", lw=0.5)
    ax_c.axhline(100.0, color="#888", ls=":", lw=0.8)
    ax_c.set_xticks(xs, labels_short, fontsize=8)
    ax_c.set_ylabel("Normalized to S0 (%)", fontweight="bold")
    ax_c.set_title("C. Throughput and resource efficiency", fontweight="bold")
    ax_c.legend(fontsize=7.5)
    ax_c.yaxis.grid(True)
    ax_c.set_axisbelow(True)
    ax_d.plot(opex, sla, color="#888", lw=1.2, ls="--")
    for sid, x, y in zip(("s0", "s1", "s2", "s3"), opex, sla):
        ax_d.scatter(x, y, s=80, color=SCHEME_COLORS[sid], edgecolor="#222", lw=0.6, zorder=3)
        ax_d.annotate(
            sid.upper(), (x, y), textcoords="offset points", xytext=(6, 5),
            fontsize=8, fontweight="bold", color=SCHEME_COLORS[sid],
        )
    ax_d.set_xlabel("OPEX (% of S0)", fontweight="bold")
    ax_d.set_ylabel("SLA violation score (% of S0)", fontweight="bold")
    ax_d.set_title("D. Joint SLA–OPEX Pareto walk", fontweight="bold")
    ax_d.grid(True)
    fig.suptitle("Experiment 4: Synergy of PL + PM + PS  (S0 = 100%)", fontsize=13, fontweight="bold", y=0.98)


def plot_means_sla(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    """Six-panel live comparison: delay, throughput, OPEX, viol %, score, SLA residual."""
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
    # Finer labeled log ticks so ~100–400 ms differences are readable (MQTT still needs log).
    from matplotlib.ticker import FuncFormatter, LogLocator

    delay_vals = [
        _mean(_ps(r.summary, sid), "mean_delay_ms")
        for r in loaded
        for sid in sids
        if _mean(_ps(r.summary, sid), "mean_delay_ms")
        == _mean(_ps(r.summary, sid), "mean_delay_ms")
    ]
    d_bar = [float(SLICES[s]["d_bar_ms"]) for s in sids]
    d_lo = max(50.0, 0.8 * min(delay_vals + d_bar + [100.0]))
    d_hi = 1.35 * max(delay_vals + d_bar + [1000.0])
    ax_d.set_ylim(d_lo, d_hi)
    ax_d.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 1.5, 2.0, 3.0, 5.0, 7.0)))
    ax_d.yaxis.set_minor_locator(LogLocator(base=10, subs="auto"))
    ax_d.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:g}"))
    ax_d.tick_params(axis="y", which="major", labelsize=7.5)
    ax_d.grid(True, axis="y", which="both", zorder=0, alpha=0.45)
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
    s0_score = next((v for run, v in zip(loaded, scores) if run.short == "s0"), float("nan"))
    if s0_score == s0_score and s0_score > 0:
        scores = [100.0 * v / s0_score if v == v else float("nan") for v in scores]
        ax_i.set_ylabel("% of S0")
        ax_i.set_title("Strict violation score (% of S0)")
        score_fmt = "{:.1f}"
        ax_i.axhline(100.0, color="#BBBBBB", lw=0.6, ls=":", zorder=1)
    else:
        ax_i.set_ylabel("score")
        ax_i.set_title("Strict violation score (slices 2/3/5)")
        score_fmt = "{:.2f}"
    colors = [SCHEME_COLORS.get(run.short, "#444") for run in loaded]
    sch_labels = [SCHEME_LABELS.get(run.short, run.short.upper()) for run in loaded]
    ax_i.bar(xi, [0.0 if v != v else v for v in scores], color=colors, edgecolor="#333", lw=0.5, zorder=3)
    ax_i.set_xticks(xi, sch_labels)
    for x0, v in zip(xi, scores):
        if v == v:
            ax_i.text(x0, v, score_fmt.format(v), ha="center", va="bottom", fontsize=8)
    finite = [v for v in scores if v == v]
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


DELTA_COLORS = ("#1F77B4", "#E67E22", "#2E8B57")


def _total_throughput_mbps(run: SchemeRun) -> float:
    tot = 0.0
    for sid in SLICES:
        v = _mean(_ps(run.summary, sid), "mean_throughput_mbps")
        if v == v:
            tot += v
    return tot


def _scheme_metrics(loaded: List[SchemeRun]) -> Optional[dict]:
    """Residuals: strict violation SCORE, OPEX, throughput, efficiency. S0 = 100%."""
    by = {r.short: r for r in loaded}
    if any(k not in by for k in ("s0", "s1", "s2", "s3")):
        return None
    ordered = [by[k] for k in ("s0", "s1", "s2", "s3")]
    score_raw = [r.strict_score for r in ordered]
    opex_raw = [sum(r.cost_mean.get(sid, 0.0) for sid in SLICES) for r in ordered]
    thr_raw = [_total_throughput_mbps(r) for r in ordered]
    if not (score_raw[0] == score_raw[0] and score_raw[0] > 0 and opex_raw[0] > 0 and thr_raw[0] > 0):
        return None
    eff_raw = [t / max(o, 1e-9) for t, o in zip(thr_raw, opex_raw)]
    return {
        "ordered": ordered,
        "sla": [100.0 * v / score_raw[0] for v in score_raw],
        "opex": [100.0 * v / opex_raw[0] for v in opex_raw],
        "thr": [100.0 * v / thr_raw[0] for v in thr_raw],
        "eff": [100.0 * v / eff_raw[0] for v in eff_raw],
        "sla_raw": [_strict_sla_pct(r) for r in ordered],
        "opex_raw": opex_raw,
        "thr_raw": thr_raw,
        "score_raw": score_raw,
    }


def _waterfall_bars(ax, residuals: List[float], ylabel: str, title: str) -> None:
    """McKinsey-style waterfall: S0 total, three floating layer bars, S3 total."""
    import matplotlib.patches as mpatches
    import numpy as np

    s0, s1, s2, s3 = residuals
    running = [s0, s1, s2, s3]
    deltas = [s1 - s0, s2 - s1, s3 - s2]
    bottoms = [0.0]
    heights = [s0]
    colors = [SCHEME_COLORS["s0"]]
    for i, d in enumerate(deltas):
        colors.append(DELTA_COLORS[i])
        if d <= 0:
            bottoms.append(running[i + 1])
            heights.append(-d)
        else:
            bottoms.append(running[i])
            heights.append(d)
    bottoms.append(0.0)
    heights.append(s3)
    colors.append(SCHEME_COLORS["s3"])
    labels = ["S0\nStatic", "+ PL", "+ PM", "+ PS", "S3\nFull"]
    xs = np.arange(5)
    ax.bar(
        xs, heights, bottom=bottoms, color=colors, width=0.62,
        edgecolor="#333333", linewidth=0.6, zorder=3,
    )
    for i in range(3):
        y = running[i]
        ax.plot([i + 0.31, i + 1 - 0.31], [y, y], color="#555555", lw=0.8, ls="--", zorder=2)
    ax.plot([3 + 0.31, 4 - 0.31], [s3, s3], color="#555555", lw=0.8, ls="--", zorder=2)
    for i, (h, b, d) in enumerate(zip(heights, bottoms, [None, *deltas, None])):
        y = b + h
        if i == 0 or i == 4:
            ax.text(i, y + 2.2, f"{h:.1f}%", ha="center", va="bottom", fontsize=9, fontweight="bold")
        elif d is not None and d <= 0:
            ax.text(i, y + 2.2, f"−{h:.1f} pp", ha="center", va="bottom", fontsize=8, color=colors[i], fontweight="bold")
        else:
            ax.text(i, y + 2.2, f"+{h:.1f} pp", ha="center", va="bottom", fontsize=8, color=colors[i], fontweight="bold")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel, fontweight="bold")
    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylim(0, max(residuals + [100.0]) * 1.18)
    ax.axhline(100.0, color="#BBBBBB", lw=0.6, ls=":", zorder=1)
    ax.yaxis.grid(True, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(
        handles=[
            mpatches.Patch(color=SCHEME_COLORS["s0"], label="S0 residual"),
            mpatches.Patch(color=DELTA_COLORS[0], label="PL contribution"),
            mpatches.Patch(color=DELTA_COLORS[1], label="PM contribution"),
            mpatches.Patch(color=DELTA_COLORS[2], label="PS contribution"),
            mpatches.Patch(color=SCHEME_COLORS["s3"], label="S3 residual"),
        ],
        loc="upper right",
        fontsize=7.5,
        framealpha=0.92,
    )


def _save_paper_fig(fig, plots_dir: Path, stem: str, written: List[Path], dpi: int = 300) -> None:
    fig.tight_layout()
    for ext in (".png", ".pdf"):
        p = plots_dir / f"{stem}{ext}"
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        written.append(p)


def _pp_delta(prev: float, nxt: float) -> str:
    d = prev - nxt
    if d >= 0:
        return f"−{d:.1f}"
    return f"+{-d:.1f}"


def plot_paper_style(loaded: List[SchemeRun], plots_dir: Path) -> List[Path]:
    """Journal Fig 4A–4E from live captures. SLA axis = strict violation score residual, S0 = 100%."""
    m = _scheme_metrics(loaded)
    if m is None:
        return []
    import matplotlib.pyplot as plt

    sla, opex, thr, eff = m["sla"], m["opex"], m["thr"], m["eff"]
    labels_short = ["S0 Static", "S1 +PL", "S2 +PL +PM", "S3 +PL +PM +PS"]
    written: List[Path] = []
    dpi = 300

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=dpi)
    _waterfall_bars(ax, sla, "SLA violation score residual (% of S0)", "Figure 4A: SLA Violation Reduction (layer waterfall)")
    _save_paper_fig(fig, plots_dir, "fig4a_sla_waterfall", written, dpi)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=dpi)
    _waterfall_bars(ax, opex, "OPEX residual (% of S0)", "Figure 4B: Network OPEX Reduction (layer waterfall)")
    _save_paper_fig(fig, plots_dir, "fig4b_opex_waterfall", written, dpi)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=dpi)
    xs = list(range(4))
    w = 0.36
    ax.bar([i - w / 2 for i in xs], thr, w, label="Useful throughput", color="#4C78A8", edgecolor="#333", lw=0.5)
    ax.bar([i + w / 2 for i in xs], eff, w, label="Resource efficiency (Mbps / $)", color="#54A24B", edgecolor="#333", lw=0.5)
    ax.axhline(100.0, color="#888", ls=":", lw=0.8, label="S0 = 100%")
    for i, (t, e) in enumerate(zip(thr, eff)):
        ax.text(i - w / 2, t + 1.5, f"{t:.0f}", ha="center", fontsize=8)
        ax.text(i + w / 2, e + 1.5, f"{e:.0f}", ha="center", fontsize=8)
    ax.set_xticks(xs, labels_short, fontsize=8)
    ax.set_ylabel("Normalized to S0 (%)", fontweight="bold")
    ax.set_title("Figure 4C: Throughput and Resource Efficiency", fontweight="bold", pad=10)
    ax.legend(fontsize=8, framealpha=0.92)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.set_ylim(0, max(thr + eff + [100.0]) * 1.16)
    _save_paper_fig(fig, plots_dir, "fig4c_throughput_efficiency", written, dpi)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.4, 3.6), dpi=dpi)
    ax.axis("off")
    ax.set_title("Figure 4D: Control-Layer Contribution", fontweight="bold", pad=8)
    cells = [
        ["Control layer", "Primary benefit", "What moves", "SLA Δ (pp of S0)", "OPEX Δ (pp of S0)"],
        ["PL", "Optimal placement (where)", "OPEX, E2E latency", _pp_delta(sla[0], sla[1]), _pp_delta(opex[0], opex[1])],
        ["PM", "Elastic compute (how much)", "CPU/GPU util., queue backlog", _pp_delta(sla[1], sla[2]), _pp_delta(opex[1], opex[2])],
        ["PS", "Channel adaptation (how radio)", "SLA violation, throughput", _pp_delta(sla[2], sla[3]), _pp_delta(opex[2], opex[3])],
        ["PL + PM + PS", "End-to-end multi-timescale", "SLA–OPEX Pareto frontier",
         f"{_pp_delta(sla[0], sla[3])} total", f"{_pp_delta(opex[0], opex[3])} total"],
    ]
    table = ax.table(cellText=cells, loc="center", cellLoc="left", colWidths=[0.18, 0.28, 0.24, 0.16, 0.16])
    table.auto_set_font_size(False)
    table.set_fontsize(8.2)
    table.scale(1.0, 1.85)
    header_color = "#2C3E50"
    row_colors = ["#F4F6F7", "#EAF2F8", "#FEF5E7", "#E8F8F5", "#EBF5FB"]
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        cell.set_linewidth(0.4)
        if r == 0:
            cell.set_facecolor(header_color)
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor(row_colors[r - 1])
            if c >= 3:
                cell.set_text_props(fontweight="bold", ha="center")
                cell._loc = "center"
    _save_paper_fig(fig, plots_dir, "fig4d_layer_contribution", written, dpi)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=dpi)
    ax.plot(opex, sla, color="#888888", lw=1.2, ls="--", zorder=2)
    for sid, x, y in zip(("s0", "s1", "s2", "s3"), opex, sla):
        ax.scatter(x, y, s=90, color=SCHEME_COLORS[sid], zorder=3, edgecolor="#222", linewidth=0.6)
        ax.annotate(
            SCHEME_LABELS[sid],
            (x, y),
            textcoords="offset points",
            xytext=(8, 6),
            fontsize=8,
            fontweight="bold",
            color=SCHEME_COLORS[sid],
        )
    ax.set_xlabel("OPEX (% of S0)", fontweight="bold")
    ax.set_ylabel("SLA violation score (% of S0)", fontweight="bold")
    ax.set_title("Figure 4E: SLA–OPEX Pareto Walk (S0 → S3)", fontweight="bold", pad=10)
    ax.grid(True)
    ax.set_xlim(min(opex) * 0.85, max(opex) * 1.08)
    ax.set_ylim(-2, max(sla) * 1.12)
    _save_paper_fig(fig, plots_dir, "fig4e_sla_opex_pareto", written, dpi)
    plt.close(fig)

    fig = plt.figure(figsize=(11.2, 8.4), dpi=dpi)
    _draw_journal_2x2(fig, m)
    for ext in (".png", ".pdf"):
        p = plots_dir / f"fig4_journal_summary{ext}"
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        written.append(p)
    plt.close(fig)

    ordered = m["ordered"]
    wf_rows = []
    for i, (sid, lab) in enumerate(zip(("s0", "s1", "s2", "s3"), labels_short)):
        wf_rows.append({
            "scheme": sid.upper(),
            "label": lab,
            "sla_violation_rate": round(m["sla_raw"][i] / 100.0, 6),
            "sla_residual_pct": round(sla[i], 4),
            "opex": round(m["opex_raw"][i], 6),
            "opex_residual_pct": round(opex[i], 4),
            "throughput_mbps": round(m["thr_raw"][i], 6),
            "throughput_pct": round(thr[i], 4),
            "efficiency_pct": round(eff[i], 4),
            "strict_score": round(m["score_raw"][i], 6) if m["score_raw"][i] == m["score_raw"][i] else None,
        })
    write_table_csv(plots_dir / "waterfall.csv", wf_rows)
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
        (axes[1][1], "sla", "SLA violation (% of S0)", "{:.1f}", False),
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
            s0 = next((_strict_sla_pct(r) for r in loaded if r.short == "s0"), float("nan"))
            slas = [_strict_sla_pct(run) for run in loaded]
            if s0 == s0 and s0 > 0:
                slas = [100.0 * v / s0 if v == v else float("nan") for v in slas]
            _scheme_bars(ax, slas, "% of S0", title, fmt)
            ax.axhline(100.0, color="#BBBBBB", lw=0.6, ls=":", zorder=1)
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
    s0_score = next((r.strict_score for r in loaded if r.short == "s0"), float("nan"))
    s0_opex = next(
        (sum(r.cost_mean.get(sid, 0.0) for sid in SLICES) for r in loaded if r.short == "s0"),
        float("nan"),
    )
    for run in loaded:
        sla = _strict_sla_pct(run)
        sla_s = f"{sla:.2f}%" if sla == sla else "n/a"
        sc = run.strict_score
        sc_s = f"{sc:.2f}" if sc == sc else "n/a"
        residual = ""
        if s0_score == s0_score and s0_score > 0 and sc == sc:
            residual = f"  score residual={100.0 * sc / s0_score:.1f}% of S0"
        opex = sum(run.cost_mean.get(sid, 0.0) for sid in SLICES)
        if s0_opex == s0_opex and s0_opex > 0:
            residual += f"  OPEX residual={100.0 * opex / s0_opex:.1f}% of S0"
        print(
            f"  {SCHEME_LABELS.get(run.short, run.short)}  "
            f"strict SLA viol={sla_s}  score={sc_s}{residual}  run={run.path.name}"
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
    written += plot_paper_style(loaded, plots_dir)
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
