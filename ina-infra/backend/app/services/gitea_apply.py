"""Render PL planning intent into repos/ and push to Gitea."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import yaml

from app.schemas import PlApplyRequest, PlApplyResponse, PlSolveResponse

SITE_TO_CLUSTER = {0: "edge", 1: "regional", 2: "central"}
CLUSTER_TO_REPO = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
    "mgmt": "mgmt",
    "ue": "ue-repo",
}


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    # ina-infra/backend/app/services → parents[4] = repo root
    return Path(__file__).resolve().parents[4]


def _repos_dir() -> Path:
    env = os.environ.get("REPOS_DIR")
    if env:
        return Path(env).resolve()
    return _repo_root() / "repos"


def _push_script() -> Path:
    env = os.environ.get("PUSH_SCRIPT")
    if env:
        return Path(env).resolve()
    return _repo_root() / "bringup" / "03_push_to_git_repos" / "push_git_repos.sh"


def _affected_clusters(result: PlSolveResponse) -> List[str]:
    sites: set[int] = set()
    for p in result.deploy_map.values():
        sites.update([p.cu_id, p.upf_id, p.app_id])
    clusters = sorted({SITE_TO_CLUSTER[s] for s in sites if s in SITE_TO_CLUSTER})
    # Always include central as anchor for the planning intent
    if "central" not in clusters:
        clusters.insert(0, "central")
    return clusters


def _namespace_yaml() -> str:
    return """apiVersion: v1
kind: Namespace
metadata:
  name: ina-planning
  labels:
    app.kubernetes.io/name: ina-planning
    app.kubernetes.io/part-of: ina-infra
"""


def _configmap_yaml(
    cluster: str,
    result: PlSolveResponse,
    slices_payload: list,
) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cluster": cluster,
        "note": (
            "INA-Infra PlanningLayer intent. Config Sync applies this ConfigMap; "
            "it does not yet relocate OAI NFs from PL placement."
        ),
        "ok": result.ok,
        "message": result.message,
        "slices_input": slices_payload,
        "deploy_map": {k: v.model_dump() for k, v in result.deploy_map.items()},
        "resources": {k: v.model_dump() for k, v in result.resources.items()},
        "slices": [s.model_dump() for s in result.slices],
    }
    # Keep data as a single JSON string for ConfigMap
    body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "ina-pl-placement",
            "namespace": "ina-planning",
            "labels": {
                "app.kubernetes.io/name": "ina-pl-placement",
                "app.kubernetes.io/part-of": "ina-infra",
            },
        },
        "data": {
            "placement.json": json.dumps(payload, indent=2) + "\n",
        },
    }
    return yaml.safe_dump(body, sort_keys=False)


def _placement_doc(
    cluster: str,
    result: PlSolveResponse,
) -> str:
    lines = [
        f"# INA-Infra PL placement intent for cluster={cluster}",
        f"# generated_at={datetime.now(timezone.utc).isoformat()}",
        "#",
        "# slice_id | CU | UPF | APP | b_min | type",
        "#---------|----|-----|-----|-------|-----",
    ]
    for s in result.slices:
        p = s.placement
        b = s.resources.b_min
        lines.append(
            f"# {s.id:>7} | {p.cu:<8} | {p.upf:<8} | {p.app:<8} | "
            f"{b if b is not None else '-':>5} | {s.slice_type}"
        )
    lines.append("")
    doc = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "ina-pl-summary",
            "namespace": "ina-planning",
        },
        "data": {
            "summary.txt": "\n".join(lines) + "\n",
        },
    }
    return yaml.safe_dump(doc, sort_keys=False)


def render_intent(req: PlApplyRequest) -> Tuple[List[str], List[str]]:
    """Write ina-planning manifests. Returns (written_files, clusters_used)."""
    if not req.result.ok:
        raise ValueError("Cannot apply a failed PL result")

    repos = _repos_dir()
    clusters = req.clusters or _affected_clusters(req.result)
    # Ensure we write to every requested cluster that has a repo mapping
    clusters = [c for c in clusters if c in CLUSTER_TO_REPO]
    if not clusters:
        clusters = ["central", "regional", "edge"]

    slices_payload = [s.model_dump() for s in req.slices]
    written: List[str] = []

    for cluster in clusters:
        repo_name = CLUSTER_TO_REPO[cluster]
        ns_dir = repos / repo_name / "namespaces" / "ina-planning"
        ns_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "00-namespace.yaml": _namespace_yaml(),
            "10-placement-configmap.yaml": _configmap_yaml(
                cluster, req.result, slices_payload
            ),
            "20-summary-configmap.yaml": _placement_doc(cluster, req.result),
        }
        for name, content in files.items():
            path = ns_dir / name
            path.write_text(content, encoding="utf-8")
            written.append(str(path.relative_to(repos.parent) if repos.parent.exists() else path))

    return written, clusters


def apply_to_gitea(req: PlApplyRequest) -> PlApplyResponse:
    try:
        written, clusters = render_intent(req)
    except Exception as exc:  # noqa: BLE001
        return PlApplyResponse(ok=False, message=str(exc), dry_run=req.dry_run)

    if req.dry_run:
        return PlApplyResponse(
            ok=True,
            dry_run=True,
            message=f"Dry-run: wrote {len(written)} file(s) for {clusters}; push skipped",
            written_files=written,
        )

    script = _push_script()
    if not script.is_file():
        return PlApplyResponse(
            ok=False,
            message=f"Push script not found: {script}",
            written_files=written,
        )

    cmd = [
        "bash",
        str(script),
        "-m",
        req.commit_message,
        *clusters,
    ]
    env = os.environ.copy()
    env.setdefault("GITEA_HOST", "10.1.132.200")
    env.setdefault("GITEA_PORT", "3000")
    env.setdefault("GITEA_USER", "nephio")
    env.setdefault("GITEA_PASS", "secret")
    env["REPOS_DIR"] = str(_repos_dir())
    env["REPO_ROOT"] = str(_repo_root())

    proc = subprocess.run(
        cmd,
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    ok = proc.returncode == 0
    return PlApplyResponse(
        ok=ok,
        dry_run=False,
        message="Pushed to Gitea" if ok else f"Push failed (exit {proc.returncode})",
        written_files=written,
        push_stdout=proc.stdout,
        push_stderr=proc.stderr,
        exit_code=proc.returncode,
    )
