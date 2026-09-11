"""Shared generate / deploy / undeploy entry points for Exp4 scheme dirs.

Each scheme directory puts itself on sys.path, then calls these helpers.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import scheme
from generate_gitops_lib import render
from k8s_cleanup import hnc_fail_open, kubectl, wait_ns_gone

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
        cluster_src = OUT / repo / "cluster"
        if cluster_src.exists():
            cluster_dst = repos_dir / repo / "cluster"
            cluster_dst.mkdir(parents=True, exist_ok=True)
            for item in cluster_src.glob("*.yaml"):
                shutil.copy2(item, cluster_dst / item.name)
                print(f"Synced {item.name} → {cluster_dst}")


GITEA_HOST = os.environ.get("GITEA_HOST", "10.1.132.200")
GITEA_PORT = os.environ.get("GITEA_PORT", "3000")
GITEA_USER = os.environ.get("GITEA_USER", "nephio")
GITEA_PASS = os.environ.get("GITEA_PASS", "secret")


def _git_gitea(args: list[str], *, cwd: Path, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    instead = (
        f"url.http://{GITEA_USER}:{GITEA_PASS}@{GITEA_HOST}:{GITEA_PORT}/"
        f".insteadOf=http://{GITEA_HOST}:{GITEA_PORT}/"
    )
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-c", instead, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def push_gitea(message: str, *, full_push: bool = False) -> None:
    """Push GitOps to lab Gitea (Config Sync). Skip GitHub unless full_push."""
    if full_push:
        script = REPO_ROOT / "bringup" / "03_push_to_git_repos" / "push_gitea_gitops.sh"
        if not script.exists():
            print(f"Notice: push script missing ({script}); skip Gitea push.")
            return
        print(f"Pushing GitOps to Gitea+GitHub via {script} ...")
        res = subprocess.run([str(script), "-m", message], cwd=REPO_ROOT)
        if res.returncode != 0:
            print(f"Notice: GitOps push returned {res.returncode}")
        else:
            print("GitOps push successful.")
        return
    for cluster in scheme.CLUSTERS:
        src = REPO_ROOT / "repos" / scheme.REPO_FOR[cluster]
        if not src.exists():
            continue
        subprocess.run(["git", "add", "-A"], cwd=src, check=False)
        staged = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=src)
        if staged.returncode != 0:
            commit = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=nephio-gitops",
                    "-c",
                    "user.email=nephio@nephio.org",
                    "commit",
                    "-m",
                    message,
                ],
                cwd=src,
                capture_output=True,
                text=True,
            )
            if commit.returncode != 0:
                print(f"  warn: commit {src.name}: {(commit.stderr or commit.stdout or '').strip()}")
        print(f"  pushing gitea {src.name} …")
        pull = _git_gitea(["pull", "--no-edit", "--no-rebase", "gitea", "main"], cwd=src, timeout=45)
        if pull.returncode != 0:
            print(f"  warn: gitea pull {src.name}: {(pull.stderr or pull.stdout or '').strip()}")
            subprocess.run(["git", "merge", "--abort"], cwd=src, capture_output=True)
        push = _git_gitea(["push", "gitea", "HEAD:main"], cwd=src, timeout=60)
        if push.returncode != 0:
            print(f"  error: gitea push {src.name}: {(push.stderr or push.stdout or '').strip()}")
        else:
            print(f"  gitea {src.name} ok")


def main_generate() -> None:
    print_banner("Render")
    generate()


def main_deploy() -> None:
    parser = argparse.ArgumentParser(description=f"Deploy {scheme.SCHEME_ID}")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument(
        "--full-push",
        action="store_true",
        help="also mirror GitHub (default: Gitea only, Config Sync)",
    )
    args = parser.parse_args()
    print_banner("Deploy")
    generate()
    copy_into_repos()
    if not args.no_push:
        push_gitea(
            f"exp4: deploy {scheme.SCHEME_ID} ({scheme.SCHEME_NAME}) to {scheme.NAMESPACE}",
            full_push=args.full_push,
        )
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
    parser.add_argument(
        "--ue",
        action="append",
        default=[],
        metavar="ID",
        help="UE/slice id for --ue-only (repeatable or comma-separated; default all)",
    )
    parser.add_argument(
        "ues",
        nargs="*",
        help="UE/slice ids for --ue-only (default: all)",
    )
    parser.add_argument(
        "--full-push",
        action="store_true",
        help="also mirror GitHub (default: Gitea only)",
    )
    args = parser.parse_args()
    print_banner("Undeploy")
    from ue_teardown import parse_ue_ids, undeploy_ues

    ue_filter = list(args.ues) + list(args.ue)
    sids = parse_ue_ids(ue_filter)
    for cluster in scheme.CLUSTERS:
        hnc_fail_open(scheme.KUBE_CONTEXT[cluster])
    undeploy_ues(namespace=scheme.NAMESPACE, context="edge@edge", sids=sids)
    if args.ue_only or ue_filter:
        return
    removed = False
    for cluster in scheme.CLUSTERS:
        ns_dir = REPO_ROOT / "repos" / scheme.REPO_FOR[cluster] / "namespaces" / scheme.NAMESPACE
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            print(f"  removed {ns_dir}")
            removed = True
        cluster_dir = REPO_ROOT / "repos" / scheme.REPO_FOR[cluster] / "cluster"
        if cluster_dir.exists():
            for item in cluster_dir.glob(f"clusterrolebinding-*-operator-{scheme.NAMESPACE}.yaml"):
                item.unlink()
                print(f"  removed {item}")
                removed = True
    # Always push Gitea: if the ns dir was already gone locally, Config Sync
    # still serves the last Gitea commit and will recreate the namespace.
    if not args.no_push:
        push_gitea(
            f"exp4: undeploy {scheme.SCHEME_ID} from {scheme.NAMESPACE}",
            full_push=args.full_push,
        )
    for cluster in scheme.CLUSTERS:
        ctx = scheme.KUBE_CONTEXT[cluster]
        hnc_fail_open(ctx)
        kubectl(
            ctx,
            [
                "delete",
                "namespace",
                scheme.NAMESPACE,
                "--ignore-not-found=true",
                "--grace-period=0",
                "--wait=false",
            ],
            timeout=25,
        )
        print(f"  deleted ns {scheme.NAMESPACE} on {ctx}")
        # With --no-push, Config Sync will recreate the ns until Gitea is updated.
        if not args.no_push:
            wait_ns_gone(ctx, scheme.NAMESPACE, timeout_s=180)
    print("Done.")
