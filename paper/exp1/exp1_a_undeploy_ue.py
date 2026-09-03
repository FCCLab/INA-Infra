#!/usr/bin/env python3
"""Undeploy Scheme A (exp1-a) OAI UEs and Application Clients from edge cluster.

Removes:
  - Deployments: oai-ue-slice-1-client-1 .. oai-ue-slice-4-client-1
  - Services: oai-ue-slice-1-client-1 .. oai-ue-slice-4-client-1
  - ServiceAccounts: oai-ue-1-sa .. oai-ue-4-sa
  - ConfigMaps: oai-ue-1-conf .. oai-ue-4-conf
"""

import sys
import argparse
import subprocess
from pathlib import Path

def undeploy_ues(namespace: str = "exp1-a", context: str = "edge@edge"):
    print("================================================================")
    print(f" Undeploying UEs & Application Clients (Namespace: {namespace})")
    print("================================================================")
    
    # 1. Delete UE Deployments
    print(f"\n1. Deleting UE deployments in namespace [{namespace}] on [{context}]...")
    cmd_deploy = [
        "kubectl", f"--context={context}", "delete", "deployment",
        "-n", namespace,
        "-l", "ina.lab/role=ue-client",
        "--grace-period=0", "--force"
    ]
    subprocess.run(cmd_deploy, check=False)
    
    # Also delete by specific names if labels differ
    for s_id in range(1, 5):
        subprocess.run([
            "kubectl", f"--context={context}", "delete", "deployment",
            f"oai-ue-slice-{s_id}-client-1", "-n", namespace,
            "--grace-period=0", "--force", "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    # 2. Delete UE Services
    print(f"2. Deleting UE services in namespace [{namespace}]...")
    for s_id in range(1, 5):
        subprocess.run([
            "kubectl", f"--context={context}", "delete", "svc",
            f"oai-ue-slice-{s_id}-client-1", "-n", namespace,
            "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    # 3. Delete UE ServiceAccounts and ConfigMaps
    print(f"3. Deleting UE ConfigMaps & ServiceAccounts in namespace [{namespace}]...")
    for s_id in range(1, 5):
        ue_name = f"oai-ue-{s_id}"
        subprocess.run([
            "kubectl", f"--context={context}", "delete", "sa",
            f"{ue_name}-sa", "-n", namespace,
            "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "kubectl", f"--context={context}", "delete", "cm",
            f"{ue_name}-conf", "-n", namespace,
            "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    print(f"\n[OK] All UEs and Application Clients in namespace [{namespace}] have been undeployed.")

def main():
    parser = argparse.ArgumentParser(description="Undeploy Scheme A UEs and Application Clients.")
    parser.add_argument("--namespace", default="exp1-a", help="Target namespace (default: exp1-a)")
    parser.add_argument("--context", default="edge@edge", help="Kubernetes context (default: edge@edge)")
    args = parser.parse_args()
    
    undeploy_ues(namespace=args.namespace, context=args.context)

if __name__ == "__main__":
    main()
