"""Deploy / undeploy the fixed oai-benchmark GitOps stack (central + edge)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterator, List

import yaml

from app.schemas import (
    BenchmarkDeployRequest,
    BenchmarkDeployResponse,
    BenchmarkUndeployRequest,
    BenchmarkUndeployResponse,
)
from app.services import paths as ina_paths
from app.services.cmd_stream import (
    CmdResult,
    error_event,
    log_event,
    result_event,
    status_event,
    stream_cmd,
)
from app.services.gitea_apply import (
    CLUSTER_TO_REPO,
    _force_delete_profile_ns,
    _gitea_push_env,
    _push_script,
    _rel,
    _repos_dir,
)

BENCH_NS = "oai-benchmark"
DEFAULT_CLUSTERS = ("central", "edge")
# Also scrub leftovers from older multi-ns layouts / accidental regional renders.
EXTRA_CLEAN_CLUSTERS = ("regional",)


def _normalize_clusters(clusters: List[str] | None) -> List[str]:
    out: List[str] = []
    for c in clusters or list(DEFAULT_CLUSTERS):
        if c in CLUSTER_TO_REPO and c != "mgmt" and c not in out:
            out.append(c)
    if not out:
        out = list(DEFAULT_CLUSTERS)
    return out


def _list_bench_files(clusters: List[str]) -> List[str]:
    repos = _repos_dir()
    files: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / BENCH_NS
        if not ns_dir.is_dir():
            continue
        for path in sorted(ns_dir.rglob("*")):
            if path.is_file():
                files.append(_rel(path))
    # Cluster-scoped RAN operator RBAC rendered next to edge ns.
    edge_cluster = repos / "edge-repo" / "cluster"
    for name in (
        "clusterrole-oai-ran-operator-cluster-role.yaml",
        "clusterrolebinding-oai-ran-operator-rolebinding-cluster.yaml",
    ):
        path = edge_cluster / name
        if path.is_file():
            files.append(_rel(path))
    return files


def _clear_ran_operator_crb(ns: str = BENCH_NS) -> List[str]:
    """Drop oai-benchmark SA from edge RAN operator CRB; remove orphan RBAC."""
    repos = _repos_dir()
    touched: List[str] = []
    cluster_dir = repos / "edge-repo" / "cluster"
    if not cluster_dir.is_dir():
        return touched

    crb_path = cluster_dir / "clusterrolebinding-oai-ran-operator-rolebinding-cluster.yaml"
    cro_path = cluster_dir / "clusterrole-oai-ran-operator-cluster-role.yaml"

    if crb_path.is_file():
        try:
            doc = yaml.safe_load(crb_path.read_text(encoding="utf-8")) or {}
        except Exception:
            doc = {}
        if isinstance(doc, dict) and doc.get("kind") == "ClusterRoleBinding":
            subjects = list(doc.get("subjects") or [])
            kept = [s for s in subjects if s.get("namespace") != ns]
            if kept != subjects:
                if kept:
                    doc["subjects"] = kept
                    crb_path.write_text(
                        yaml.safe_dump(
                            doc, sort_keys=False, default_flow_style=False
                        ),
                        encoding="utf-8",
                    )
                    touched.append(_rel(crb_path))
                else:
                    crb_path.unlink(missing_ok=True)
                    touched.append(_rel(crb_path))
                    if cro_path.is_file():
                        cro_path.unlink(missing_ok=True)
                        touched.append(_rel(cro_path))

    return touched


def _bench_tracked_in_git(clusters: List[str]) -> List[str]:
    """Paths still tracked (or deleted-but-unpushed) under namespaces/oai-benchmark."""
    repos = _repos_dir()
    found: List[str] = []
    scrub = list(clusters)
    for c in EXTRA_CLEAN_CLUSTERS:
        if c not in scrub:
            scrub.append(c)
    for cluster in scrub:
        repo = CLUSTER_TO_REPO.get(cluster)
        if not repo:
            continue
        repo_dir = repos / repo
        if not (repo_dir / ".git").exists() and not (repo_dir / ".git").is_file():
            # Submodule may use .git file; still try git ls-files.
            if not repo_dir.is_dir():
                continue
        try:
            proc = subprocess.run(
                [
                    "git",
                    "ls-files",
                    "--",
                    f"namespaces/{BENCH_NS}",
                    "namespaces/oai-cn-benchmark",
                    "namespaces/oai-upf-benchmark",
                ],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except Exception:
            continue
        for line in (proc.stdout or "").splitlines():
            path = line.strip()
            if path:
                found.append(f"{repo}/{path}")
    return found


def _remove_bench_ns_dirs(clusters: List[str]) -> List[str]:
    repos = _repos_dir()
    removed: List[str] = []
    scrub = list(clusters)
    for c in EXTRA_CLEAN_CLUSTERS:
        if c not in scrub:
            scrub.append(c)
    for cluster in scrub:
        repo = CLUSTER_TO_REPO.get(cluster)
        if not repo:
            continue
        ns_dir = repos / repo / "namespaces" / BENCH_NS
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            removed.append(_rel(ns_dir))
        # Legacy multi-namespace dirs from older renders.
        for legacy in ("oai-cn-benchmark", "oai-upf-benchmark"):
            legacy_dir = repos / repo / "namespaces" / legacy
            if legacy_dir.exists():
                shutil.rmtree(legacy_dir)
                removed.append(_rel(legacy_dir))
    removed.extend(_clear_ran_operator_crb(BENCH_NS))
    return removed


def _render_script() -> Path:
    return ina_paths.render_oai_benchmark_script()


def _normalize_ran_node(name: str | None, default: str = "usrp") -> str:
    n = (name or "").strip()
    return n or default


def iter_deploy_sse(req: BenchmarkDeployRequest) -> Iterator[str]:
    """SSE: render oai-benchmark (+ optional Gitea push)."""
    clusters = _normalize_clusters(req.clusters)
    du_node = _normalize_ran_node(req.du_node)
    ue_node = _normalize_ran_node(req.ue_node, default=du_node)
    script = _render_script()
    if not script.is_file():
        yield result_event(
            BenchmarkDeployResponse(
                ok=False,
                dry_run=req.dry_run,
                message=f"Render script not found: {script}",
            )
        )
        return

    yield status_event(
        f"Rendering {BENCH_NS} via {script.name} "
        f"(DU={du_node} UE={ue_node})…"
    )
    env = ina_paths.script_env()
    env.setdefault("OAI_BENCHMARK_NS", BENCH_NS)
    env["OAI_BENCH_DU_NODE"] = du_node
    env["OAI_BENCH_UE_NODE"] = ue_node
    cmd = ["bash", str(script)]
    yield status_event(f"$ OAI_BENCH_DU_NODE={du_node} OAI_BENCH_UE_NODE={ue_node} {' '.join(cmd)}")

    cmd_result: CmdResult | None = None
    for item in stream_cmd(cmd, cwd=str(ina_paths.repo_root()), env=env):
        if isinstance(item, CmdResult):
            cmd_result = item
        else:
            stream, line = item
            yield log_event(stream, line)

    assert cmd_result is not None
    if cmd_result.returncode != 0:
        yield result_event(
            BenchmarkDeployResponse(
                ok=False,
                dry_run=req.dry_run,
                message=(
                    f"render_oai_benchmark_gitops.sh failed "
                    f"(exit {cmd_result.returncode})"
                ),
                push_stdout=cmd_result.stdout,
                push_stderr=cmd_result.stderr,
                exit_code=cmd_result.returncode,
            )
        )
        return

    written = _list_bench_files(clusters)
    yield status_event(
        f"Wrote {len(written)} file(s) for {', '.join(clusters)}",
        files=len(written),
        clusters=clusters,
    )

    if req.dry_run:
        yield result_event(
            BenchmarkDeployResponse(
                ok=True,
                dry_run=True,
                message=(
                    f"Dry deploy: rendered {len(written)} file(s) for "
                    f"{clusters}; DU={du_node} UE={ue_node}; push skipped"
                ),
                written_files=written,
                deployed=False,
            )
        )
        return

    # Clear Terminating / finalizer-stuck namespaces before GitOps apply
    # (otherwise RootSync KNV2009 blocks recreate of oai-benchmark).
    scrub = list(clusters)
    for c in EXTRA_CLEAN_CLUSTERS:
        if c not in scrub:
            scrub.append(c)
    yield status_event("Force-clearing cluster namespaces before push…")
    for cluster in scrub:
        try:
            note = _force_delete_profile_ns(cluster, BENCH_NS)
            yield status_event(note)
        except Exception as exc:  # noqa: BLE001
            yield error_event(f"{cluster}: {exc}")

    push = _push_script()
    if not push.is_file():
        yield result_event(
            BenchmarkDeployResponse(
                ok=False,
                message=f"Push script not found: {push}",
                written_files=written,
            )
        )
        return

    push_cmd = ["bash", str(push), "-m", req.commit_message, *clusters]
    yield status_event(f"$ {' '.join(push_cmd)}")
    push_result: CmdResult | None = None
    for item in stream_cmd(
        push_cmd, cwd=str(ina_paths.ina_infra_root()), env=_gitea_push_env()
    ):
        if isinstance(item, CmdResult):
            push_result = item
        else:
            stream, line = item
            yield log_event(stream, line)

    assert push_result is not None
    ok = push_result.returncode == 0

    # Operator create-once does not set Multus nodeSelectors; pin after Deployments exist.
    if ok:
        patch = ina_paths.patch_oai_benchmark_ran_script()
        if patch.is_file():
            yield status_event(
                f"Pinning RAN VPC / DU nodeSelector ({du_node}) via {patch.name}…"
            )
            patch_env = dict(env)
            patch_env["OAI_BENCH_DU_NODE"] = du_node
            # Patch script waits for create-once Deployments (default 240s).
            for item in stream_cmd(
                ["bash", str(patch)],
                cwd=str(ina_paths.repo_root()),
                env=patch_env,
                timeout=300,
            ):
                if isinstance(item, CmdResult):
                    if item.returncode != 0:
                        yield status_event(
                            f"RAN pin skipped/failed (exit {item.returncode}) — "
                            "re-run after oai-du exists: "
                            f"OAI_BENCH_DU_NODE={du_node} ./scripts/patch_oai_benchmark_ran_vpc.sh"
                        )
                    else:
                        yield status_event(f"RAN pinned: DU→{du_node}")
                else:
                    stream, line = item
                    yield log_event(stream, line)
        else:
            yield status_event(f"RAN patch script missing: {patch}")

    yield result_event(
        BenchmarkDeployResponse(
            ok=ok,
            dry_run=False,
            message=(
                f"Deployed {BENCH_NS} ({len(written)} file(s)) to Gitea "
                f"[{', '.join(clusters)}] DU={du_node} UE={ue_node}"
                if ok
                else f"Push failed (exit {push_result.returncode})"
            ),
            written_files=written,
            push_stdout=push_result.stdout,
            push_stderr=push_result.stderr,
            exit_code=push_result.returncode,
            deployed=ok,
        )
    )


def iter_undeploy_sse(req: BenchmarkUndeployRequest) -> Iterator[str]:
    """SSE: Clear (dry_run) or Undeploy (clear + push + cluster cleanup)."""
    clusters = _normalize_clusters(req.clusters)

    yield status_event(
        f"{'Clearing' if req.dry_run else 'Undeploying'} {BENCH_NS} "
        f"from {', '.join(clusters)}…"
    )

    removed = _remove_bench_ns_dirs(clusters)
    for path in removed:
        yield status_event(f"Removed {path}")

    # Working tree may already lack the dirs (prior Clear) while git still
    # tracks deletions / Gitea still has the manifests — Config Sync will
    # recreate the namespace unless we push.
    pending = _bench_tracked_in_git(clusters)
    if pending and not removed:
        yield status_event(
            f"Working tree already empty; {len(pending)} tracked path(s) "
            "still need a Gitea push to prune Config Sync"
        )
        for path in pending[:12]:
            yield status_event(f"Pending {path}")
        if len(pending) > 12:
            yield status_event(f"… and {len(pending) - 12} more")

    if req.dry_run:
        yield result_event(
            BenchmarkUndeployResponse(
                ok=True,
                dry_run=True,
                message=(
                    f"Cleared local {BENCH_NS} "
                    f"({len(removed)} path(s)"
                    + (
                        f", {len(pending)} still tracked — Push/Undeploy to sync"
                        if pending
                        else ""
                    )
                    + "); push skipped"
                ),
                removed_paths=removed or pending,
                deployed=False,
            )
        )
        return

    cluster_notes: List[str] = []
    need_push = bool(removed or pending)
    push_result: CmdResult | None = None
    ok = True

    if need_push:
        push = _push_script()
        if not push.is_file():
            yield result_event(
                BenchmarkUndeployResponse(
                    ok=False,
                    message=f"Push script not found: {push}",
                    removed_paths=removed or pending,
                )
            )
            return

        push_cmd = ["bash", str(push), "-m", req.commit_message, *clusters]
        yield status_event(f"$ {' '.join(push_cmd)}")
        for item in stream_cmd(
            push_cmd, cwd=str(ina_paths.ina_infra_root()), env=_gitea_push_env()
        ):
            if isinstance(item, CmdResult):
                push_result = item
            else:
                stream, line = item
                yield log_event(stream, line)

        assert push_result is not None
        ok = push_result.returncode == 0
    else:
        yield status_event(
            "Git already clean (no local or tracked oai-benchmark); "
            "forcing cluster cleanup…"
        )

    if ok:
        yield status_event("Force-cleaning cluster namespaces…")
        scrub = list(clusters)
        for c in EXTRA_CLEAN_CLUSTERS:
            if c not in scrub:
                scrub.append(c)
        for cluster in scrub:
            try:
                note = _force_delete_profile_ns(cluster, BENCH_NS)
                cluster_notes.append(note)
                yield status_event(note)
            except Exception as exc:  # noqa: BLE001
                note = f"{cluster}: {exc}"
                cluster_notes.append(note)
                yield error_event(note)

    cleanup = "; ".join(cluster_notes) if cluster_notes else ""
    n_paths = len(removed) or len(pending)
    if ok:
        msg = f"Undeployed {BENCH_NS}\nPaths       {n_paths} removed/pushed"
        if cleanup:
            msg += f"\nCleanup     {cleanup}"
    else:
        code = push_result.returncode if push_result else -1
        msg = f"Undeploy push failed (exit {code})"

    yield result_event(
        BenchmarkUndeployResponse(
            ok=ok,
            dry_run=False,
            message=msg,
            removed_paths=removed or pending,
            push_stdout=push_result.stdout if push_result else "",
            push_stderr=push_result.stderr if push_result else "",
            exit_code=push_result.returncode if push_result else None,
            deployed=False if ok else True,
        )
    )
