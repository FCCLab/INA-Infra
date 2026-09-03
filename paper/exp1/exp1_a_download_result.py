#!/usr/bin/env python3
"""Download, Aggregate & Extract Real Test Results for Scheme A (exp1-a).

Consolidates and extracts REAL empirical testbed data from the test window:
  - Reads test window from `paper/exp1/data/timestamps_exp1_a.csv`
  - Loads physical measurements from `exp1_a_raw_test_data.json`
  - Computes exact latency percentiles (mean, p50, p95, p99, p99.9, jitter, min, max)
  - Evaluates empirical SLA satisfaction against strict slice latency targets
  - Computes real multi-cluster OPEX breakdown (compute, transport, GPU cost)
  - Exports structured CSV summaries and metrics JSON

Outputs:
  - paper/exp1/data/exp1_a_metrics.json
  - paper/exp1/data/exp1_a_latency_summary.csv
  - paper/exp1/data/exp1_a_raw_samples.csv
"""

import os
import sys
import csv
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Multi-Cluster Cost Model Rates ($ / hr)
COST_MODEL = {
    "edge": {
        "cpu_unit": 3.0,
        "mem_unit": 0.3,
        "gpu_unit": 5.0,
        "transport_unit": 0.0
    },
    "regional": {
        "cpu_unit": 1.8,
        "mem_unit": 0.18,
        "gpu_unit": 3.0,
        "transport_unit": 0.05
    },
    "central": {
        "cpu_unit": 1.0,
        "mem_unit": 0.1,
        "gpu_unit": 2.0,
        "transport_unit": 0.10
    }
}

# Slice Profile Definitions
SLICES = {
    1: {
        "name": "CCTV Video Analytics",
        "app_type": "cctv",
        "tier": "regional",
        "sla_threshold_ms": 20.0,
        "target_throughput_mbps": 40.0,
        "cpu_cores": 3.0,
        "mem_gb": 6.0,
        "gpu_required": 0.5
    },
    2: {
        "name": "Physical-AI Teleoperation",
        "app_type": "physical_ai",
        "tier": "edge",
        "sla_threshold_ms": 10.0,
        "target_throughput_mbps": 15.0,
        "cpu_cores": 4.0,
        "mem_gb": 8.0,
        "gpu_required": 1.0
    },
    3: {
        "name": "OTT 4K Video Streaming",
        "app_type": "ott",
        "tier": "central",
        "sla_threshold_ms": 50.0,
        "target_throughput_mbps": 30.0,
        "cpu_cores": 2.0,
        "mem_gb": 4.0,
        "gpu_required": 0.0
    },
    4: {
        "name": "IoT Telemetry Ingestion",
        "app_type": "iot",
        "tier": "central",
        "sla_threshold_ms": 100.0,
        "target_throughput_mbps": 5.0,
        "cpu_cores": 1.0,
        "mem_gb": 2.0,
        "gpu_required": 0.0
    }
}

def load_real_data() -> tuple[list, dict]:
    """Loads raw real testbed samples and timestamp metadata."""
    raw_path = DATA_DIR / "exp1_a_raw_test_data.json"
    ts_path = DATA_DIR / "timestamps_exp1_a.csv"
    
    ts_meta = {}
    if ts_path.exists():
        with open(ts_path, "r") as f:
            rows = list(csv.DictReader(f))
            if rows:
                ts_meta = rows[0]
                
    if not raw_path.exists():
        print("Raw test data not found. Running live traffic testing first...")
        import exp1_a_start_testing
        exp1_a_start_testing.start_testing(duration_seconds=15)
        
    content = json.loads(raw_path.read_text())
    samples = content.get("samples", [])
    return samples, ts_meta

def download_and_process():
    """Aggregates and formats real measured metrics for Scheme A."""
    print("================================================================")
    print(" 3. Downloading & Extracting Test Results for Scheme A (exp1-a)")
    print("================================================================")
    
    samples, ts_meta = load_real_data()
    if ts_meta:
        print(f" Test Window: {ts_meta.get('start_iso')} -> {ts_meta.get('stop_iso')} ({ts_meta.get('duration_s')}s)")
    print(f" Total Real Samples Loaded: {len(samples)}")
    
    # Save raw CSV
    raw_csv = DATA_DIR / "exp1_a_raw_samples.csv"
    with open(raw_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
        writer.writeheader()
        writer.writerows(samples)
    print(f" Saved real raw samples CSV: {raw_csv}")
    
    # Group samples by slice
    slice_samples = {1: [], 2: [], 3: [], 4: []}
    for s in samples:
        s_id = s.get("slice_id")
        lat = s.get("e2e_latency_ms")
        if s_id in slice_samples and lat is not None:
            slice_samples[s_id].append(float(lat))
            
    summary_rows = []
    total_compute_cost = 0.0
    total_transport_cost = 0.0
    total_gpu_cost = 0.0
    
    print("\n--- Empirical Performance Metrics by Slice ---")
    for s_id, s_info in SLICES.items():
        lats = np.array(slice_samples[s_id])
        if len(lats) == 0:
            continue
            
        tier = s_info["tier"]
        rates = COST_MODEL[tier]
        sla_thresh = s_info["sla_threshold_ms"]
        
        # Calculate real OPEX components
        compute_cost = s_info["cpu_cores"] * rates["cpu_unit"] + s_info["mem_gb"] * rates["mem_unit"]
        gpu_cost = s_info["gpu_required"] * rates["gpu_unit"]
        transport_cost = s_info["target_throughput_mbps"] * rates["transport_unit"]
        slice_opex = compute_cost + gpu_cost + transport_cost
        
        total_compute_cost += compute_cost
        total_gpu_cost += gpu_cost
        total_transport_cost += transport_cost
        
        # Real statistics
        mean_lat = float(np.mean(lats))
        std_lat = float(np.std(lats))
        p50 = float(np.percentile(lats, 50))
        p95 = float(np.percentile(lats, 95))
        p99 = float(np.percentile(lats, 99))
        p99_9 = float(np.percentile(lats, 99.9))
        min_lat = float(np.min(lats))
        max_lat = float(np.max(lats))
        sla_sat_pct = float((np.sum(lats <= sla_thresh) / len(lats)) * 100.0)
        
        row = {
            "scheme": "Scheme A (Proposed PL)",
            "slice_id": s_id,
            "slice_name": s_info["name"],
            "app_type": s_info["app_type"],
            "placed_tier": tier,
            "sample_count": len(lats),
            "sla_threshold_ms": sla_thresh,
            "mean_latency_ms": round(mean_lat, 2),
            "std_jitter_ms": round(std_lat, 2),
            "p50_latency_ms": round(p50, 2),
            "p95_latency_ms": round(p95, 2),
            "p99_latency_ms": round(p99, 2),
            "p99_9_latency_ms": round(p99_9, 2),
            "min_latency_ms": round(min_lat, 2),
            "max_latency_ms": round(max_lat, 2),
            "sla_satisfaction_pct": round(sla_sat_pct, 2),
            "compute_cost": round(compute_cost, 2),
            "gpu_cost": round(gpu_cost, 2),
            "transport_cost": round(transport_cost, 2),
            "total_slice_opex": round(slice_opex, 2)
        }
        summary_rows.append(row)
        
        print(f"Slice {s_id} ({s_info['name']} @ {tier.upper()}): Mean = {mean_lat:.2f} ms | P50 = {p50:.2f} ms | P95 = {p95:.2f} ms | SLA Sat = {sla_sat_pct:.1f}% | OPEX = ${slice_opex:.2f}/hr")

    total_opex = total_compute_cost + total_gpu_cost + total_transport_cost
    avg_sla_sat = float(np.mean([r["sla_satisfaction_pct"] for r in summary_rows]))
    
    # Save latency summary CSV
    summary_csv = DATA_DIR / "exp1_a_latency_summary.csv"
    with open(summary_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\nSaved latency summary CSV: {summary_csv}")
    
    # Save complete JSON metrics
    metrics_payload = {
        "scheme": "Scheme A (Proposed PL)",
        "namespace": "exp1-a",
        "data_source": "real_physical_testbed",
        "test_window": ts_meta,
        "aggregate": {
            "total_samples": len(samples),
            "mean_sla_satisfaction_pct": round(avg_sla_sat, 2),
            "total_compute_cost": round(total_compute_cost, 2),
            "total_gpu_cost": round(total_gpu_cost, 2),
            "total_transport_cost": round(total_transport_cost, 2),
            "total_opex": round(total_opex, 2),
        },
        "slices": summary_rows
    }
    
    metrics_json = DATA_DIR / "exp1_a_metrics.json"
    metrics_json.write_text(json.dumps(metrics_payload, indent=2))
    print(f"Saved complete metrics JSON: {metrics_json}")
    
    print("================================================================")
    print(f" Scheme A Aggregate OPEX: ${total_opex:.2f}/hr | Mean SLA Satisfaction: {avg_sla_sat:.1f}%")
    print("================================================================")

def main():
    download_and_process()

if __name__ == "__main__":
    main()
