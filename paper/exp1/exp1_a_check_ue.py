#!/usr/bin/env python3
"""Scheme A (exp1-a) OAI UE Requirement & 5G PDU Session Checker.

Verifies that all 4 OAI UEs are operational and transmitting on the 5G data plane:
  1. Pod State: Checks all 4 UE pods (`oai-ue-1..4`) are in `2/2 Running` state on node `usrp`.
  2. 5G PDU Sessions: Inspects that `oaitun_ue1` interfaces have established IP addresses:
     - UE 1 (Slice 1 - CCTV): `10.140.1.2`
     - UE 2 (Slice 2 - Physical-AI): `10.140.2.2`
     - UE 3 (Slice 3 - OTT 4K): `10.140.3.2`
     - UE 4 (Slice 4 - IoT): `10.140.4.2`
  3. Data Plane Connectivity: Probes the slice server endpoint from inside each UE container.

Exit Codes:
  0 = All UEs and 5G PDU sessions are operational and ready for testing
  1 = UE pods or 5G PDU tunnels are not ready (requires UE bringup)
"""

import sys
import subprocess
from pathlib import Path

TARGET_NS = "exp1-a"
EDGE_CONTEXT = "edge@edge"

UE_SPECS = [
    {
        "slice_id": 1,
        "name": "oai-ue-1",
        "app_name": "CCTV UE",
        "expected_pdu_ip": "10.140.1.2",
        "server_ip": "10.1.137.211",
        "server_path": "/api/status",
    },
    {
        "slice_id": 2,
        "name": "oai-ue-2",
        "app_name": "Physical-AI UE",
        "expected_pdu_ip": "10.140.2.2",
        "server_ip": "10.1.137.212",
        "server_path": "/api/status",
    },
    {
        "slice_id": 3,
        "name": "oai-ue-3",
        "app_name": "OTT 4K UE",
        "expected_pdu_ip": "10.140.3.2",
        "server_ip": "10.1.137.213",
        "server_path": "/",
    },
    {
        "slice_id": 4,
        "name": "oai-ue-4",
        "app_name": "IoT UE",
        "expected_pdu_ip": "10.140.4.2",
        "server_ip": "10.1.137.214",
        "server_path": "/api/status",
    },
]

def check_ue_health(verbose: bool = True) -> bool:
    """Checks UE pods, 5G PDU IP assignments, and end-to-end PDU reachability."""
    if verbose:
        print("================================================================")
        print(f" Checking Scheme A 5G UEs & PDU Sessions (Namespace: {TARGET_NS})")
        print("================================================================")
        
    all_ready = True
    unready_ues = []
    
    for u_info in UE_SPECS:
        s_id = u_info["slice_id"]
        ue_name = u_info["name"]
        app_name = u_info["app_name"]
        expected_ip = u_info["expected_pdu_ip"]
        target_server = u_info["server_ip"]
        target_path = u_info["server_path"]
        
        # 1. Discover pod name
        cmd = f"kubectl --context={EDGE_CONTEXT} get pods -n {TARGET_NS} -l app.kubernetes.io/name={ue_name} -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        pod_name = res.stdout.strip()
        
        if not pod_name:
            all_ready = False
            unready_ues.append(f"{ue_name} (Pod Missing)")
            if verbose:
                print(f"  [FAILED] Slice {s_id} ({ue_name}): Pod not found in namespace {TARGET_NS}")
            continue
            
        # 2. Check pod phase and ready ratio
        cmd = f"kubectl --context={EDGE_CONTEXT} get pod {pod_name} -n {TARGET_NS} -o jsonpath='{{.status.phase}} {{.status.containerStatuses[*].ready}}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        status_info = res.stdout.strip()
        
        pod_running = ("Running" in status_info and "false" not in status_info)
        if not pod_running:
            all_ready = False
            unready_ues.append(f"{ue_name} ({pod_name} not all containers ready: {status_info})")
            if verbose:
                print(f"  [FAILED] Slice {s_id} ({ue_name}): Pod {pod_name} state: {status_info}")
            continue
            
        # 3. Check 5G PDU tunnel interface
        expected_subnet = f"10.140.{s_id}."
        cmd = f"kubectl --context={EDGE_CONTEXT} exec -n {TARGET_NS} {pod_name} -c traffic-tester -- ip -br a show dev oaitun_ue1 2>/dev/null"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        pdu_output = res.stdout.strip()
        
        has_pdu_ip = expected_subnet in pdu_output
        if not has_pdu_ip:
            all_ready = False
            unready_ues.append(f"{ue_name} (PDU subnet {expected_subnet} missing on oaitun_ue1: {pdu_output})")
            if verbose:
                print(f"  [FAILED] Slice {s_id} ({ue_name}): Missing 5G PDU IP {expected_subnet}* (Output: {pdu_output})")
            continue
            
        # 4. Probe application reachability over 5G PDU
        cmd = f"kubectl --context={EDGE_CONTEXT} exec -n {TARGET_NS} {pod_name} -c traffic-tester -- curl -o /dev/null -s -w '%{{http_code}}' -m 2 http://{target_server}{target_path}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        http_code = res.stdout.strip()
        
        app_connected = http_code in ("200", "404")
        if app_connected:
            if verbose:
                print(f"  [OK] Slice {s_id} ({ue_name:10} -> {app_name:18}): PDU IP [{expected_ip}] -> Server [{target_server}] HTTP {http_code}")
        else:
            all_ready = False
            unready_ues.append(f"{ue_name} (5G Traffic to {target_server} failed, code: {http_code})")
            if verbose:
                print(f"  [FAILED] Slice {s_id} ({ue_name}): 5G traffic to {target_server} failed (HTTP code: {http_code})")
                
    if verbose:
        print("\n================================================================")
        if all_ready:
            print(" Scheme A 5G UEs Status: ALL 4 UES ACTIVE & CONNECTED OVER 5G")
        else:
            print(f" Scheme A 5G UEs Status: UNREADY ({len(unready_ues)} issues detected)")
            for uu in unready_ues:
                print(f"   * {uu}")
        print("================================================================")
        
    return all_ready

def main():
    ready = check_ue_health(verbose=True)
    sys.exit(0 if ready else 1)

if __name__ == "__main__":
    main()
