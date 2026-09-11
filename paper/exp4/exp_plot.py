#!/usr/bin/env python3
"""Plot an Exp4 live experiment run produced by ``exp_start.py``.

Reads ``samples.csv`` + ``summary.json`` under a run directory and writes
PNG/PDF under ``<run>/plots/``:

  violation_rates.*   per-slice violation % (+ strict index line)
  timeseries_sla.*    delay & throughput vs D̄ / T̄ (5 slices)
  means_vs_sla.*      mean delay/thr vs budgets

Usage:
  python3 paper/exp4/exp_plot.py                  # latest run under s0/data/
  python3 paper/exp4/exp_plot.py --scheme s1      # latest under s1/data/
  python3 paper/exp4/exp_plot.py --scheme s0 --run-id 20260908-234232_300s
  python3 paper/exp4/exp_plot.py --run paper/exp4/s0/data/smoke-test
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from exp_start import RATE_FLOOR, SLICES, data_dir, scheme_id  # noqa: E402

SLICE_COLORS = {
    1: "#4C72B0",
    2: "#DD8452",
    3: "#55A868",
    4: "#C44E52",
    5: "#8172B3",
}


def load_samples_csv(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_summary_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def latest_run_dir(scheme: str = "s0") -> Path:
    """Newest run dir under ``paper/exp4/<scheme>/data/`` that has ``summary.json``."""
    root = data_dir(scheme_id(scheme))
    if not root.is_dir():
        raise ValueError(f"no data directory: {root}")
    candidates: List[tuple[float, Path]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        summary = child / "summary.json"
        if not summary.is_file():
            continue
        try:
            mtime = summary.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, child))
    if not candidates:
        raise ValueError(f"no runs with summary.json under {root}")
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].resolve()


def resolve_run_dir(
    *,
    run: str = "",
    scheme: str = "",
    run_id: str = "",
) -> Path:
    if run.strip():
        p = Path(run).expanduser()
        if not p.is_absolute():
            # Prefer cwd-relative, else under paper/exp4/
            cand = Path.cwd() / p
            p = cand if cand.exists() else (HERE / p)
        return p.resolve()
    sch = scheme.strip() or "s0"
    if run_id.strip():
        return (data_dir(scheme_id(sch)) / run_id.strip()).resolve()
    return latest_run_dir(sch)


def plot_run(
    out: Path,
    samples: Optional[List[dict]] = None,
    summary: Optional[dict] = None,
) -> List[Path]:
    """Write PNG(+PDF) figures under ``out/plots/`` from samples + summary."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib required for plots (pip install matplotlib)") from exc

    out = Path(out)
    if samples is None:
        samples = load_samples_csv(out / "samples.csv")
    if summary is None:
        summary_path = out / "summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"missing {summary_path}")
        summary = load_summary_json(summary_path)

    plots_dir = out / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "axes.edgecolor": "#333333",
            "axes.linewidth": 0.8,
            "grid.color": "#E0E0E0",
            "grid.linestyle": "--",
            "grid.alpha": 0.7,
        }
    )

    scheme = summary.get("scheme") or out.parent.name

    def _ps(sid: int) -> dict:
        ps = summary.get("per_slice") or {}
        row = ps.get(sid)
        if row is None:
            row = ps.get(str(sid))
        return row or {}

    # --- 1) Violation rates bar ---
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    sids = sorted({int(k) for k in (summary.get("per_slice") or {})})
    names: List[str] = []
    rates: List[float] = []
    colors: List[str] = []
    for sid in sids:
        row = _ps(sid)
        names.append(f"S{sid}\n{row.get('name', '')}")
        vr = row.get("violation_rate")
        rates.append(100.0 * float(vr) if vr is not None else 0.0)
        colors.append(SLICE_COLORS.get(sid, "#888888"))
        if row.get("strict_sla"):
            names[-1] = names[-1] + "*"
    ax.bar(names, rates, color=colors, edgecolor="#333333", linewidth=0.6, zorder=3)
    ax.set_ylabel("Violation rate (%)")
    ax.set_ylim(0, 105)
    ax.set_title(f"{scheme}: per-slice SLA violation (* = strict index)")
    ax.grid(True, axis="y", zorder=0)
    sla = summary.get("sla_violation_rate")
    if sla is not None:
        ax.axhline(
            100.0 * float(sla),
            color="#222222",
            ls=":",
            lw=1.2,
            label=f"strict index {100 * float(sla):.1f}%",
        )
        ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    for ext in (".png", ".pdf"):
        p = plots_dir / f"violation_rates{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)

    # --- 2) Timeseries: delay + throughput (5 rows) ---
    by_slice: Dict[int, List[dict]] = {sid: [] for sid in SLICES}
    for row in samples:
        try:
            sid = int(row["slice"])
        except (KeyError, TypeError, ValueError):
            continue
        by_slice.setdefault(sid, []).append(row)

    fig, axes = plt.subplots(5, 2, figsize=(11, 10), sharex="col")
    t0 = None
    for sid in range(1, 6):
        rows = by_slice.get(sid) or []
        xs: List[float] = []
        delays: List[float] = []
        thr: List[float] = []
        for r in rows:
            try:
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
        spec = SLICES[sid]
        ax_d = axes[sid - 1][0]
        ax_t = axes[sid - 1][1]
        c = SLICE_COLORS.get(sid, "#444")
        if xs:
            ax_d.plot(xs, delays, color=c, lw=1.0, label="delay")
            ax_t.plot(xs, thr, color=c, lw=1.0, label="goodput")
        ax_d.axhline(
            spec["d_bar_ms"],
            color="#C44E52",
            ls="--",
            lw=1.0,
            label=f"D̄={spec['d_bar_ms']:g} ms",
        )
        ax_d.set_ylabel(f"S{sid} ms")
        ax_d.grid(True)
        if sid == 1:
            ax_d.set_title("E2E delay")
            ax_d.legend(loc="upper right", fontsize=7)
        ax_t.axhline(
            spec["t_bar_mbps"],
            color="#55A868",
            ls="--",
            lw=1.0,
            label=f"T̄={spec['t_bar_mbps']:g} Mbps",
        )
        ax_t.axhline(
            RATE_FLOOR * spec["t_bar_mbps"],
            color="#55A868",
            ls=":",
            lw=0.9,
            label="0.95·T̄",
        )
        ax_t.set_ylabel(f"S{sid} Mbps")
        ax_t.grid(True)
        if sid == 1:
            ax_t.set_title("DL throughput")
            ax_t.legend(loc="upper right", fontsize=7)
        if sid == 5:
            ax_d.set_xlabel("time (s)")
            ax_t.set_xlabel("time (s)")
    fig.suptitle(f"{scheme}: delay & throughput vs SLA budgets", fontsize=12, y=1.01)
    fig.tight_layout()
    for ext in (".png", ".pdf"):
        p = plots_dir / f"timeseries_sla{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)

    # --- 3) Mean delay / thr vs budget ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 3.6))
    x = list(range(len(sids)))
    mean_d: List[float] = []
    mean_t: List[float] = []
    d_bar: List[float] = []
    t_bar: List[float] = []
    labels: List[str] = []
    for sid in sids:
        row = _ps(sid)
        labels.append(f"S{sid}")
        mean_d.append(
            float(row["mean_delay_ms"]) if row.get("mean_delay_ms") is not None else float("nan")
        )
        mean_t.append(
            float(row["mean_throughput_mbps"])
            if row.get("mean_throughput_mbps") is not None
            else float("nan")
        )
        d_bar.append(float(SLICES[sid]["d_bar_ms"]))
        t_bar.append(float(SLICES[sid]["t_bar_mbps"]))
    ax1.bar([i - 0.18 for i in x], mean_d, width=0.36, color="#4C72B0", label="mean delay", zorder=3)
    ax1.bar([i + 0.18 for i in x], d_bar, width=0.36, color="#C44E52", alpha=0.55, label="D̄", zorder=3)
    ax1.set_xticks(x, labels)
    ax1.set_ylabel("ms")
    ax1.set_title("Mean delay vs budget")
    ax1.legend(fontsize=8)
    ax1.grid(True, axis="y", zorder=0)
    ax2.bar([i - 0.18 for i in x], mean_t, width=0.36, color="#4C72B0", label="mean thr", zorder=3)
    ax2.bar([i + 0.18 for i in x], t_bar, width=0.36, color="#55A868", alpha=0.55, label="T̄", zorder=3)
    ax2.set_xticks(x, labels)
    ax2.set_ylabel("Mbps")
    ax2.set_title("Mean throughput vs budget")
    ax2.legend(fontsize=8)
    ax2.grid(True, axis="y", zorder=0)
    fig.suptitle(f"{scheme}: means vs SLA", fontsize=12)
    fig.tight_layout()
    for ext in (".png", ".pdf"):
        p = plots_dir / f"means_vs_sla{ext}"
        fig.savefig(p, dpi=160, bbox_inches="tight")
        written.append(p)
    plt.close(fig)

    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("run_path", nargs="?", default="", help="run directory path (optional)")
    p.add_argument("--run", default="", help="run directory path")
    p.add_argument(
        "--scheme",
        default="s0",
        help="s0|s1|s2|s3 (default s0); used when selecting latest or --run-id",
    )
    p.add_argument(
        "--run-id",
        default="",
        help="folder under paper/exp4/<scheme>/data/ (default: latest with summary.json)",
    )
    args = p.parse_args(argv)

    run = args.run or args.run_path
    try:
        out = resolve_run_dir(run=run, scheme=args.scheme, run_id=args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not out.is_dir():
        print(f"error: not a directory: {out}", file=sys.stderr)
        return 1
    if not (out / "summary.json").is_file():
        print(f"error: missing {out}/summary.json (run exp_start.py first)", file=sys.stderr)
        return 1

    print(f"Plotting {out} …")
    try:
        paths = plot_run(out)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
