#!/usr/bin/env python3
"""Experiment 1: Publication Plotting Script.

Generates:
  1. plots/fig1a_opex_vs_sla_pareto.png: OPEX vs. SLA Target Pareto Frontier
  2. plots/fig1b_e2e_latency_breakdown.png: E2E Delay breakdown per slice type
  3. plots/fig1c_testbed_vs_simulation.png: Real testbed vs. simulation verification

All plots strictly use plain-text typography with NO LaTeX markup.
"""

import csv
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
PLOTS_DIR = HERE / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

# Set clean aesthetic style without LaTeX
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
plt.rcParams["axes.edgecolor"] = "#333333"
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams["grid.color"] = "#E0E0E0"
plt.rcParams["grid.linestyle"] = "--"
plt.rcParams["grid.alpha"] = 0.7

def plot_figure_1a_pareto():
    """Plots Figure 1A: OPEX vs SLA Target (Pareto Frontier)."""
    csv_file = DATA_DIR / "exp1_testbed_results.csv"
    if not csv_file.exists():
        print(f"File {csv_file} not found. Run run_exp1_testbed.py first.")
        return

    data = {}
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scheme = row["scheme"]
            if scheme not in data:
                data[scheme] = {"sla_targets": [], "opex": [], "effective_opex": []}
            data[scheme]["sla_targets"].append(float(row["sla_target_pct"]))
            data[scheme]["opex"].append(float(row["total_opex"]))
            data[scheme]["effective_opex"].append(float(row["effective_opex_with_penalty"]))

    fig, ax = plt.subplots(figsize=(7, 4.8), dpi=300)

    styles = {
        "Full Algorithm (Proposed PL)": {"color": "#1F77B4", "marker": "o", "lw": 2.5, "ls": "-"},
        "Fixed Edge": {"color": "#D62728", "marker": "s", "lw": 2.0, "ls": "--"},
        "Fixed Regional": {"color": "#2CA02C", "marker": "^", "lw": 2.0, "ls": "-."},
        "Fixed Central": {"color": "#9467BD", "marker": "d", "lw": 2.0, "ls": ":"}
    }

    for scheme, vals in data.items():
        st = styles.get(scheme, {"color": "black", "marker": "x", "lw": 1.5, "ls": "-"})
        ax.plot(
            vals["sla_targets"], 
            vals["effective_opex"], 
            label=scheme,
            color=st["color"],
            marker=st["marker"],
            linewidth=st["lw"],
            linestyle=st["ls"],
            markersize=7
        )

    ax.set_xlabel("SLA Target Reliability (%)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Effective Network OPEX (Cost Units / hr)", fontsize=11, fontweight="bold")
    ax.set_title("Figure 1A: OPEX vs. SLA Target (Pareto Frontier)", fontsize=12, fontweight="bold", pad=12)
    ax.grid(True)
    ax.set_xticks([95.0, 99.0, 99.9, 99.99])
    ax.set_xticklabels(["95%", "99%", "99.9%", "99.99%"])
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9.5)

    out_path = PLOTS_DIR / "fig1a_opex_vs_sla_pareto.png"
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"Saved: {out_path}")

def plot_figure_1b_latency():
    """Plots Figure 1B: E2E Latency Breakdown per Slice Type."""
    csv_file = DATA_DIR / "exp1_latency_breakdown.csv"
    if not csv_file.exists():
        print(f"File {csv_file} not found. Run run_exp1_testbed.py first.")
        return

    schemes = ["Full Algorithm (Proposed PL)", "Fixed Edge", "Fixed Regional", "Fixed Central"]
    slice_names = ["URLLC (Physical-AI)", "Video Analytics (CCTV)", "eMBB (OTT 4K)", "IoT (Telemetry)"]
    
    # Organize data: mean delays per scheme per slice
    matrix = {s: [] for s in schemes}
    sla_thresholds = []
    
    with open(csv_file, "r") as f:
        reader = list(csv.DictReader(f))
        
    # Get unique slice threshold values
    for s_id in [2, 1, 3, 4]:
        for row in reader:
            if int(row["slice_id"]) == s_id:
                sla_thresholds.append(float(row["sla_threshold_ms"]))
                break

    for scheme in schemes:
        delays = []
        for s_id in [2, 1, 3, 4]: # Order: URLLC, CCTV, OTT, IoT
            for row in reader:
                if row["scheme"] == scheme and int(row["slice_id"]) == s_id:
                    delays.append(float(row["p95_delay_ms"]))
                    break
        matrix[scheme] = delays

    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=300)
    x = np.arange(len(slice_names))
    width = 0.18

    colors = ["#1F77B4", "#D62728", "#2CA02C", "#9467BD"]
    
    for i, scheme in enumerate(schemes):
        offset = (i - 1.5) * width
        rects = ax.bar(x + offset, matrix[scheme], width, label=scheme, color=colors[i], alpha=0.9, edgecolor="#333333")

    # Plot SLA constraint markers
    for idx, thresh in enumerate(sla_thresholds):
        ax.hlines(thresh, idx - 0.4, idx + 0.4, colors="#D62728", linestyles="--", linewidth=1.5)
        ax.text(idx, thresh + 1.2, f"SLA <= {thresh:.0f}ms", ha="center", va="bottom", fontsize=8, color="#D62728", fontweight="bold")

    ax.set_ylabel("95th-Percentile E2E Latency (ms)", fontsize=11, fontweight="bold")
    ax.set_title("Figure 1B: Measured E2E Latency per Network Slice", fontsize=12, fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(slice_names, fontsize=9.5, fontweight="bold")
    ax.legend(loc="upper right", framealpha=0.9, fontsize=9)
    ax.grid(True, axis="y")

    out_path = PLOTS_DIR / "fig1b_e2e_latency_breakdown.png"
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"Saved: {out_path}")

def plot_figure_1c_verification():
    """Plots Figure 1C: Real Testbed vs Simulation Validation."""
    tb_file = DATA_DIR / "exp1_testbed_results.csv"
    sim_file = DATA_DIR / "exp1_sim_results.csv"
    
    if not tb_file.exists() or not sim_file.exists():
        print("Data files for testbed/simulation missing.")
        return

    tb_data = []
    with open(tb_file, "r") as f:
        for row in csv.DictReader(f):
            if row["scheme"] == "Full Algorithm (Proposed PL)":
                tb_data.append((float(row["sla_target_pct"]), float(row["total_opex"])))

    sim_data = []
    with open(sim_file, "r") as f:
        for row in csv.DictReader(f):
            if row["scheme"] == "Full Algorithm (Proposed PL)":
                sim_data.append((float(row["sla_target_pct"]), float(row["total_opex"])))

    fig, ax = plt.subplots(figsize=(6.5, 4.5), dpi=300)
    
    # Plot Simulation curve
    sim_x, sim_y = zip(*sim_data)
    ax.plot(sim_x, sim_y, label="Theoretical Optimization Model", color="#1F77B4", linestyle="--", lw=2.0)
    
    # Plot Testbed points
    tb_x, tb_y = zip(*tb_data)
    ax.scatter(tb_x, tb_y, label="Real Multi-Cluster Testbed Measurement", color="#FF7F0E", s=80, zorder=5, edgecolor="#333333")

    ax.set_xlabel("SLA Target Reliability (%)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Network OPEX (Cost Units / hr)", fontsize=11, fontweight="bold")
    ax.set_title("Figure 1C: Testbed Measurement vs. Analytical Model", fontsize=12, fontweight="bold", pad=12)
    ax.grid(True)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9.5)

    out_path = PLOTS_DIR / "fig1c_testbed_vs_simulation.png"
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"Saved: {out_path}")

def main():
    print("=== Generating Experiment 1 Publication Figures ===")
    plot_figure_1a_pareto()
    plot_figure_1b_latency()
    plot_figure_1c_verification()
    print("All figures successfully generated in paper/exp1/plots/.")

if __name__ == "__main__":
    main()
