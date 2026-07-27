"""Matplotlib plots matching the original simulation.py figures."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from matplotlib.lines import Line2D

from ina.models import Slice


def _colors(n: int):
    return plt.cm.tab10(np.linspace(0, 1, n + 1))


def plot_all(
    history,
    slices: Sequence[Slice],
    deploy_map: Dict[int, Tuple[int, int, int]],
    num_pm_cycles: int,
    ps_steps_per_pm: int,
    activation_schedule: Dict[int, List[int]],
    *,
    show: bool = True,
    save_dir: Optional[str | Path] = None,
) -> None:
    """Render the five figures from the classic simulation."""
    ids = [s.id for s in slices]
    by_id = {s.id: s for s in slices}
    colors = _colors(max(ids))
    get_color = lambda s: colors[s - 1]
    total_steps = num_pm_cycles * ps_steps_per_pm
    time_axis = range(total_steps)

    # Batch vertical lines from activation schedule (PM cycle → step)
    batch_steps = sorted(
        c * ps_steps_per_pm for c in activation_schedule if c > 0
    )

    saved = []

    def _finish(fig, name: str):
        fig.tight_layout()
        if save_dir:
            path = Path(save_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, dpi=150, bbox_inches="tight")
            saved.append(str(path))
            print(f"[saved] {path}")
        if show:
            plt.show()
        else:
            plt.close(fig)

    # --- Fig 1: throughput + violation ---------------------------------------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    for s in ids:
        ax1.plot(
            time_axis, history.t_actual[s], linewidth=2,
            label=f"S{s}", color=get_color(s), alpha=0.8,
        )
    for ax in (ax1, ax2):
        for x in batch_steps:
            ax.axvline(x=x, color="black", linestyle="--", alpha=0.5)
        ax.grid(True, linestyle="--", alpha=0.4)

    sla_map: Dict[float, List[str]] = {}
    for s in ids:
        sla_map.setdefault(by_id[s].t_bar, []).append(f"S{s}")
    sla_colors = ["purple", "orange", "green", "blue", "red", "brown"]
    for i, (sla_val, s_list) in enumerate(sla_map.items()):
        ax1.axhline(
            y=sla_val, color=sla_colors[i % len(sla_colors)], linestyle=":",
            label=f"{','.join(s_list)} SLA ({sla_val})",
        )
    ax1.set_title("Real-time Throughput (Dynamic Algorithm)", fontsize=16)
    ax1.set_ylabel("Throughput (Mbps)", fontsize=14)
    ax1.legend(bbox_to_anchor=(1.01, 1), loc="upper left")

    for s in ids:
        ax2.plot(
            time_axis, history.violation[s], linewidth=2,
            label=f"S{s}", color=get_color(s), alpha=0.8,
        )
    ax2.set_title("SLA Violation Magnitude", fontsize=16)
    ax2.set_ylabel("Violation (Mbps)", fontsize=14)
    ax2.set_xlabel("Time Steps", fontsize=14)
    _finish(fig, "throughput_and_sla_violation.png")

    # --- Fig 2: deployment topology ------------------------------------------
    fig, ax = plt.subplots(figsize=(12, 7))
    zone_centers = {0: 0, 1: 1, 2: 2}
    colors_zones = ["#e6f2ff", "#fff0e6", "#e6ffe6"]
    labels_zones = ["Edge DC", "Regional DC", "Central DC"]
    for j in (0, 1, 2):
        ax.add_patch(
            patches.Rectangle(
                (zone_centers[j] - 0.3, 0), 0.6, 10,
                linewidth=0, facecolor=colors_zones[j], alpha=0.5, zorder=0,
            )
        )
        ax.text(
            zone_centers[j], 9.5, labels_zones[j], ha="center", va="center",
            fontsize=12, fontweight="bold", color="gray",
        )
    markers = {"CU": "o", "UPF": "s", "APP": "^"}
    offsets = {"APP": 0.15, "UPF": 0, "CU": -0.15}
    for s in ids:
        if s not in deploy_map:
            continue
        loc_cu, loc_upf, loc_app = deploy_map[s]
        y_pos = s
        x_app = zone_centers[loc_app] + offsets["APP"]
        x_upf = zone_centers[loc_upf] + offsets["UPF"]
        x_cu = zone_centers[loc_cu] + offsets["CU"]
        c = get_color(s)
        ax.plot([x_app, x_upf], [y_pos, y_pos], color=c, linewidth=2, alpha=0.6, zorder=1)
        ax.plot([x_upf, x_cu], [y_pos, y_pos], color=c, linewidth=2, alpha=0.6, zorder=1)
        ax.scatter(x_app, y_pos, marker=markers["APP"], s=180, color=c, edgecolors="black", zorder=2)
        ax.scatter(x_upf, y_pos, marker=markers["UPF"], s=180, color=c, edgecolors="black", zorder=2)
        ax.scatter(x_cu, y_pos, marker=markers["CU"], s=180, color=c, edgecolors="black", zorder=2)
    ax.set_yticks(ids)
    ax.set_yticklabels([f"Slice {s}" for s in ids], fontsize=11)
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels([])
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0.5, max(ids) + 2)
    ax.set_ylabel("Network Slice", fontsize=14)
    ax.set_title("Optimal Deployment Topology", fontsize=16)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.legend(
        handles=[
            Line2D([0], [0], marker="o", color="w", label="CU-UP",
                   markerfacecolor="gray", markersize=10, markeredgecolor="k"),
            Line2D([0], [0], marker="s", color="w", label="UPF",
                   markerfacecolor="gray", markersize=10, markeredgecolor="k"),
            Line2D([0], [0], marker="^", color="w", label="APP",
                   markerfacecolor="gray", markersize=10, markeredgecolor="k"),
        ],
        loc="upper right", ncol=3,
    )
    _finish(fig, "deployment_topology.png")

    # --- Fig 3: bottleneck ---------------------------------------------------
    fig_bn, axes_bn = plt.subplots(4, 2, figsize=(18, 14), sharex=True)
    axes_bn = axes_bn.flatten()
    fig_bn.suptitle("Bottleneck Analysis", fontsize=24)
    for i, s in enumerate(ids):
        ax = axes_bn[i]
        ax.plot(time_axis, history.analysis[s]["SLA"], color="black",
                linestyle="--", linewidth=1.5, label="SLA")
        ax.step(time_axis, history.analysis[s]["ComputeCap"], where="post",
                color="green", linewidth=2, label="Compute Cap")
        ax.plot(time_axis, history.analysis[s]["RadioGuaranteed"], color="blue",
                linewidth=1.5, label="Radio Guaranteed")
        ax.plot(time_axis, history.t_actual[s], color="red", linewidth=2,
                label="Actual Throughput")
        ax.set_title(f"S{s}", fontsize=18)
        ax.grid(True, linestyle="--", alpha=0.3)
        for x in batch_steps:
            ax.axvline(x=x, color="gray", linestyle=":", alpha=0.5)
        ax.set_ylabel("Throughput (Mbps)", fontsize=14)
        if i >= 6:
            ax.set_xlabel("Time Steps", fontsize=14)
        if i == 0:
            ax.legend(loc="upper right", fontsize=12)
    fig_bn.tight_layout(rect=[0, 0.03, 1, 0.97])
    _finish(fig_bn, "bottleneck_analysis.png")

    # --- Fig 4: resource utilization -----------------------------------------
    fig_res, axes_res = plt.subplots(3, 2, figsize=(14, 12), sharex=True)
    loc_names = {0: "Edge", 1: "Regional", 2: "Central"}
    for j in (0, 1, 2):
        ax_app = axes_res[j, 0]
        ax_app.plot(time_axis, history.usage[j]["APP_CPU"], color="blue", linewidth=2, label="APP CPU")
        ax_app.plot(time_axis, history.usage[j]["APP_RAM"], color="orange", linewidth=2, label="APP RAM")
        ax_app.plot(time_axis, history.usage[j]["APP_GPU"], color="green", linewidth=2, label="APP GPU")
        ax_app.set_title(f"{loc_names[j]} DC - APP Resources", fontsize=12)
        ax_app.set_ylim(0, 110)
        ax_app.grid(True, linestyle="--", alpha=0.3)
        if j == 0:
            ax_app.legend(loc="upper right", fontsize=9)
        ax_nf = axes_res[j, 1]
        ax_nf.plot(time_axis, history.usage[j]["NF_CPU"], color="red", linewidth=2, label="NF CPU")
        ax_nf.plot(time_axis, history.usage[j]["NF_RAM"], color="purple", linewidth=2, label="NF RAM")
        ax_nf.set_title(f"{loc_names[j]} DC - NF Resources", fontsize=12)
        ax_nf.set_ylim(0, 110)
        ax_nf.grid(True, linestyle="--", alpha=0.3)
        if j == 0:
            ax_nf.legend(loc="upper right", fontsize=9)
    axes_res[2, 0].set_xlabel("Time Steps")
    axes_res[2, 1].set_xlabel("Time Steps")
    fig_res.suptitle("Resource Utilization", fontsize=16)
    fig_res.tight_layout(rect=[0, 0.03, 1, 0.97])
    _finish(fig_res, "resource_utilization.png")

    # --- Fig 5: dynamic vs static --------------------------------------------
    fig_comp, (ax_c1, ax_c2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    comp_limit = min(60, total_steps)
    time_comp = list(range(comp_limit))
    slices_to_plot = [s for s in ids if s <= 6]
    for s in slices_to_plot:
        ax_c1.plot(time_comp, history.t_actual[s][:comp_limit], linewidth=2,
                   linestyle="-", color=get_color(s))
        ax_c1.plot(time_comp, history.t_baseline[s][:comp_limit], linewidth=2,
                   linestyle="--", color=get_color(s), alpha=0.6)
    if batch_steps:
        ax_c1.axvline(x=batch_steps[0], color="black", linestyle="-.", alpha=0.3)
    ax_c1.set_title("Throughput Comparison: Dynamic (Solid) vs Static (Dashed)", fontsize=18)
    ax_c1.set_ylabel("Throughput (Mbps)", fontsize=14)
    ax_c1.grid(True, linestyle="--", alpha=0.3)
    legend_elements = [
        Line2D([0], [0], color="black", lw=2, linestyle="-", label="Dynamic (PL+PM+PS)"),
        Line2D([0], [0], color="black", lw=2, linestyle="--", label="Static (PL Only)"),
    ]
    for s in slices_to_plot:
        legend_elements.append(Line2D([0], [0], color=get_color(s), lw=2, label=f"Slice {s}"))
    ax_c1.legend(handles=legend_elements, bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=12)

    for s in slices_to_plot:
        ax_c2.plot(time_comp, history.violation[s][:comp_limit], linewidth=2,
                   linestyle="-", color=get_color(s))
        ax_c2.plot(time_comp, history.vio_baseline[s][:comp_limit], linewidth=2,
                   linestyle="--", color=get_color(s), alpha=0.6)
    if batch_steps:
        ax_c2.axvline(x=batch_steps[0], color="black", linestyle="-.", alpha=0.3)
    ax_c2.set_title("SLA Violation Comparison: Dynamic (Solid) vs Static (Dashed)", fontsize=18)
    ax_c2.set_ylabel("Violation (Mbps)", fontsize=14)
    ax_c2.set_xlabel(f"Time Steps (First {comp_limit})", fontsize=14)
    ax_c2.grid(True, linestyle="--", alpha=0.3)
    _finish(fig_comp, "dynamic_vs_static_comparison.png")

    if saved:
        print(f"Saved {len(saved)} figures under {save_dir}")
