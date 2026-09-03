#!/usr/bin/env python3
"""Publication Plotting Script for Scheme A (exp1-a) using Real Testbed Results.

Generates high-resolution publication figures directly from physical testbed data:
  1. fig1a_scheme_a_latency_cdf.png: Empirical CDF of Real Latency vs SLA Deadlines
  2. fig1a_scheme_a_sla_compliance.png: Real Latency Percentiles (p50, p95, Mean) vs SLA Targets
  3. fig1a_scheme_a_opex_breakdown.png: Real OPEX Component Breakdown (Compute, GPU, Transport)

Note: Pure plain text formatting only (No LaTeX syntax).
"""

import os
import sys
import json
import csv
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
PLOTS_DIR = HERE / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

# Styling Configuration (Modern clean design, no LaTeX)
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "figure.titlesize": 14,
    "text.usetex": False,
    "mathtext.fontset": "dejavusans",
    "grid.color": "#e0e0e0",
    "grid.linestyle": "--",
    "grid.linewidth": 0.7,
})

# Harmonious Color Palette
COLORS = {
    "cctv": "#2b5c8f",        # Blue (Slice 1)
    "physical_ai": "#d95f02", # Red-Orange (Slice 2)
    "ott": "#7570b3",         # Purple (Slice 3)
    "iot": "#1b9e77",         # Green (Slice 4)
    "compute": "#386cb0",     # Blue
    "gpu": "#fdc086",         # Gold
    "transport": "#f0027f",   # Magenta
}

def plot_latency_cdf(samples_by_slice: dict, summary_data: list):
    """Generates Empirical CDF from real measured testbed samples."""
    fig, ax = plt.subplots(figsize=(8.2, 5.2), dpi=300)
    
    slice_colors = {1: COLORS["cctv"], 2: COLORS["physical_ai"], 3: COLORS["ott"], 4: COLORS["iot"]}
    
    max_lat_seen = 10.0
    for row in summary_data:
        s_id = int(row["slice_id"])
        s_name = row["slice_name"]
        tier = row["placed_tier"].capitalize()
        sla_ms = float(row["sla_threshold_ms"])
        color = slice_colors.get(s_id, "#333333")
        
        lats = np.array(samples_by_slice.get(s_id, []))
        if len(lats) == 0:
            continue
            
        max_lat_seen = max(max_lat_seen, float(np.max(lats)), sla_ms)
        sorted_lats = np.sort(lats)
        cdf = np.arange(1, len(sorted_lats) + 1) / len(sorted_lats) * 100.0
        
        ax.plot(sorted_lats, cdf, label=f"Slice {s_id}: {s_name} ({tier})", color=color, linewidth=2.2)
        
        # Real SLA target line
        ax.axvline(x=sla_ms, color=color, linestyle=":", alpha=0.65, linewidth=1.4)
        ax.text(sla_ms, 12 + (s_id * 18), f" SLA {int(sla_ms)}ms", color=color, fontsize=8.5, fontweight="bold")

    ax.set_xlabel("Real End-to-End Latency (ms)", fontweight="bold")
    ax.set_ylabel("Empirical CDF (%)", fontweight="bold")
    ax.set_title("Scheme A (Proposed PL): Real Measured Multi-Cluster Latency Distribution", pad=12, fontweight="bold")
    ax.set_ylim(0, 105)
    ax.set_xlim(0, max_lat_seen * 1.15)
    ax.grid(True, linestyle="--", alpha=0.6)
    ax.legend(loc="lower right", framealpha=0.92)
    
    plt.tight_layout()
    out_path = PLOTS_DIR / "fig1a_scheme_a_latency_cdf.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved: {out_path}")

def plot_sla_compliance(summary_data: list):
    """Generates bar chart of real latency percentiles vs SLA thresholds."""
    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=300)
    
    slices = [f"Slice {r['slice_id']}\n{r['app_type'].upper()}\n({r['placed_tier'].capitalize()})" for r in summary_data]
    x = np.arange(len(slices))
    width = 0.2
    
    sla_vals = [float(r["sla_threshold_ms"]) for r in summary_data]
    p50_vals = [float(r["p50_latency_ms"]) for r in summary_data]
    p95_vals = [float(r["p95_latency_ms"]) for r in summary_data]
    mean_vals = [float(r["mean_latency_ms"]) for r in summary_data]
    sla_sat_vals = [float(r["sla_satisfaction_pct"]) for r in summary_data]
    
    rects1 = ax.bar(x - 1.5*width, sla_vals, width, label="SLA Target (ms)", color="#cccccc", edgecolor="#888888", hatch="//")
    rects2 = ax.bar(x - 0.5*width, mean_vals, width, label="Measured Mean (ms)", color="#386cb0")
    rects3 = ax.bar(x + 0.5*width, p50_vals, width, label="Measured P50 (ms)", color="#7fc97f")
    rects4 = ax.bar(x + 1.5*width, p95_vals, width, label="Measured P95 (ms)", color="#fdc086")
    
    # SLA satisfaction annotations
    for i, sat in enumerate(sla_sat_vals):
        max_bar = max(sla_vals[i], p95_vals[i], mean_vals[i])
        badge_color = "#1b9e77" if sat >= 95.0 else "#d95f02"
        ax.text(x[i], max_bar + 2.5, f"SLA: {sat:.1f}%", ha="center", va="bottom", fontsize=9, fontweight="bold", color=badge_color)

    ax.set_xlabel("5G Network Slice & Placed Tier", fontweight="bold", labelpad=8)
    ax.set_ylabel("Measured Latency (ms)", fontweight="bold")
    ax.set_title("Scheme A (Proposed PL): Real Latency Metrics vs SLA Target Threshold", pad=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(slices)
    ax.set_ylim(0, max(max(sla_vals), max(p95_vals)) * 1.25)
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    ax.legend(loc="upper left", framealpha=0.9)
    
    plt.tight_layout()
    out_path = PLOTS_DIR / "fig1a_scheme_a_sla_compliance.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved: {out_path}")

def plot_opex_breakdown(summary_data: list):
    """Generates stacked bar chart for real OPEX component breakdown."""
    fig, ax = plt.subplots(figsize=(8.5, 5.2), dpi=300)
    
    slices = [f"Slice {r['slice_id']} ({r['placed_tier'].capitalize()})" for r in summary_data] + ["Total Scheme A"]
    x = np.arange(len(slices))
    width = 0.45
    
    comp_vals = [float(r["compute_cost"]) for r in summary_data]
    gpu_vals = [float(r["gpu_cost"]) for r in summary_data]
    trans_vals = [float(r["transport_cost"]) for r in summary_data]
    
    comp_vals.append(sum(comp_vals))
    gpu_vals.append(sum(gpu_vals))
    trans_vals.append(sum(trans_vals))
    
    p1 = ax.bar(x, comp_vals, width, label="Compute OPEX (CPU+RAM)", color="#386cb0")
    p2 = ax.bar(x, gpu_vals, width, bottom=comp_vals, label="GPU Accelerator OPEX", color="#fdc086")
    p3 = ax.bar(x, trans_vals, width, bottom=np.array(comp_vals) + np.array(gpu_vals), label="Transport OPEX", color="#f0027f")
    
    totals = np.array(comp_vals) + np.array(gpu_vals) + np.array(trans_vals)
    for i, tot in enumerate(totals):
        ax.text(x[i], tot + 0.6, f"${tot:.2f}", ha="center", va="bottom", fontsize=9.5, fontweight="bold")

    ax.set_xlabel("5G Slice / Aggregated Total", fontweight="bold", labelpad=8)
    ax.set_ylabel("Hourly Operational Cost ($/hr)", fontweight="bold")
    ax.set_title("Scheme A (Proposed PL): Real Multi-Cluster OPEX Breakdown", pad=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(slices)
    ax.set_ylim(0, max(totals) * 1.18)
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    ax.legend(loc="upper left", framealpha=0.9)
    
    plt.tight_layout()
    out_path = PLOTS_DIR / "fig1a_scheme_a_opex_breakdown.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved: {out_path}")

def main():
    print("=== Generating Scheme A Publication Figures from Real Testbed Data ===")
    
    # Load summary CSV
    summary_csv = DATA_DIR / "exp1_a_latency_summary.csv"
    if not summary_csv.exists():
        import exp1_a_download_result
        exp1_a_download_result.download_and_process()
        
    summary_data = []
    with open(summary_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            summary_data.append(row)
            
    # Load raw physical samples
    samples_by_slice = {1: [], 2: [], 3: [], 4: []}
    raw_csv = DATA_DIR / "exp1_a_raw_samples.csv"
    if raw_csv.exists():
        with open(raw_csv, "r") as f:
            for row in csv.DictReader(f):
                s_id = int(row["slice_id"])
                if s_id in samples_by_slice:
                    samples_by_slice[s_id].append(float(row["e2e_latency_ms"]))
                    
    plot_latency_cdf(samples_by_slice, summary_data)
    plot_sla_compliance(summary_data)
    plot_opex_breakdown(summary_data)
    
    print("All Scheme A figures successfully generated from real testbed data in paper/exp1/plots/.")

if __name__ == "__main__":
    main()
