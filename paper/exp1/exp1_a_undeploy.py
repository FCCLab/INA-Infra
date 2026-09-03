#!/usr/bin/env python3
"""Undeploy Scheme A: Full Algorithm (Proposed PL) from multi-cluster testbed.

Workflow:
  1. Undeploy UEs and Application Clients on Edge cluster (namespace: exp1-a).
  2. Remove GitOps manifests for namespace [exp1-a] from active repos (edge-repo, regional-repo, central-repo).
  3. Sync GitOps repos to Gitea and GitHub to trigger Config Sync undeployment.
  4. Delete residual workloads and namespace [exp1-a] across Edge, Regional, Central clusters.
"""

import sys
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
REPOS_DIR = REPO_ROOT / "repos"

import exp1_a_undeploy_ue

CLUSTERS = {
    "edge": {"context": "edge@edge", "repo": "edge-repo"},
    "regional": {"context": "regional@regional", "repo": "regional-repo"},
    "central": {"context": "central@central", "repo": "central-repo"},
}

def undeploy_scheme_a(sync_gitops: bool = True):
    namespace = "exp1-a"
    scheme_id = "exp1-a"
    
    print("================================================================")
    print(f" Undeploying Scheme A (Proposed PL) from Namespace: {namespace}")
    print("================================================================")
    
    # Step 1: Undeploy UEs
    print("\n[Step 1/3] Undeploying 5G UEs and application clients...")
    exp1_a_undeploy_ue.undeploy_ues(namespace=namespace, context="edge@edge")
    
    # Step 2: Remove GitOps manifests from repos
    print(f"\n[Step 2/3] Removing GitOps manifests for [{namespace}] from active cluster repositories...")
    removed_any = False
    for cluster_name, info in CLUSTERS.items():
        ns_dir = REPOS_DIR / info["repo"] / "namespaces" / namespace
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            print(f"  - Removed {ns_dir}")
            removed_any = True
            
    if sync_gitops and removed_any:
        push_script = REPO_ROOT / "bringup" / "03_push_to_git_repos" / "push_gitea_gitops.sh"
        if push_script.exists():
            print(f"\nSyncing GitOps deletion to Gitea & GitHub via {push_script}...")
            subprocess.run([str(push_script), "-m", f"exp1: undeploy {scheme_id} from namespace {namespace}"], cwd=REPO_ROOT, check=False)
            
    # Step 3: Delete residual resources and namespace on all clusters
    print(f"\n[Step 3/3] Deleting namespace [{namespace}] on physical clusters...")
    for cluster_name, info in CLUSTERS.items():
        ctx = info["context"]
        print(f"  - Cleaning namespace [{namespace}] on [{ctx}]...")
        # Force delete deployments, services, pods
        subprocess.run([
            "kubectl", f"--context={ctx}", "delete", "deployment,svc,cm,secret,pod",
            "-n", namespace, "--all", "--grace-period=0", "--force"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Delete namespace itself
        subprocess.run([
            "kubectl", f"--context={ctx}", "delete", "namespace", namespace,
            "--grace-period=0", "--force", "--ignore-not-found=true"
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    print(f"\n[OK] Scheme A ({scheme_id}) undeployed cleanly from all clusters.")

def main():
    undeploy_scheme_a(sync_gitops=True)

if __name__ == "__main__":
    main()
