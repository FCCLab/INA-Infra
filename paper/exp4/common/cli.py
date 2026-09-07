"""Shared generate / deploy / undeploy entry points for Exp4 scheme dirs.

Each scheme directory puts itself on sys.path, then calls these helpers.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import scheme
from generate_gitops_lib import render

SCHEME_DIR = Path(scheme.__file__).resolve().parent
REPO_ROOT = SCHEME_DIR.parents[2]
OUT = SCHEME_DIR / "gitops_manifests"


def print_banner(action: str) -> None:
    print("=" * 64)
    print(f" {action} {scheme.SCHEME_NAME}  ns={scheme.NAMESPACE}")
    print(f"  PL={scheme.PL_ENABLED}  PM={scheme.PM_ENABLED}  PS={scheme.PS_ENABLED}")
    for sid, s in scheme.SLICES.items():
        print(f"  slice {sid} {s['name']}: {s['cu']}/{s['upf']}/{s['app']}")
    print("=" * 64)


def generate() -> None:
    render(scheme, OUT)


def copy_into_repos() -> None:
    repos_dir = REPO_ROOT / "repos"
    for cluster in scheme.CLUSTERS:
        repo = scheme.REPO_FOR[cluster]
        src = OUT / repo / "namespaces" / scheme.NAMESPACE
        dst = repos_dir / repo / "namespaces" / scheme.NAMESPACE
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.glob("*.yaml"):
            shutil.copy2(item, dst / item.name)
        print(f"Synced {src} → {dst}")


def push_gitea(message: str) -> None:
    script = REPO_ROOT / "bringup" / "03_push_to_git_repos" / "push_gitea_gitops.sh"
    if not script.exists():
        print(f"Notice: push script missing ({script}); skip Gitea push.")
        return
    print(f"Pushing GitOps to Gitea via {script} ...")
    res = subprocess.run([str(script), "-m", message], cwd=REPO_ROOT)
    if res.returncode != 0:
        print(f"Notice: GitOps push returned {res.returncode}")
    else:
        print("GitOps push successful.")


def main_generate() -> None:
    print_banner("Render")
    generate()


def main_deploy() -> None:
    parser = argparse.ArgumentParser(description=f"Deploy {scheme.SCHEME_ID}")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    print_banner("Deploy")
    generate()
    copy_into_repos()
    if not args.no_push:
        push_gitea(f"exp4: deploy {scheme.SCHEME_ID} ({scheme.SCHEME_NAME}) to {scheme.NAMESPACE}")
    rel = SCHEME_DIR.relative_to(REPO_ROOT)
    print()
    print("Next:")
    print("  export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge")
    print("  ./scripts/check-configsync.sh")
    print(f"  python3 {rel}/deploy_ue.py")
    print(f"  kubectl --context=central@central -n {scheme.NAMESPACE} get pods,nfdeployment")
    print(f"  kubectl --context=edge@edge -n {scheme.NAMESPACE} get pods")


def main_undeploy() -> None:
    parser = argparse.ArgumentParser(description=f"Undeploy {scheme.SCHEME_ID}")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--ue-only", action="store_true")
    args = parser.parse_args()
    print_banner("Undeploy")
    from ue_teardown import undeploy_ues

    undeploy_ues(namespace=scheme.NAMESPACE, context="edge@edge")
    if args.ue_only:
        return
    removed = False
    for cluster in scheme.CLUSTERS:
        ns_dir = REPO_ROOT / "repos" / scheme.REPO_FOR[cluster] / "namespaces" / scheme.NAMESPACE
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            print(f"  removed {ns_dir}")
            removed = True
    if removed and not args.no_push:
        push_gitea(f"exp4: undeploy {scheme.SCHEME_ID} from {scheme.NAMESPACE}")
    for cluster in scheme.CLUSTERS:
        ctx = scheme.KUBE_CONTEXT[cluster]
        subprocess.run(
            [
                "kubectl",
                f"--context={ctx}",
                "delete",
                "namespace",
                scheme.NAMESPACE,
                "--ignore-not-found=true",
                "--grace-period=0",
            ],
            check=False,
        )
        print(f"  deleted ns {scheme.NAMESPACE} on {ctx}")
    print("Done.")
