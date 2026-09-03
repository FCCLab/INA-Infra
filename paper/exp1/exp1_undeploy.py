#!/usr/bin/env python3
"""Unified Multi-Scheme Undeployment Utility for Experiment 1.

Supports:
  - Undeploying specific scheme: exp1-a, exp1-b, exp1-c, exp1-d
  - Undeploying all schemes (--all)
  - Selective cleanup (--ue-only, --workloads-only)

Usage:
  python3 paper/exp1/exp1_undeploy.py --scheme exp1-a
  python3 paper/exp1/exp1_undeploy.py --all
"""

import sys
import shutil
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
REPOS_DIR = REPO_ROOT / "repos"

import exp1_a_undeploy_ue

SCHEMES = ["exp1-a", "exp1-b", "exp1-c", "exp1-d"]

CLUSTERS = {
    "edge": {"context": "edge@edge", "repo": "edge-repo"},
    "regional": {"context": "regional@regional", "repo": "regional-repo"},
    "central": {"context": "central@central", "repo": "central-repo"},
}

def undeploy_single_scheme(scheme_id: str, ue_only: bool = False, workloads_only: bool = False, sync_gitops: bool = True):
    ns = scheme_id
    print(f"\n================================================================")
    print(f" Undeploying Experiment 1 Scheme: {scheme_id} (Namespace: {ns})")
    print(f"================================================================")
    
    # 1. Clean up UEs if requested
    if not workloads_only:
        print(f"\n[1/3] Removing UEs from namespace [{ns}] on Edge cluster...")
        exp1_a_undeploy_ue.undeploy_ues(namespace=ns, context="edge@edge")
        
    if ue_only:
        print(f"[OK] UE cleanup complete for {scheme_id}.")
        return

    # 2. Remove GitOps directories
    print(f"\n[2/3] Removing GitOps manifests for namespace [{ns}]...")
    removed_any = False
    for c_name, info in CLUSTERS.items():
        ns_dir = REPOS_DIR / info["repo"] / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            print(f"  - Deleted {ns_dir}")
            removed_any = True
            
    if sync_gitops and removed_any:
        push_script = REPO_ROOT / "bringup" / "03_push_to_git_repos" / "push_gitea_gitops.sh"
        if push_script.exists():
            print(f"\nPushing GitOps updates to Gitea/GitHub...")
            subprocess.run([str(push_script), "-m", f"exp1: undeploy {scheme_id}"], cwd=REPO_ROOT, check=False)
            
    # 3. Direct Kubernetes deletion
    print(f"\n[3/3] Purging namespace [{ns}] from physical clusters...")
    for c_name, info in CLUSTERS.items():
        ctx = info["context"]
        print(f"  - Purging [{ns}] on [{ctx}]...")
        subprocess.run([
            "kubectl", f"--context={ctx}", "delete", "deployment,svc,cm,secret,pod",
            "-n", ns, "--all", "--grace-period=0", "--force"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        subprocess.run([
            "kubectl", f"--context={ctx}", "delete", "namespace", ns,
            "--grace-period=0", "--force", "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    print(f"\n[OK] Scheme {scheme_id} completely undeployed.")

def main():
    parser = argparse.ArgumentParser(description="Undeploy Experiment 1 Schemes.")
    parser.add_argument("--scheme", choices=SCHEMES, default="exp1-a", help="Specific scheme to undeploy (default: exp1-a)")
    parser.add_argument("--all", action="store_true", help="Undeploy all schemes (exp1-a through exp1-d)")
    parser.add_argument("--ue-only", action="store_true", help="Only undeploy UE modems and clients")
    parser.add_argument("--workloads-only", action="store_true", help="Only undeploy server workloads and namespace")
    parser.add_argument("--no-git-sync", action="store_true", help="Skip pushing GitOps repo changes")
    args = parser.parse_args()
    
    sync = not args.no_git_sync
    
    if args.all:
        for sid in SCHEMES:
            undeploy_single_scheme(sid, ue_only=args.ue_only, workloads_only=args.workloads_only, sync_gitops=sync)
    else:
        undeploy_single_scheme(args.scheme, ue_only=args.ue_only, workloads_only=args.workloads_only, sync_gitops=sync)

if __name__ == "__main__":
    main()
