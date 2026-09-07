#!/usr/bin/env python3
"""Experiment 4: publication figures (waterfall + layer contribution + Pareto).

Reads paper/exp4/data/exp4_waterfall.csv and exp4_scheme_metrics.csv.
Writes PNG (+ PDF) under paper/exp4/plots/.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
PLOTS_DIR = HERE / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
    "axes.edgecolor": "#333333",
    "axes.linewidth": 0.8,
    "grid.color": "#E0E0E0",
    "grid.linestyle": "--",
    "grid.alpha": 0.7,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
})

SCHEME_COLORS = {
    "S0": "#7A7A7A",
    "S1": "#1F77B4",
    "S2": "#E67E22",
    "S3": "#2E8B57",
}
DELTA_COLORS = ["#1F77B4", "#E67E22", "#2E8B57"]
LAYER_NAMES = ["PL", "PM", "PS"]


def load_csv(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Run simulate_exp4.py first.")
    with path.open() as f:
        return list(csv.DictReader(f))


def save(fig, stem: str) -> None:
    fig.tight_layout()
    for ext in (".png", ".pdf"):
        out = PLOTS_DIR / f"{stem}{ext}"
        fig.savefig(out, dpi=300, bbox_inches="tight")
        print(f"  wrote {out}")
    plt.close(fig)


def _waterfall_bars(ax, residuals: list[float], ylabel: str, title: str) -> None:
    """McKinsey-style waterfall: S0 total, three floating drops, S3 total."""
    s0, s1, s2, s3 = residuals
    drops = [s0 - s1, s1 - s2, s2 - s3]
    # 5 bars: S0 | ΔPL | ΔPM | ΔPS | S3
    bottoms = [0.0, s1, s2, s3, 0.0]
    heights = [s0, drops[0], drops[1], drops[2], s3]
    colors = [SCHEME_COLORS["S0"], *DELTA_COLORS, SCHEME_COLORS["S3"]]
    labels = ["S0\nStatic", "+ PL", "+ PM", "+ PS", "S3\nFull"]
    xs = np.arange(5)
    ax.bar(xs, heights, bottom=bottoms, color=colors, width=0.62,
           edgecolor="#333333", linewidth=0.6, zorder=3)

    # Connector lines between running residual
    running = [s0, s1, s2, s3]
    for i in range(3):
        y = running[i]
        ax.plot([i + 0.31, i + 1 - 0.31], [y, y], color="#555555",
                lw=0.8, ls="--", zorder=2)
    ax.plot([3 + 0.31, 4 - 0.31], [s3, s3], color="#555555",
            lw=0.8, ls="--", zorder=2)

    for i, (h, b) in enumerate(zip(heights, bottoms)):
        if i == 0 or i == 4:
            ax.text(i, h + 2.2, f"{h:.1f}%", ha="center", va="bottom",
                    fontsize=9, fontweight="bold")
        else:
            ax.text(i, b + h + 2.2, f"−{h:.1f} pp", ha="center", va="bottom",
                    fontsize=8, color=colors[i], fontweight="bold")

    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel, fontweight="bold")
    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylim(0, max(s0, 100) * 1.18)
    ax.axhline(100.0, color="#BBBBBB", lw=0.6, ls=":", zorder=1)
    ax.yaxis.grid(True, zorder=0)
    ax.set_axisbelow(True)

    handles = [
        mpatches.Patch(color=SCHEME_COLORS["S0"], label="S0 residual"),
        mpatches.Patch(color=DELTA_COLORS[0], label="PL contribution"),
        mpatches.Patch(color=DELTA_COLORS[1], label="PM contribution"),
        mpatches.Patch(color=DELTA_COLORS[2], label="PS contribution"),
        mpatches.Patch(color=SCHEME_COLORS["S3"], label="S3 residual"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=7.5, framealpha=0.92)


def plot_sla_waterfall(wf: list[dict]) -> None:
    residuals = [float(r["sla_residual_pct"]) for r in wf]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=300)
    _waterfall_bars(
        ax, residuals,
        ylabel="SLA violation residual (% of S0)",
        title="Figure 4A: SLA Violation Reduction (layer waterfall)",
    )
    save(fig, "fig4a_sla_waterfall")


def plot_opex_waterfall(wf: list[dict]) -> None:
    residuals = [float(r["opex_residual_pct"]) for r in wf]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=300)
    _waterfall_bars(
        ax, residuals,
        ylabel="OPEX residual (% of S0)",
        title="Figure 4B: Network OPEX Reduction (layer waterfall)",
    )
    save(fig, "fig4b_opex_waterfall")


def plot_throughput_efficiency(sch: list[dict]) -> None:
    labels = [r["label"] for r in sch]
    thr = [float(r["throughput_norm_pct"]) for r in sch]
    eff = [float(r["efficiency_norm_pct"]) for r in sch]
    xs = np.arange(len(labels))
    w = 0.36
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=300)
    ax.bar(xs - w / 2, thr, w, label="Useful throughput", color="#4C78A8",
           edgecolor="#333", lw=0.5)
    ax.bar(xs + w / 2, eff, w, label="Resource efficiency (Mbps / $)",
           color="#54A24B", edgecolor="#333", lw=0.5)
    ax.axhline(100.0, color="#888", ls=":", lw=0.8, label="S0 = 100%")
    for i, (t, e) in enumerate(zip(thr, eff)):
        ax.text(i - w / 2, t + 1.5, f"{t:.0f}", ha="center", fontsize=8)
        ax.text(i + w / 2, e + 1.5, f"{e:.0f}", ha="center", fontsize=8)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Normalized to S0 (%)", fontweight="bold")
    ax.set_title("Figure 4C: Throughput and Resource Efficiency", fontweight="bold", pad=10)
    ax.legend(fontsize=8, framealpha=0.92)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ymax = max(thr + eff + [100]) * 1.16
    ax.set_ylim(0, ymax)
    save(fig, "fig4c_throughput_efficiency")


def plot_layer_contribution(wf: list[dict]) -> None:
    sla = [float(r["sla_residual_pct"]) for r in wf]
    opex = [float(r["opex_residual_pct"]) for r in wf]
    sla_pp = [sla[0] - sla[1], sla[1] - sla[2], sla[2] - sla[3]]
    opex_pp = [opex[0] - opex[1], opex[1] - opex[2], opex[2] - opex[3]]

    fig, ax = plt.subplots(figsize=(8.4, 3.6), dpi=300)
    ax.axis("off")
    ax.set_title("Figure 4D: Control-Layer Contribution", fontweight="bold", pad=8)

    cells = [
        ["Control layer", "Primary benefit", "What moves", "SLA Δ (pp of S0)", "OPEX Δ (pp of S0)"],
        ["PL", "Optimal placement (where)", "OPEX, E2E latency",
         f"−{sla_pp[0]:.1f}", f"−{opex_pp[0]:.1f}"],
        ["PM", "Elastic compute (how much)", "CPU/GPU util., queue backlog",
         f"−{sla_pp[1]:.1f}", f"−{opex_pp[1]:.1f}"],
        ["PS", "Channel adaptation (how radio)", "SLA violation, throughput",
         f"−{sla_pp[2]:.1f}", f"−{opex_pp[2]:.1f}"],
        ["PL + PM + PS", "End-to-end multi-timescale", "SLA–OPEX Pareto frontier",
         f"−{sla[0] - sla[3]:.1f} total", f"−{opex[0] - opex[3]:.1f} total"],
    ]
    table = ax.table(
        cellText=cells,
        loc="center",
        cellLoc="left",
        colWidths=[0.18, 0.28, 0.24, 0.16, 0.16],
    )
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
    save(fig, "fig4d_layer_contribution")


def plot_pareto(sch: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=300)
    xs, ys = [], []
    for r in sch:
        x = float(r["opex_norm_pct"])
        y = float(r["sla_violation_norm_pct"])
        xs.append(x)
        ys.append(y)
        sid = r["scheme"]
        ax.scatter(x, y, s=90, color=SCHEME_COLORS[sid], zorder=3,
                   edgecolor="#222", linewidth=0.6)
        ax.annotate(r["label"], (x, y), textcoords="offset points",
                    xytext=(8, 6), fontsize=8, fontweight="bold",
                    color=SCHEME_COLORS[sid])
    ax.plot(xs, ys, color="#888888", lw=1.2, ls="--", zorder=2)
    ax.set_xlabel("OPEX (% of S0)", fontweight="bold")
    ax.set_ylabel("SLA violation (% of S0)", fontweight="bold")
    ax.set_title("Figure 4E: SLA–OPEX Pareto Walk (S0 → S3)", fontweight="bold", pad=10)
    ax.grid(True)
    ax.set_xlim(min(xs) * 0.85, max(xs) * 1.08)
    ax.set_ylim(-2, max(ys) * 1.12)
    save(fig, "fig4e_sla_opex_pareto")


def plot_journal_summary(wf: list[dict], sch: list[dict]) -> None:
    fig = plt.figure(figsize=(11.2, 8.4), dpi=300)
    gs = fig.add_gridspec(2, 2, hspace=0.38, wspace=0.28)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

    _waterfall_bars(
        ax_a,
        [float(r["sla_residual_pct"]) for r in wf],
        ylabel="SLA violation residual (% of S0)",
        title="A. SLA violation reduction",
    )
    _waterfall_bars(
        ax_b,
        [float(r["opex_residual_pct"]) for r in wf],
        ylabel="OPEX residual (% of S0)",
        title="B. OPEX reduction",
    )

    labels = [r["scheme"] for r in sch]
    thr = [float(r["throughput_norm_pct"]) for r in sch]
    eff = [float(r["efficiency_norm_pct"]) for r in sch]
    xs = np.arange(len(labels))
    w = 0.36
    ax_c.bar(xs - w / 2, thr, w, label="Throughput", color="#4C78A8", edgecolor="#333", lw=0.5)
    ax_c.bar(xs + w / 2, eff, w, label="Efficiency", color="#54A24B", edgecolor="#333", lw=0.5)
    ax_c.axhline(100.0, color="#888", ls=":", lw=0.8)
    ax_c.set_xticks(xs)
    ax_c.set_xticklabels([r["label"] for r in sch], fontsize=8)
    ax_c.set_ylabel("Normalized to S0 (%)", fontweight="bold")
    ax_c.set_title("C. Throughput and resource efficiency", fontweight="bold")
    ax_c.legend(fontsize=7.5)
    ax_c.yaxis.grid(True)
    ax_c.set_axisbelow(True)

    px = [float(r["opex_norm_pct"]) for r in sch]
    py = [float(r["sla_violation_norm_pct"]) for r in sch]
    ax_d.plot(px, py, color="#888", lw=1.2, ls="--")
    for r, x, y in zip(sch, px, py):
        ax_d.scatter(x, y, s=80, color=SCHEME_COLORS[r["scheme"]],
                     edgecolor="#222", lw=0.6, zorder=3)
        ax_d.annotate(r["scheme"], (x, y), textcoords="offset points",
                      xytext=(6, 5), fontsize=8, fontweight="bold",
                      color=SCHEME_COLORS[r["scheme"]])
    ax_d.set_xlabel("OPEX (% of S0)", fontweight="bold")
    ax_d.set_ylabel("SLA violation (% of S0)", fontweight="bold")
    ax_d.set_title("D. Joint SLA–OPEX Pareto walk", fontweight="bold")
    ax_d.grid(True)

    fig.suptitle(
        "Experiment 4: Synergy of PL + PM + PS  (S0 = 100%)",
        fontsize=13, fontweight="bold", y=0.98,
    )
    fig.savefig(PLOTS_DIR / "fig4_journal_summary.png", dpi=300, bbox_inches="tight")
    fig.savefig(PLOTS_DIR / "fig4_journal_summary.pdf", dpi=300, bbox_inches="tight")
    print(f"  wrote {PLOTS_DIR / 'fig4_journal_summary.png'}")
    plt.close(fig)


def main() -> None:
    wf = load_csv("exp4_waterfall.csv")
    sch = load_csv("exp4_scheme_metrics.csv")
    print("Experiment 4 — generating publication figures")
    plot_sla_waterfall(wf)
    plot_opex_waterfall(wf)
    plot_throughput_efficiency(sch)
    plot_layer_contribution(wf)
    plot_pareto(sch)
    plot_journal_summary(wf, sch)
    print(f"All figures in {PLOTS_DIR}")


if __name__ == "__main__":
    main()
