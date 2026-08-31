#!/usr/bin/env python3
"""Plot last-window DL / UL / RTT CSVs from ``data/<ue_id>/``.

Default (no args): overview + per-slice time series, plus mean bar charts.

Writes under ``plots/`` (gitignored):

  plots/overview.png|.pdf                 DL / UL / RTT, all UEs
  plots/overview_slice_mean.png|.pdf      DL / UL / RTT, mean of UEs in each slice
  plots/slice{N}_{app}.png|.pdf           DL / UL / RTT, one slice
  plots/mean_throughput.png|.pdf          mean DL & UL per UE
  plots/mean_rtt.png|.pdf                 mean RTT per UE
  plots/mean_throughput_slice.png|.pdf    mean DL / UL per slice
  plots/mean_rtt_slice.png|.pdf           mean RTT per slice
  plots/mean_slice_bars.png|.pdf          slice comparison: DL / UL / RTT (one bar per slice)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import AutoMinorLocator

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "data"
DEFAULT_OUT = HERE / "plots"

UE_RE = re.compile(r"^slice(\d+)-(.+)-client-(\d+)$")
TZ = ZoneInfo("Asia/Taipei")

APP_COLOR: Dict[str, str] = {
    "cctv": "#c45c26",
    "physical_ai": "#1f4e79",
    "ott": "#2e7d32",
    "iot": "#6a1b9a",
}
APP_LABEL: Dict[str, str] = {
    "cctv": "CCTV",
    "physical_ai": "Physical AI",
    "ott": "OTT",
    "iot": "IoT",
}
LINESTYLES = ("-", "--", "-.", ":")

# Per-slice expected rates (Mbps) and delay SLA (ms) for the bar overlay.
EXPECTED_SLA: Dict[int, Dict[str, float]] = {
    1: {"dl": 1.0, "ul": 4.0, "rtt": 150.0},  # CCTV
    2: {"dl": 1.0, "ul": 1.0, "rtt": 20.0},  # Physical AI
    3: {"dl": 40.0, "ul": 5.0, "rtt": 50.0},  # OTT
    4: {"dl": 1.0, "ul": 1.0, "rtt": 150.0},  # IoT
}


@dataclass
class UeSeries:
    ue_id: str
    slice_id: int
    app: str
    client: int
    t_dl: np.ndarray
    dl: np.ndarray
    t_ul: np.ndarray
    ul: np.ndarray
    t_rtt: np.ndarray
    rtt: np.ndarray

    @property
    def short(self) -> str:
        return f"s{self.slice_id}-{APP_LABEL.get(self.app, self.app)}-{self.client}"

    @property
    def color(self) -> str:
        return APP_COLOR.get(self.app, "#444444")

    @property
    def linestyle(self) -> str:
        return LINESTYLES[(self.client - 1) % len(LINESTYLES)]


def load_csv(path: Path, value_col: str) -> Tuple[np.ndarray, np.ndarray]:
    if not path.is_file():
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    times: List[float] = []
    values: List[float] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            t_s = (row.get("t_unix_s") or "").strip()
            v_s = (row.get(value_col) or "").strip()
            if not t_s or not v_s:
                continue
            times.append(float(t_s))
            values.append(float(v_s))
    if not times:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    t = np.asarray(times, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    order = np.argsort(t)
    return t[order], y[order]


def discover_ues(data_dir: Path) -> List[UeSeries]:
    ues: List[UeSeries] = []
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        m = UE_RE.match(d.name)
        if not m:
            continue
        t_dl, dl = load_csv(d / "dl.csv", "dl_mbps")
        t_ul, ul = load_csv(d / "ul.csv", "ul_mbps")
        t_rtt, rtt = load_csv(d / "rtt.csv", "rtt_ms")
        if t_dl.size == 0 and t_ul.size == 0 and t_rtt.size == 0:
            continue
        ues.append(
            UeSeries(
                ue_id=d.name,
                slice_id=int(m.group(1)),
                app=m.group(2),
                client=int(m.group(3)),
                t_dl=t_dl,
                dl=dl,
                t_ul=t_ul,
                ul=ul,
                t_rtt=t_rtt,
                rtt=rtt,
            )
        )
    ues.sort(key=lambda u: (u.slice_id, u.client, u.ue_id))
    return ues


def t0_unix(ues: Sequence[UeSeries]) -> float:
    mins: List[float] = []
    for u in ues:
        for t in (u.t_dl, u.t_ul, u.t_rtt):
            if t.size:
                mins.append(float(t[0]))
    return min(mins) if mins else 0.0


def minutes_since(t: np.ndarray, t0: float) -> np.ndarray:
    if t.size == 0:
        return t
    return (t - t0) / 60.0


def style_ax(ax, ylabel: str, *, logy: bool = False) -> None:
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    else:
        ax.set_ylim(bottom=0)
    ax.grid(True, which="major", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.xaxis.set_minor_locator(AutoMinorLocator())
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot_series(ax, x: np.ndarray, y: np.ndarray, ue: UeSeries, *, lw: float = 1.3) -> None:
    if x.size == 0:
        return
    ax.plot(
        x,
        y,
        color=ue.color,
        linestyle=ue.linestyle,
        linewidth=lw,
        label=ue.short,
        alpha=0.92,
    )


def save(fig, out: Path, stem: str) -> None:
    png = out / f"{stem}.png"
    pdf = out / f"{stem}.pdf"
    fig.savefig(png, dpi=160)
    fig.savefig(pdf)
    plt.close(fig)
    print(f"Wrote {png}")
    print(f"Wrote {pdf}")


def time_title(ues: Sequence[UeSeries], t0: float) -> str:
    start = datetime.fromtimestamp(t0, tz=timezone.utc).astimezone(TZ)
    return f"no PM/PS · {start:%Y-%m-%d %H:%M:%S} {TZ.key}"


def plot_timeseries(
    ues: Sequence[UeSeries],
    out: Path,
    stem: str,
    title: str,
    *,
    rtt_log: bool,
) -> None:
    t0 = t0_unix(ues)
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(10.5, 8.2),
        sharex=True,
        constrained_layout=True,
    )
    ax_dl, ax_ul, ax_rtt = axes
    for ue in ues:
        plot_series(ax_dl, minutes_since(ue.t_dl, t0), ue.dl, ue)
        plot_series(ax_ul, minutes_since(ue.t_ul, t0), ue.ul, ue)
        plot_series(ax_rtt, minutes_since(ue.t_rtt, t0), ue.rtt, ue)
    style_ax(ax_dl, "DL (Mbps)")
    style_ax(ax_ul, "UL (Mbps)")
    style_ax(ax_rtt, "RTT (ms)", logy=rtt_log)
    ax_dl.set_title(title)
    ax_rtt.set_xlabel("Time (min)")
    handles, labels = ax_dl.get_legend_handles_labels()
    if handles:
        ncol = 2 if len(handles) > 6 else 1
        ax_dl.legend(
            handles,
            labels,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            frameon=False,
            fontsize=8,
            ncol=ncol,
        )
    save(fig, out, stem)


def mean_of(y: np.ndarray) -> float:
    if y.size == 0:
        return float("nan")
    return float(np.nanmean(y))


def slice_sum_time_mean(group: Sequence[UeSeries], y_attr: str) -> float:
    """Average each UE over time, then sum UEs → slice aggregate Mbps."""
    vals = [mean_of(getattr(u, y_attr)) for u in group]
    if not vals:
        return float("nan")
    return float(np.nansum(vals))


def group_by_slice(ues: Sequence[UeSeries]) -> Dict[int, List[UeSeries]]:
    by_slice: Dict[int, List[UeSeries]] = {}
    for u in ues:
        by_slice.setdefault(u.slice_id, []).append(u)
    return by_slice


def slice_label(group: Sequence[UeSeries]) -> str:
    u = group[0]
    app = APP_LABEL.get(u.app, u.app)
    return f"s{u.slice_id} {app} (n={len(group)})"


def slice_compare_label(group: Sequence[UeSeries]) -> str:
    u = group[0]
    app = APP_LABEL.get(u.app, u.app)
    return f"Slice {u.slice_id}\n{app}"


def t_span(ues: Sequence[UeSeries]) -> Tuple[float, float]:
    t0 = t0_unix(ues)
    tmax = t0
    for u in ues:
        for t in (u.t_dl, u.t_ul, u.t_rtt):
            if t.size:
                tmax = max(tmax, float(t[-1]))
    return t0, tmax


def resample_mean(
    group: Sequence[UeSeries],
    t_attr: str,
    y_attr: str,
    t_grid: np.ndarray,
) -> np.ndarray:
    """Equal-weight mean of UEs on a common time grid (1 s)."""
    rows: List[np.ndarray] = []
    for u in group:
        t = getattr(u, t_attr)
        y = getattr(u, y_attr)
        if t.size < 2:
            rows.append(np.full(t_grid.shape, np.nan))
            continue
        yi = np.interp(t_grid, t, y, left=np.nan, right=np.nan)
        rows.append(yi)
    if not rows:
        return np.full(t_grid.shape, np.nan)
    stacked = np.vstack(rows)
    with np.errstate(all="ignore"):
        return np.nanmean(stacked, axis=0)


def resample_sum(
    group: Sequence[UeSeries],
    t_attr: str,
    y_attr: str,
    t_grid: np.ndarray,
) -> np.ndarray:
    """Sum of UEs on a common time grid (1 s)."""
    rows: List[np.ndarray] = []
    for u in group:
        t = getattr(u, t_attr)
        y = getattr(u, y_attr)
        if t.size < 2:
            rows.append(np.full(t_grid.shape, np.nan))
            continue
        yi = np.interp(t_grid, t, y, left=np.nan, right=np.nan)
        rows.append(yi)
    if not rows:
        return np.full(t_grid.shape, np.nan)
    stacked = np.vstack(rows)
    with np.errstate(all="ignore"):
        n = np.sum(~np.isnan(stacked), axis=0)
        s = np.nansum(stacked, axis=0)
        return np.where(n > 0, s, np.nan)


def plot_slice_mean_timeseries(ues: Sequence[UeSeries], out: Path) -> None:
    t0, tmax = t_span(ues)
    if tmax <= t0:
        return
    t_grid = np.arange(t0, tmax + 0.5, 1.0)
    x = minutes_since(t_grid, t0)
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(10.5, 8.2),
        sharex=True,
        constrained_layout=True,
    )
    ax_dl, ax_ul, ax_rtt = axes
    for _sid, group in sorted(group_by_slice(ues).items()):
        color = group[0].color
        label = slice_label(group)
        ax_dl.plot(x, resample_sum(group, "t_dl", "dl", t_grid), color=color, lw=2.0, label=label)
        ax_ul.plot(x, resample_sum(group, "t_ul", "ul", t_grid), color=color, lw=2.0, label=label)
        ax_rtt.plot(x, resample_mean(group, "t_rtt", "rtt", t_grid), color=color, lw=2.0, label=label)
    style_ax(ax_dl, "DL (Mbps)")
    style_ax(ax_ul, "UL (Mbps)")
    style_ax(ax_rtt, "RTT (ms)", logy=True)
    ax_dl.set_title(f"Slice sum (time-aligned UEs) · {time_title(ues, t0)}")
    ax_rtt.set_xlabel("Time (min)")
    ax_dl.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=9)
    save(fig, out, "overview_slice_mean")


def _style_bar_ax(ax, ylabel: str) -> None:
    ax.set_ylabel(ylabel)
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _metric_bars(
    ax,
    values: np.ndarray,
    *,
    labels: Sequence[str],
    colors: Sequence[str],
    ylabel: str,
    fmt: str,
    rotate: bool = False,
    expected: Optional[np.ndarray] = None,
    legend: bool = False,
) -> None:
    x = np.arange(len(values), dtype=float)
    bars = ax.bar(x, values, color=list(colors), width=0.65, zorder=2, label="Measured")
    ax.bar_label(bars, fmt=fmt, padding=3, fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30 if rotate else 0, ha="right" if rotate else "center")
    _style_bar_ax(ax, ylabel)
    ymax = float(np.nanmax(values)) if values.size else 0.0
    if expected is not None and expected.size:
        ax.plot(
            x,
            expected,
            color="#1a1a1a",
            linestyle="--",
            marker="D",
            markersize=6.5,
            markerfacecolor="white",
            markeredgewidth=1.5,
            linewidth=1.8,
            label="Expected (SLA)",
            zorder=4,
        )
        ymax = max(ymax, float(np.nanmax(expected)))
    ax.set_ylim(0, ymax * 1.22 if ymax > 0 else 1.0)
    if legend:
        ax.legend(frameon=False, loc="upper right")


def plot_mean_slice_bars(ues: Sequence[UeSeries], out: Path) -> None:
    """Compare slices: DL row, UL row, RTT row; one bar per slice."""
    groups = [g for _, g in sorted(group_by_slice(ues).items())]
    labels = [slice_compare_label(g) for g in groups]
    colors = [g[0].color for g in groups]
    dl = np.array([slice_sum_time_mean(g, "dl") for g in groups])
    ul = np.array([slice_sum_time_mean(g, "ul") for g in groups])
    rtt = np.array([float(np.nanmean([mean_of(u.rtt) for u in g])) for g in groups])
    exp_dl = np.array([EXPECTED_SLA.get(g[0].slice_id, {}).get("dl", float("nan")) for g in groups])
    exp_ul = np.array([EXPECTED_SLA.get(g[0].slice_id, {}).get("ul", float("nan")) for g in groups])
    exp_rtt = np.array([EXPECTED_SLA.get(g[0].slice_id, {}).get("rtt", float("nan")) for g in groups])
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(8.0, 8.6),
        sharex=True,
        constrained_layout=True,
    )
    _metric_bars(
        axes[0], dl, labels=labels, colors=colors, ylabel="DL sum (Mbps)", fmt="%.3f",
        expected=exp_dl, legend=True,
    )
    _metric_bars(
        axes[1], ul, labels=labels, colors=colors, ylabel="UL sum (Mbps)", fmt="%.3f",
        expected=exp_ul,
    )
    _metric_bars(
        axes[2], rtt, labels=labels, colors=colors, ylabel="RTT (ms)", fmt="%.1f",
        expected=exp_rtt,
    )
    axes[0].set_title("Slice comparison vs SLA (no PM/PS)")
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    save(fig, out, "mean_slice_bars")


def plot_per_slice_bars(group: Sequence[UeSeries], out: Path) -> None:
    """Three bar panels for one slice: mean DL, UL, RTT per UE."""
    u = group[0]
    app = APP_LABEL.get(u.app, u.app)
    labels = [f"UE {ue.client}" for ue in group]
    colors = [ue.color for ue in group]
    dl = np.array([mean_of(ue.dl) for ue in group])
    ul = np.array([mean_of(ue.ul) for ue in group])
    rtt = np.array([mean_of(ue.rtt) for ue in group])
    fig, axes = plt.subplots(3, 1, figsize=(max(5.5, 1.4 * len(group) + 2.5), 8.0), constrained_layout=True)
    _metric_bars(axes[0], dl, labels=labels, colors=colors, ylabel="DL (Mbps)", fmt="%.3f")
    _metric_bars(axes[1], ul, labels=labels, colors=colors, ylabel="UL (Mbps)", fmt="%.3f")
    _metric_bars(axes[2], rtt, labels=labels, colors=colors, ylabel="RTT (ms)", fmt="%.1f")
    axes[0].set_title(f"Slice {u.slice_id} {app} · mean DL / UL / RTT (no PM/PS)")
    save(fig, out, f"slice{u.slice_id}_{u.app}_bars")


def plot_mean_throughput_slice(ues: Sequence[UeSeries], out: Path) -> None:
    groups = [g for _, g in sorted(group_by_slice(ues).items())]
    labels = [slice_label(g) for g in groups]
    dl = np.array([slice_sum_time_mean(g, "dl") for g in groups])
    ul = np.array([slice_sum_time_mean(g, "ul") for g in groups])
    x = np.arange(len(groups), dtype=float)
    width = 0.36
    fig, ax = plt.subplots(figsize=(8.0, 4.8), constrained_layout=True)
    bars_dl = ax.bar(x - width / 2, dl, width, label="DL", color="#1f4e79")
    bars_ul = ax.bar(x + width / 2, ul, width, label="UL", color="#c45c26")
    ax.bar_label(bars_dl, fmt="%.3f", padding=3, fontsize=8)
    ax.bar_label(bars_ul, fmt="%.3f", padding=3, fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Mean throughput (Mbps)")
    ax.set_title("Slice DL / UL (time-avg per UE, sum over UEs, no PM/PS)")
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    save(fig, out, "mean_throughput_slice")


def plot_mean_rtt_slice(ues: Sequence[UeSeries], out: Path) -> None:
    groups = [g for _, g in sorted(group_by_slice(ues).items())]
    labels = [slice_label(g) for g in groups]
    rtt = np.array([float(np.nanmean([mean_of(u.rtt) for u in g])) for g in groups])
    colors = [g[0].color for g in groups]
    x = np.arange(len(groups), dtype=float)
    fig, ax = plt.subplots(figsize=(8.0, 4.8), constrained_layout=True)
    bars = ax.bar(x, rtt, color=colors, width=0.6)
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Mean RTT (ms)")
    ax.set_title("Mean RTT per slice (no PM/PS)")
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    save(fig, out, "mean_rtt_slice")


def plot_mean_throughput(ues: Sequence[UeSeries], out: Path) -> None:
    labels = [u.short for u in ues]
    dl = np.array([mean_of(u.dl) for u in ues])
    ul = np.array([mean_of(u.ul) for u in ues])
    x = np.arange(len(ues), dtype=float)
    width = 0.38
    fig, ax = plt.subplots(figsize=(max(8.0, 0.7 * len(ues) + 3.0), 4.8), constrained_layout=True)
    ax.bar(x - width / 2, dl, width, label="DL", color="#1f4e79")
    ax.bar(x + width / 2, ul, width, label="UL", color="#c45c26")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Mean throughput (Mbps)")
    ax.set_title("Mean DL / UL per UE (no PM/PS)")
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    save(fig, out, "mean_throughput")


def plot_mean_rtt(ues: Sequence[UeSeries], out: Path) -> None:
    labels = [u.short for u in ues]
    rtt = np.array([mean_of(u.rtt) for u in ues])
    x = np.arange(len(ues), dtype=float)
    colors = [u.color for u in ues]
    fig, ax = plt.subplots(figsize=(max(8.0, 0.7 * len(ues) + 3.0), 4.8), constrained_layout=True)
    ax.bar(x, rtt, color=colors, width=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Mean RTT (ms)")
    ax.set_title("Mean RTT per UE (no PM/PS)")
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    save(fig, out, "mean_rtt")


def plot_per_ue(ues: Sequence[UeSeries], out: Path) -> None:
    dest = out / "ues"
    dest.mkdir(parents=True, exist_ok=True)
    for ue in ues:
        t0 = t0_unix([ue])
        fig, axes = plt.subplots(
            3,
            1,
            figsize=(8.5, 7.2),
            sharex=True,
            constrained_layout=True,
        )
        ax_dl, ax_ul, ax_rtt = axes
        plot_series(ax_dl, minutes_since(ue.t_dl, t0), ue.dl, ue, lw=1.6)
        plot_series(ax_ul, minutes_since(ue.t_ul, t0), ue.ul, ue, lw=1.6)
        plot_series(ax_rtt, minutes_since(ue.t_rtt, t0), ue.rtt, ue, lw=1.6)
        style_ax(ax_dl, "DL (Mbps)")
        style_ax(ax_ul, "UL (Mbps)")
        style_ax(ax_rtt, "RTT (ms)")
        ax_dl.set_title(f"{ue.ue_id} · {time_title([ue], t0)}")
        ax_rtt.set_xlabel("Time (min)")
        save(fig, dest, ue.ue_id)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--per-ue",
        action="store_true",
        help="also write plots/ues/<ue_id>.png for each UE",
    )
    p.add_argument(
        "--ue",
        action="append",
        default=[],
        help="only these ue_id directory names (repeatable)",
    )
    p.add_argument(
        "--only",
        choices=("all", "slice-bars"),
        default="all",
        help="all plots, or only the slice-comparison bar graph",
    )
    args = p.parse_args(argv)

    if not args.data.is_dir():
        print(f"error: missing {args.data} — run data_download.py first", file=sys.stderr)
        return 1

    ues = discover_ues(args.data)
    if args.ue:
        want = set(args.ue)
        ues = [u for u in ues if u.ue_id in want]
    if not ues:
        print(f"error: no UE CSVs under {args.data}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    if args.only == "slice-bars":
        plot_mean_slice_bars(ues, args.out)
        return 0

    t0 = t0_unix(ues)
    plot_timeseries(
        ues,
        args.out,
        "overview",
        f"All UEs · {time_title(ues, t0)}",
        rtt_log=True,
    )

    by_slice = group_by_slice(ues)
    for sid, group in sorted(by_slice.items()):
        app = group[0].app
        label = APP_LABEL.get(app, app)
        plot_timeseries(
            group,
            args.out,
            f"slice{sid}_{app}",
            f"Slice {sid} {label} · {time_title(group, t0_unix(group))}",
            rtt_log=False,
        )
        plot_per_slice_bars(group, args.out)

    plot_slice_mean_timeseries(ues, args.out)
    plot_mean_throughput(ues, args.out)
    plot_mean_rtt(ues, args.out)
    plot_mean_throughput_slice(ues, args.out)
    plot_mean_rtt_slice(ues, args.out)
    plot_mean_slice_bars(ues, args.out)
    if args.per_ue:
        plot_per_ue(ues, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
