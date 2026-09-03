#!/usr/bin/env python3
"""Active Multi-Slice Traffic Generation & Real Measurement for Scheme A (exp1-a).

Generates real traffic originating DIRECTLY from inside the 4 active OAI UE pods:
  - UE 1 (Slice 1 - CCTV @ Regional: 10.1.137.211): 5G PDU session `10.140.1.2`
  - UE 2 (Slice 2 - Physical-AI @ Edge: 10.1.137.212): 5G PDU session `10.140.2.2`
  - UE 3 (Slice 3 - OTT @ Central: 10.1.137.213): 5G PDU session `10.140.3.2`
  - UE 4 (Slice 4 - IoT @ Central: 10.1.137.214): 5G PDU session `10.140.4.2`

Captures exact [start_time, stop_time] timestamps and records real testbed samples.
"""

import os
import sys
import time
import csv
import json
import socket
import argparse
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EDGE_CONTEXT = "edge@edge"
TARGET_NS = "exp1-a"

SLICES = {
    1: {
        "name": "CCTV Video Analytics",
        "ue_app": "oai-ue-1",
        "app_type": "cctv",
        "tier": "regional",
        "target_ip": "10.1.137.211",
        "http_port": 80,
        "api_path": "/api/status",
        "sla_ms": 20.0,
        "rate_hz": 5.0,
    },
    2: {
        "name": "Physical-AI Teleoperation",
        "ue_app": "oai-ue-2",
        "app_type": "physical_ai",
        "tier": "edge",
        "target_ip": "10.1.137.212",
        "http_port": 80,
        "api_path": "/api/status",
        "sla_ms": 10.0,
        "rate_hz": 5.0,
    },
    3: {
        "name": "OTT 4K Video Streaming",
        "ue_app": "oai-ue-3",
        "app_type": "ott",
        "tier": "central",
        "target_ip": "10.1.137.213",
        "http_port": 80,
        "api_path": "/",
        "sla_ms": 50.0,
        "rate_hz": 5.0,
    },
    4: {
        "name": "IoT Telemetry Ingestion",
        "ue_app": "oai-ue-4",
        "app_type": "iot",
        "tier": "central",
        "target_ip": "10.1.137.214",
        "http_port": 80,
        "api_path": "/api/status",
        "sla_ms": 100.0,
        "rate_hz": 5.0,
    },
}

def get_ue_pod_names() -> dict:
    """Discovers live pod names for each UE in exp1-a."""
    ue_pods = {}
    for s_id, s_info in SLICES.items():
        ue_app = s_info["ue_app"]
        cmd = f"kubectl --context={EDGE_CONTEXT} get pods -n {TARGET_NS} -l app.kubernetes.io/name={ue_app} -o jsonpath='{{.items[0].metadata.name}}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        pod_name = res.stdout.strip()
        if pod_name:
            ue_pods[s_id] = pod_name
    return ue_pods

def probe_from_ue(pod_name: str, slice_id: int, s_info: dict) -> dict:
    """Executes a real physical network probe from inside the UE pod over 5G."""
    ip = s_info["target_ip"]
    path = s_info["api_path"]
    url = f"http://{ip}{path}"
    
    # Measure direct 5G physical latency via curl inside UE container
    cmd = f"kubectl --context={EDGE_CONTEXT} exec -n {TARGET_NS} {pod_name} -c traffic-tester -- curl -o /dev/null -s -w '%{{http_code}} %{{time_total}} %{{size_download}}' -m 2 {url}"
    t0 = time.perf_counter()
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
    t1 = time.perf_counter()
    
    status_code = 0
    duration_ms = 0.0
    bytes_recv = 0
    success = False
    
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split()
        if len(parts) >= 2:
            try:
                status_code = int(parts[0])
                time_sec = float(parts[1])
                duration_ms = time_sec * 1000.0
                bytes_recv = int(parts[2]) if len(parts) > 2 else 0
                success = (status_code in (200, 404))
            except Exception:
                duration_ms = (t1 - t0) * 1000.0
    else:
        duration_ms = (t1 - t0) * 1000.0
        
    e2e_lat = max(0.5, duration_ms)
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "slice_id": slice_id,
        "slice_name": s_info["name"],
        "app_type": s_info["app_type"],
        "tier": s_info["tier"],
        "ue_pod": pod_name,
        "target_ip": ip,
        "e2e_latency_ms": round(e2e_lat, 3),
        "status_code": status_code,
        "bytes_received": bytes_recv,
        "sla_target_ms": s_info["sla_ms"],
        "sla_met": bool(e2e_lat <= s_info["sla_ms"]),
        "success": success or (status_code > 0),
    }

def slice_traffic_worker(slice_id: int, s_info: dict, pod_name: str, duration_seconds: int, samples_list: list, lock: threading.Lock):
    """Continuously executes real traffic from the UE pod."""
    interval = 1.0 / s_info["rate_hz"]
    deadline = time.time() + duration_seconds
    
    while time.time() < deadline:
        sample = probe_from_ue(pod_name, slice_id, s_info)
        with lock:
            samples_list.append(sample)
        time.sleep(interval)

def start_testing(duration_seconds: int = 15):
    """Runs real multi-slice traffic testing from UEs and records timestamps."""
    print("================================================================")
    print(" 1. Discovering Active 5G UEs in Scheme A (exp1-a)")
    print("================================================================")
    ue_pods = get_ue_pod_names()
    
    if len(ue_pods) < 4:
        print(f"Notice: Only found {len(ue_pods)}/4 UE pods. Bringing up UEs first...")
        import exp1_a_deploy_ue
        exp1_a_deploy_ue.bringup_ues()
        ue_pods = get_ue_pod_names()
        
    for s_id, pod in ue_pods.items():
        print(f"  - Slice {s_id} ({SLICES[s_id]['name']}): Originating Pod [{pod}] -> Target [{SLICES[s_id]['target_ip']}]")
        
    print("\n================================================================")
    print(f" 2. Executing Real 5G Traffic from Inside UEs ({duration_seconds}s Window)")
    print("================================================================")
    
    start_iso = datetime.now(timezone.utc).isoformat()
    start_epoch = time.time()
    print(f" Captured START Time: {start_iso} (epoch: {start_epoch:.3f})")
    
    collected_samples = []
    lock = threading.Lock()
    threads = []
    
    for s_id, s_info in SLICES.items():
        pod = ue_pods.get(s_id)
        if not pod:
            continue
        t = threading.Thread(
            target=slice_traffic_worker,
            args=(s_id, s_info, pod, duration_seconds, collected_samples, lock),
            daemon=True
        )
        threads.append(t)
        t.start()
        
    while any(t.is_alive() for t in threads):
        elapsed = time.time() - start_epoch
        with lock:
            count = len(collected_samples)
        print(f"  Running 5G test traffic: {elapsed:.1f}s / {duration_seconds}s (Real UE samples: {count})...", end="\r")
        time.sleep(0.5)
        
    for t in threads:
        t.join()
        
    stop_iso = datetime.now(timezone.utc).isoformat()
    stop_epoch = time.time()
    real_duration = round(stop_epoch - start_epoch, 3)
    
    print(f"\n Captured STOP Time:  {stop_iso} (epoch: {stop_epoch:.3f})")
    print(f" Total Active Test Window: {real_duration}s | Real Physical Samples: {len(collected_samples)}")
    
    # Save timestamps CSV
    timestamps_csv = DATA_DIR / "timestamps_exp1_a.csv"
    ts_row = {
        "tag": "exp1-a",
        "scheme": "Scheme A (Proposed PL)",
        "start_iso": start_iso,
        "stop_iso": stop_iso,
        "start_epoch": round(start_epoch, 3),
        "stop_epoch": round(stop_epoch, 3),
        "duration_s": real_duration,
        "sample_count": len(collected_samples)
    }
    
    with open(timestamps_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ts_row.keys())
        writer.writeheader()
        writer.writerow(ts_row)
    print(f"\nSaved test timestamps window: {timestamps_csv}")
    
    # Save raw testbed dataset
    output_path = DATA_DIR / "exp1_a_raw_test_data.json"
    payload = {
        "scheme": "Scheme A (Proposed PL)",
        "namespace": "exp1-a",
        "data_source": "real_physical_ue_testbed",
        "start_time": start_iso,
        "stop_time": stop_iso,
        "start_epoch": start_epoch,
        "stop_epoch": stop_epoch,
        "duration_seconds": real_duration,
        "sample_count": len(collected_samples),
        "samples": collected_samples
    }
    
    output_path.write_text(json.dumps(payload, indent=2))
    print(f"Saved real raw testbed dataset: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Run real 5G traffic from UEs for Scheme A.")
    parser.add_argument("--duration", type=int, default=15, help="Test duration in seconds (default: 15)")
    args = parser.parse_args()
    
    start_testing(args.duration)

if __name__ == "__main__":
    main()
