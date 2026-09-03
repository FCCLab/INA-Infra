#!/usr/bin/env python3
"""Scheme A (exp1-a) System Requirement & Health Checker.

Verifies that all multi-cluster components are deployed and healthy in namespace `exp1-a`:
  1. Central Cluster (`central@central`):
     - 5GC Control Plane: AMF, SMF, NRF, UDM, UDR, AUSF, MySQL DB
     - Slice Workloads: UPF Slice 3 (OTT), UPF Slice 4 (IoT), CU-UP 4, Application OTT, Application IoT
  2. Regional Cluster (`regional@regional`):
     - Slice Workloads: UPF Slice 1 (CCTV), CU-UP 3, Application CCTV
  3. Edge Cluster (`edge@edge`):
     - RAN Infrastructure: CU-CP, DU (on USRP), CU-UP 1, CU-UP 2, FlexRIC, Near-RT RIC xApp
     - Slice Workloads: UPF Slice 2 (Physical-AI), Application Physical-AI (on GPU-A40)
  4. Network Reachability:
     - Slice N6 IP endpoints: 10.1.137.211 (CCTV), 10.1.137.212 (Physical-AI), 10.1.137.213 (OTT), 10.1.137.214 (IoT)

Exit Codes:
  0 = System is fully healthy and ready for testing
  1 = System is incomplete or unhealthy (requires bringup)
"""

import sys
import subprocess
from pathlib import Path

TARGET_NS = "exp1-a"

CLUSTERS = {
    "central@central": [
        {"name": "amf-core", "label": "app.kubernetes.io/name=oai-amf"},
        {"name": "smf-core", "label": "app.kubernetes.io/name=oai-smf"},
        {"name": "nrf-core", "label": "app.kubernetes.io/name=oai-nrf"},
        {"name": "udm-core", "label": "app.kubernetes.io/name=oai-udm"},
        {"name": "udr-core", "label": "app.kubernetes.io/name=oai-udr"},
        {"name": "ausf-core", "label": "app.kubernetes.io/name=oai-ausf"},
        {"name": "mysql", "label": "app=mysql"},
        {"name": "upf-slice-3", "prefix": "upf-slice-3"},
        {"name": "upf-slice-4", "prefix": "upf-slice-4"},
        {"name": "oai-cu-up-4", "label": "app.kubernetes.io/name=oai-cu-up-4"},
        {"name": "application-ott", "label": "app.kubernetes.io/name=application-ott"},
        {"name": "application-iot", "label": "app.kubernetes.io/name=application-iot"},
    ],
    "regional@regional": [
        {"name": "upf-slice-1", "prefix": "upf-slice-1"},
        {"name": "oai-cu-up-3", "label": "app.kubernetes.io/name=oai-cu-up-3"},
        {"name": "application-cctv", "label": "app.kubernetes.io/name=application-cctv"},
    ],
    "edge@edge": [
        {"name": "oai-cu-cp", "label": "app.kubernetes.io/name=oai-cu-cp"},
        {"name": "oai-du", "label": "app.kubernetes.io/name=oai-du"},
        {"name": "oai-cu-up-1", "label": "app.kubernetes.io/name=oai-cu-up-1"},
        {"name": "oai-cu-up-2", "label": "app.kubernetes.io/name=oai-cu-up-2"},
        {"name": "upf-slice-2", "prefix": "upf-slice-2"},
        {"name": "oai-flexric", "label": "app.kubernetes.io/name=oai-flexric"},
        {"name": "nws-xapp", "label": "app.kubernetes.io/name=nws-xapp"},
        {"name": "application-physical-ai", "label": "app.kubernetes.io/name=application-physical-ai"},
    ]
}

SLICE_IPS = {
    1: {"name": "CCTV (Regional)", "ip": "10.1.137.211"},
    2: {"name": "Physical-AI (Edge)", "ip": "10.1.137.212"},
    3: {"name": "OTT 4K (Central)", "ip": "10.1.137.213"},
    4: {"name": "IoT (Central)", "ip": "10.1.137.214"},
}

def check_system_health(verbose: bool = True) -> bool:
    """Checks pod health across all clusters and network endpoint reachability."""
    if verbose:
        print("================================================================")
        print(f" Checking Scheme A System Bringup (Namespace: {TARGET_NS})")
        print("================================================================")
        
    all_healthy = True
    unhealthy_components = []
    
    # 1. Check pods per cluster
    for ctx, components in CLUSTERS.items():
        if verbose:
            print(f"\n--- Checking Cluster Context: [{ctx}] ---")
            
        cmd = f"kubectl --context={ctx} get pods -n {TARGET_NS} --no-headers"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        pod_lines = [l for l in res.stdout.splitlines() if l.strip()]
        
        for comp in components:
            c_name = comp["name"]
            c_label = comp.get("label")
            c_prefix = comp.get("prefix")
            
            c_lines = []
            if c_label:
                check_cmd = f"kubectl --context={ctx} get pods -n {TARGET_NS} -l '{c_label}' --no-headers 2>/dev/null"
                c_res = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
                c_lines = [l for l in c_res.stdout.splitlines() if l.strip()]
            elif c_prefix:
                c_lines = [l for l in pod_lines if l.split()[0].startswith(c_prefix)]
            
            is_ok = False
            status_text = "MISSING"
            if c_lines:
                parts = c_lines[0].split()
                if len(parts) >= 3:
                    ready_ratio = parts[1]
                    phase = parts[2]
                    status_text = f"{ready_ratio} {phase}"
                    
                    if phase == "Running":
                        r_cur, r_tot = ready_ratio.split("/")
                        if int(r_cur) > 0:
                            is_ok = True
                    elif phase == "Completed":
                        is_ok = True
                        
            if is_ok:
                if verbose:
                    print(f"  [OK] {c_name:25} -> {status_text}")
            else:
                all_healthy = False
                unhealthy_components.append(f"{ctx}/{c_name} ({status_text})")
                if verbose:
                    print(f"  [FAILED] {c_name:25} -> {status_text}")
                    
    # 2. Check network reachability
    if verbose:
        print("\n--- Checking Slice N6 Network Reachability ---")
        
    for s_id, s_info in SLICE_IPS.items():
        ip = s_info["ip"]
        name = s_info["name"]
        
        ping_res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True)
        is_reachable = (ping_res.returncode == 0)
        
        if is_reachable:
            if verbose:
                print(f"  [OK] Slice {s_id} ({name}): IP {ip} reachable")
        else:
            all_healthy = False
            unhealthy_components.append(f"Network/{name} ({ip} unreachable)")
            if verbose:
                print(f"  [FAILED] Slice {s_id} ({name}): IP {ip} unreachable")
                
    if verbose:
        print("\n================================================================")
        if all_healthy:
            print(f" Scheme A System Status: FULLY HEALTHY & OPERATIONAL (100% components ready)")
        else:
            print(f" Scheme A System Status: UNHEALTHY ({len(unhealthy_components)} components failed)")
            for uc in unhealthy_components:
                print(f"   * {uc}")
        print("================================================================")
        
    return all_healthy

def main():
    healthy = check_system_health(verbose=True)
    sys.exit(0 if healthy else 1)

if __name__ == "__main__":
    main()
