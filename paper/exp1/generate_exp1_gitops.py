#!/usr/bin/env python3
"""Experiment 1: GitOps Manifest Generator & Physical Cluster Deployer with Dedicated Namespaces.

Generates complete GitOps multi-cluster manifests (ConfigMaps, Deployments, Services, NADs)
for each experiment placement scheme in its own dedicated namespace:
  1. exp1-a (Proposed PL: Slice 2->Edge, Slice 1->Regional, Slices 3&4->Central)
  2. exp1-b (Fixed Edge: All Slices 1..4 -> Edge Cluster)
  3. exp1-c (Fixed Regional: All Slices 1..4 -> Regional Cluster)
  4. exp1-d (Fixed Central: All Slices 1..4 -> Central Cluster)

Usage:
  # 1. Generate manifests for all schemes into paper/exp1/gitops_manifests/<namespace>/
  python3 paper/exp1/generate_exp1_gitops.py --generate

  # 2. Deploy a specific scheme to its dedicated namespace via GitOps:
  python3 paper/exp1/generate_exp1_gitops.py --deploy exp1-a
  python3 paper/exp1/generate_exp1_gitops.py --deploy exp1-b
  python3 paper/exp1/generate_exp1_gitops.py --deploy exp1-c
  python3 paper/exp1/generate_exp1_gitops.py --deploy exp1-d
"""

import os
import re
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
REPOS_DIR = REPO_ROOT / "repos"
GITOPS_OUT = HERE / "gitops_manifests"

SCHEMES = {
    "exp1-a": {
        "name": "Full Algorithm (Proposed PL)",
        "namespace": "exp1-a",
        "description": "Dynamic multi-cluster placement across Edge, Regional, Central",
        "placements": {
            1: {"cu": "Edge", "upf": "Regional", "app": "Regional", "cu_cluster": "edge", "upf_cluster": "regional"},
            2: {"cu": "Edge", "upf": "Edge", "app": "Edge", "cu_cluster": "edge", "upf_cluster": "edge"},
            3: {"cu": "Regional", "upf": "Central", "app": "Central", "cu_cluster": "regional", "upf_cluster": "central"},
            4: {"cu": "Central", "upf": "Central", "app": "Central", "cu_cluster": "central", "upf_cluster": "central"}
        }
    },
    "exp1-b": {
        "name": "Fixed Edge Baseline",
        "namespace": "exp1-b",
        "description": "Static placement of all slices strictly at Edge cluster",
        "placements": {
            1: {"cu": "Edge", "upf": "Edge", "app": "Edge", "cu_cluster": "edge", "upf_cluster": "edge"},
            2: {"cu": "Edge", "upf": "Edge", "app": "Edge", "cu_cluster": "edge", "upf_cluster": "edge"},
            3: {"cu": "Edge", "upf": "Edge", "app": "Edge", "cu_cluster": "edge", "upf_cluster": "edge"},
            4: {"cu": "Edge", "upf": "Edge", "app": "Edge", "cu_cluster": "edge", "upf_cluster": "edge"}
        }
    },
    "exp1-c": {
        "name": "Fixed Regional Baseline",
        "namespace": "exp1-c",
        "description": "Static placement of all slices strictly at Regional cluster",
        "placements": {
            1: {"cu": "Regional", "upf": "Regional", "app": "Regional", "cu_cluster": "regional", "upf_cluster": "regional"},
            2: {"cu": "Regional", "upf": "Regional", "app": "Regional", "cu_cluster": "regional", "upf_cluster": "regional"},
            3: {"cu": "Regional", "upf": "Regional", "app": "Regional", "cu_cluster": "regional", "upf_cluster": "regional"},
            4: {"cu": "Regional", "upf": "Regional", "app": "Regional", "cu_cluster": "regional", "upf_cluster": "regional"}
        }
    },
    "exp1-d": {
        "name": "Fixed Central Baseline",
        "namespace": "exp1-d",
        "description": "Static placement of all slices strictly at Central cluster",
        "placements": {
            1: {"cu": "Central", "upf": "Central", "app": "Central", "cu_cluster": "central", "upf_cluster": "central"},
            2: {"cu": "Central", "upf": "Central", "app": "Central", "cu_cluster": "central", "upf_cluster": "central"},
            3: {"cu": "Central", "upf": "Central", "app": "Central", "cu_cluster": "central", "upf_cluster": "central"},
            4: {"cu": "Central", "upf": "Central", "app": "Central", "cu_cluster": "central", "upf_cluster": "central"}
        }
    }
}

def generate_placement_configmap(scheme_id: str, scheme_info: dict) -> dict:
    """Generates the ina-pl-placement JSON document for a given scheme."""
    placements = scheme_info["placements"]
    ns = scheme_info["namespace"]
    deploy_map = {}
    slices = []
    
    slice_meta = {
        1: {"type": "CCTV", "t_bar": 10.0, "d_bar": 150.0, "h_s": 0, "eta": 2.0, "cpu": 9.8, "upf_c": 12.3, "app_c": 20.0, "mem": 1250.0, "gpu": 10.0, "b_min": 5.0},
        2: {"type": "Physical AI", "t_bar": 20.0, "d_bar": 20.0, "h_s": 1, "eta": 2.0, "cpu": 19.6, "upf_c": 24.7, "app_c": 40.0, "mem": 2500.0, "gpu": 20.0, "b_min": 10.0},
        3: {"type": "OTT", "t_bar": 40.0, "d_bar": 50.0, "h_s": 0, "eta": 2.5, "cpu": 39.2, "upf_c": 49.4, "app_c": 80.0, "mem": 5000.0, "gpu": 40.0, "b_min": 16.0},
        4: {"type": "IoT", "t_bar": 5.0, "d_bar": 150.0, "h_s": 0, "eta": 1.5, "cpu": 4.9, "upf_c": 6.2, "app_c": 10.0, "mem": 625.0, "gpu": 5.0, "b_min": 4.0}
    }
    
    tier_to_id = {"Edge": 0, "Regional": 1, "Central": 2}
    
    for s_id, p in placements.items():
        m = slice_meta[s_id]
        deploy_map[str(s_id)] = {
            "cu": p["cu"],
            "upf": p["upf"],
            "app": p["app"],
            "cu_id": tier_to_id[p["cu"]],
            "upf_id": tier_to_id[p["upf"]],
            "app_id": tier_to_id[p["app"]]
        }
        slices.append({
            "id": s_id,
            "slice_type": m["type"],
            "t_bar": m["t_bar"],
            "d_bar": m["d_bar"],
            "h_s": m["h_s"],
            "eta_t0": m["eta"],
            "placement": deploy_map[str(s_id)],
            "resources": {
                "a_c_cu": m["cpu"],
                "a_r_cu": 10.0,
                "a_c_upf": m["upf_c"],
                "a_r_upf": 10.0,
                "a_c_app": m["app_c"],
                "a_r_app": m["mem"],
                "a_g_app": m["gpu"],
                "b_min": m["b_min"],
                "b_ded": None
            }
        })
        
    placement_doc = {
        "cluster": "multi-cluster",
        "namespace": ns,
        "profile": {
            "name": ns,
            "subnet": "10.1.140.0/24",
            "max_slices": 16,
            "dnn_prefix": "10.140",
            "du_node": "usrp",
            "ue_node": "usrp"
        },
        "scheme_id": scheme_id,
        "scheme_name": scheme_info["name"],
        "deploy_map": deploy_map,
        "slices": slices
    }
    return placement_doc

TEMPLATE_DIR = HERE / "templates"

def transform_manifest_to_namespace(content: str, target_ns: str) -> str:
    """Replaces namespace declarations and references to the target dedicated namespace."""
    # Replace metadata namespace
    content = re.sub(r'namespace:\s*(ina-infra|exp1-[a-d])', f'namespace: {target_ns}', content)
    # Replace subjects namespace in RoleBindings
    content = re.sub(r'(\s+namespace:)\s*(ina-infra|exp1-[a-d])', rf'\1 {target_ns}', content)
    # Replace CLI flag --namespace=...
    content = re.sub(r'--namespace=(ina-infra|exp1-[a-d])', f'--namespace={target_ns}', content)
    # Inject container args --namespace for OAI operators
    if 'kind: Deployment' in content and ('oai-controller' in content or '-controller' in content):
        if f"--namespace={target_ns}" not in content:
            content = re.sub(r'(imagePullPolicy:\s*Always)', rf'\1\n        args:\n        - --namespace={target_ns}', content)
    # Replace Multus network namespace annotations like ina-infra/app-slice
    content = re.sub(r'(ina-infra|exp1-[a-d])/', f'{target_ns}/', content)
    return content

def render_scheme_manifests(scheme_id: str):
    """Renders manifests for a given scheme into its dedicated namespace directory."""
    scheme_info = SCHEMES[scheme_id]
    ns = scheme_info["namespace"]
    target_dir = GITOPS_OUT / scheme_id
    
    for cluster in ["edge-repo", "regional-repo", "central-repo"]:
        c_dir = target_dir / cluster / "namespaces" / ns
        c_dir.mkdir(parents=True, exist_ok=True)
        
        # Base namespace manifest
        ns_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {ns}
  labels:
    app.kubernetes.io/name: {ns}
    app.kubernetes.io/part-of: exp1
    ina.lab/scheme: {scheme_id}
"""
        with open(c_dir / "00-namespace.yaml", "w") as f:
            f.write(ns_yaml)
        
        # Copy and transform other manifests from template directory
        src_dir = TEMPLATE_DIR / cluster / "namespaces" / "exp1-a"
        if not src_dir.exists():
            src_dir = REPOS_DIR / cluster / "namespaces" / "ina-infra"
        if src_dir.exists():
            for item in src_dir.glob("*.yaml"):
                if item.name == "00-namespace.yaml" or "placement-configmap" in item.name:
                    continue
                with open(item, "r") as sf:
                    raw_text = sf.read()
                transformed = transform_manifest_to_namespace(raw_text, ns)
                with open(c_dir / item.name, "w") as df:
                    df.write(transformed)
                    
        # Generate dedicated 20-placement-configmap.yaml
        placement_doc = generate_placement_configmap(scheme_id, scheme_info)
        json_str = json.dumps(placement_doc, indent=2)
        indented_json = "\n".join(f"    {line}" for line in json_str.splitlines())
        cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: ina-pl-placement
  namespace: {ns}
  labels:
    app.kubernetes.io/name: ina-pl-placement
    app.kubernetes.io/part-of: exp1
    ina.lab/scheme: {scheme_id}
data:
  placement.json: |
{indented_json}
"""
        with open(c_dir / "20-placement-configmap.yaml", "w") as f:
            f.write(cm_yaml)
            
    print(f"Rendered dedicated GitOps manifests for [{scheme_id}] (Namespace: {ns}) into {target_dir}")

def deploy_scheme(scheme_id: str):
    """Copies rendered manifests to the dedicated namespace in active GitOps repos and syncs."""
    if scheme_id not in SCHEMES:
        print(f"Error: Unknown scheme {scheme_id}. Choices: {list(SCHEMES.keys())}")
        sys.exit(1)
        
    scheme_info = SCHEMES[scheme_id]
    ns = scheme_info["namespace"]
    src_scheme_dir = GITOPS_OUT / scheme_id
    if not src_scheme_dir.exists():
        render_scheme_manifests(scheme_id)
        
    print(f"=== Deploying Scheme [{scheme_id}] into Dedicated Namespace [{ns}] ===")
    
    for cluster in ["edge-repo", "regional-repo", "central-repo"]:
        src = src_scheme_dir / cluster / "namespaces" / ns
        dst = REPOS_DIR / cluster / "namespaces" / ns
        dst.mkdir(parents=True, exist_ok=True)
        
        if src.exists():
            for item in src.glob("*.yaml"):
                shutil.copy2(item, dst / item.name)
            print(f"Synced manifests to {dst}")
            
    # Push to Gitea to trigger Google Config Sync RootSync
    push_script = REPO_ROOT / "bringup" / "03_push_to_git_repos" / "push_gitea_gitops.sh"
    if push_script.exists():
        print(f"Pushing GitOps updates to Gitea via {push_script}...")
        res = subprocess.run([str(push_script), "-m", f"exp1: deploy {scheme_id} to namespace {ns}"], cwd=REPO_ROOT)
        if res.returncode == 0:
            print(f"GitOps push successful for {scheme_id} in namespace {ns}.")
        else:
            print("Notice: GitOps push returned status code:", res.returncode)
            
    print(f"Deployment of [{scheme_id}] to namespace [{ns}] initiated.")

def main():
    parser = argparse.ArgumentParser(description="GitOps Generator with Dedicated Namespaces for Experiment 1")
    parser.add_argument("--generate", action="store_true", help="Generate GitOps manifests for all schemes (exp1-a, exp1-b, exp1-c, exp1-d)")
    parser.add_argument("--deploy", choices=list(SCHEMES.keys()), help="Deploy specific scheme to its dedicated namespace")
    
    args = parser.parse_args()
    
    if args.generate or not args.deploy:
        for sid in SCHEMES.keys():
            render_scheme_manifests(sid)
            
    if args.deploy:
        deploy_scheme(args.deploy)

if __name__ == "__main__":
    main()
