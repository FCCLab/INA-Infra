"""Render profile templates into repos/ and push to Gitea."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import IpPlan, PlApplyRequest, PlApplyResponse, PlSolveResponse, Profile
from app.services import ip_allocator

SITE_TO_CLUSTER = {0: "edge", 1: "regional", 2: "central"}
CLUSTER_TO_REPO = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
    "mgmt": "mgmt",
    "ue": "ue-repo",
}

# Default Multus parent NICs (usrp DU/UE use enp4s0f0 on edge).
MASTER_DEFAULT = "enp7s0"
MASTER_USRP = "enp4s0f0"


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def _repos_dir() -> Path:
    env = os.environ.get("REPOS_DIR")
    if env:
        return Path(env).resolve()
    return _repo_root() / "repos"


def _templates_dir() -> Path:
    env = os.environ.get("INA_TEMPLATES")
    if env:
        return Path(env).resolve()
    return _repo_root() / "ina-infra" / "templates"


def _push_script() -> Path:
    env = os.environ.get("PUSH_SCRIPT")
    if env:
        return Path(env).resolve()
    return _repo_root() / "bringup" / "03_push_to_git_repos" / "push_git_repos.sh"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_templates_dir())),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _rel(path: Path) -> str:
    root = _repo_root()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write(path: Path, content: str, written: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(_rel(path))


def _render(env: Environment, template: str, **ctx) -> str:
    return env.get_template(template).render(**ctx)


def _nad(
    env: Environment,
    *,
    namespace: str,
    nad_name: str,
    role: str,
    address: str,
    gateway: str,
    prefix_len: int,
    master: str,
    slice_n: int | None = None,
) -> str:
    ctx = {
        "namespace": namespace,
        "nad_name": nad_name,
        "role": role,
        "address": address,
        "gateway": gateway,
        "prefix_len": prefix_len,
        "master": master,
    }
    if slice_n is not None:
        ctx["slice_n"] = slice_n
    return _render(env, "profile/10-nad.yaml.j2", **ctx)


def _gw_for_cluster(ip_plan: IpPlan, cluster: str) -> str:
    s = ip_plan.shared
    return {
        "central": s.gw_central,
        "regional": s.gw_regional,
        "edge": s.gw_edge,
    }.get(cluster, s.gw_central)


def _summary_txt(ip_plan: IpPlan, result: PlSolveResponse) -> str:
    lines = [
        f"# INA-Infra profile={ip_plan.profile.name} subnet={ip_plan.subnet}",
        f"# generated_at={datetime.now(timezone.utc).isoformat()}",
        f"# N={ip_plan.n_slices}  formula: host = base[role] + n",
        "#",
        "# slice | CU | UPF | APP | UPF_N3 | CUUP_N3 | UE_RF | DNN",
    ]
    for s in ip_plan.slices:
        place = result.deploy_map.get(str(s.slice_id))
        cu = place.cu if place else s.site_cu
        upf = place.upf if place else s.site_upf
        app = place.app if place else s.site_app
        lines.append(
            f"# {s.n:>5} | {cu:<8} | {upf:<8} | {app:<8} | "
            f"{s.upf_n3} | {s.cuup_n3} | {s.ue_rf} | {s.dnn_cidr}"
        )
    sh = ip_plan.shared
    lines.extend(
        [
            "#",
            f"# AMF_N2={sh.amf_n2} SMF_N4={sh.smf_n4}",
            f"# CUCP N2/F1/E1={sh.cucp_n2}/{sh.cucp_f1c}/{sh.cucp_e1}",
            f"# DU F1/RF={sh.du_f1}/{sh.du_rf} FlexRIC={sh.flexric_e2}",
            "",
        ]
    )
    return "\n".join(lines)


def _placement_payload(
    cluster: str,
    profile: Profile,
    result: PlSolveResponse,
    ip_plan: IpPlan,
    slices_payload: list,
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cluster": cluster,
        "profile": profile.model_dump(),
        "note": (
            "INA-Infra profile Apply: Multus NADs + IP ConfigMaps under "
            f"namespaces/{profile.name}/. Dedicated-core NFDeploy CRs can be "
            "added alongside; IP formula host=base[role]+n."
        ),
        "ok": result.ok,
        "message": result.message,
        "slices_input": slices_payload,
        "deploy_map": {k: v.model_dump() for k, v in result.deploy_map.items()},
        "resources": {k: v.model_dump() for k, v in result.resources.items()},
        "slices": [s.model_dump() for s in result.slices],
        "ip_plan": ip_plan.model_dump(),
    }


def _clusters_for_apply(req: PlApplyRequest) -> List[str]:
    clusters = req.clusters or ["central", "regional", "edge"]
    clusters = [c for c in clusters if c in CLUSTER_TO_REPO and c != "mgmt"]
    # Always include all three so purge+rewrite stays consistent when N changes
    for c in ("central", "regional", "edge"):
        if c not in clusters:
            clusters.append(c)
    return clusters


def render_profile(req: PlApplyRequest) -> Tuple[List[str], List[str]]:
    """Purge+rewrite namespaces/<profile>/ from templates. Returns (files, clusters)."""
    if not req.result.ok:
        raise ValueError("Cannot apply a failed PL result")

    profile = req.profile or req.result.profile or Profile()
    from app.schemas import SliceIn

    if req.slices:
        slices = list(req.slices)
    elif req.result.slices:
        slices = [
            SliceIn(
                id=s.id,
                t_bar=s.t_bar,
                d_bar=s.d_bar,
                h_s=s.h_s,
                eta_t0=s.eta_t0,
                slice_type=s.slice_type,
            )
            for s in req.result.slices
        ]
    else:
        raise ValueError("No slices provided for apply")

    ip_plan = req.result.ip_plan
    if ip_plan is None:
        ip_plan = ip_allocator.allocate_profile_ips(
            profile, slices, req.result.deploy_map
        )

    env = _jinja_env()
    repos = _repos_dir()
    clusters = _clusters_for_apply(req)
    written: List[str] = []
    slices_payload = [s.model_dump() for s in slices]
    summary = _summary_txt(ip_plan, req.result)
    ns = profile.name
    shared = ip_plan.shared
    plen = shared.prefix_len

    for cluster in clusters:
        repo_name = CLUSTER_TO_REPO[cluster]
        ns_dir = repos / repo_name / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
        ns_dir.mkdir(parents=True, exist_ok=True)

        gw = _gw_for_cluster(ip_plan, cluster)

        _write(
            ns_dir / "00-namespace.yaml",
            _render(env, "profile/00-namespace.yaml.j2", namespace=ns),
            written,
        )

        placement = _placement_payload(
            cluster, profile, req.result, ip_plan, slices_payload
        )
        _write(
            ns_dir / "20-placement-configmap.yaml",
            _render(
                env,
                "profile/20-placement-configmap.yaml.j2",
                namespace=ns,
                placement_json=json.dumps(placement, indent=2),
                summary_txt=summary,
            ),
            written,
        )

        # --- central: dedicated-core IP CM + AMF/SMF NADs ---
        if cluster == "central":
            _write(
                ns_dir / "10-core-ips-configmap.yaml",
                _render(
                    env,
                    "core/10-core-ips-configmap.yaml.j2",
                    namespace=ns,
                    subnet=ip_plan.subnet,
                    shared=shared,
                ),
                written,
            )
            for nad_name, role, addr in (
                ("amf-n2", "amf_n2", shared.amf_n2),
                ("smf-n4", "smf_n4", shared.smf_n4),
            ):
                _write(
                    ns_dir / f"12-nad-{nad_name}.yaml",
                    _nad(
                        env,
                        namespace=ns,
                        nad_name=nad_name,
                        role=role,
                        address=addr,
                        gateway=gw,
                        prefix_len=plen,
                        master=MASTER_DEFAULT,
                    ),
                    written,
                )

        # --- edge: CU-CP / DU / FlexRIC / xApp NADs + RAN IP CM ---
        if cluster == "edge":
            _write(
                ns_dir / "10-ran-ips-configmap.yaml",
                _render(
                    env,
                    "cucp/10-ran-ips-configmap.yaml.j2",
                    namespace=ns,
                    shared=shared,
                ),
                written,
            )
            for nad_name, role, addr, master in (
                ("cucp-n2", "cucp_n2", shared.cucp_n2, MASTER_DEFAULT),
                ("cucp-f1c", "cucp_f1c", shared.cucp_f1c, MASTER_DEFAULT),
                ("cucp-e1", "cucp_e1", shared.cucp_e1, MASTER_DEFAULT),
                ("du-f1", "du_f1", shared.du_f1, MASTER_USRP),
                ("du-rf", "du_rf", shared.du_rf, MASTER_USRP),
                ("flexric-e2", "flexric_e2", shared.flexric_e2, MASTER_DEFAULT),
                ("xapp-e2", "xapp_e2", shared.xapp_e2, MASTER_DEFAULT),
            ):
                _write(
                    ns_dir / f"12-nad-{nad_name}.yaml",
                    _nad(
                        env,
                        namespace=ns,
                        nad_name=nad_name,
                        role=role,
                        address=addr,
                        gateway=gw,
                        prefix_len=plen,
                        master=master,
                    ),
                    written,
                )

        # --- per-slice: UPF/CU-UP on PL site cluster; UE always on edge ---
        for sl in ip_plan.slices:
            place = req.result.deploy_map.get(str(sl.slice_id))
            upf_cluster = (
                SITE_TO_CLUSTER.get(place.upf_id, "central") if place else "central"
            )
            cu_cluster = (
                SITE_TO_CLUSTER.get(place.cu_id, "edge") if place else "edge"
            )
            # Co-locate UPF+CU-UP on UPF site (lab convention); also emit CU-UP on cu site if different
            if cluster == upf_cluster:
                _write(
                    ns_dir / f"30-slice-{sl.n}-ips-configmap.yaml",
                    _render(
                        env,
                        "upf/10-slice-ips-configmap.yaml.j2",
                        namespace=ns,
                        n=sl.n,
                        slice_id=sl.slice_id,
                        upf_n3=sl.upf_n3,
                        upf_n4=sl.upf_n4,
                        upf_n6=sl.upf_n6,
                        cuup_e1=sl.cuup_e1,
                        cuup_f1u=sl.cuup_f1u,
                        cuup_n3=sl.cuup_n3,
                        ue_rf=sl.ue_rf,
                        dnn_cidr=sl.dnn_cidr,
                        site_cu=sl.site_cu,
                        site_upf=sl.site_upf,
                        site_app=sl.site_app,
                        cluster_cu=sl.cluster_cu,
                        cluster_upf=sl.cluster_upf,
                        cucp_e1=shared.cucp_e1,
                        smf_n4=shared.smf_n4,
                        du_rf=shared.du_rf,
                    ),
                    written,
                )
                for nad_name, role, addr in (
                    (f"upf{sl.n}-n3", "upf_n3", sl.upf_n3),
                    (f"upf{sl.n}-n4", "upf_n4", sl.upf_n4),
                    (f"upf{sl.n}-n6", "upf_n6", sl.upf_n6),
                    (f"cuup{sl.n}-e1", "cuup_e1", sl.cuup_e1),
                    (f"cuup{sl.n}-f1u", "cuup_f1u", sl.cuup_f1u),
                    (f"cuup{sl.n}-n3", "cuup_n3", sl.cuup_n3),
                ):
                    _write(
                        ns_dir / f"32-nad-{nad_name}.yaml",
                        _nad(
                            env,
                            namespace=ns,
                            nad_name=nad_name,
                            role=role,
                            address=addr,
                            gateway=gw,
                            prefix_len=plen,
                            master=MASTER_DEFAULT,
                            slice_n=sl.n,
                        ),
                        written,
                    )

            # If CU site differs from UPF site, still need CU-UP NADs there
            if cluster == cu_cluster and cu_cluster != upf_cluster:
                for nad_name, role, addr in (
                    (f"cuup{sl.n}-e1", "cuup_e1", sl.cuup_e1),
                    (f"cuup{sl.n}-f1u", "cuup_f1u", sl.cuup_f1u),
                    (f"cuup{sl.n}-n3", "cuup_n3", sl.cuup_n3),
                ):
                    _write(
                        ns_dir / f"32-nad-{nad_name}.yaml",
                        _nad(
                            env,
                            namespace=ns,
                            nad_name=nad_name,
                            role=role,
                            address=addr,
                            gateway=gw,
                            prefix_len=plen,
                            master=MASTER_DEFAULT,
                            slice_n=sl.n,
                        ),
                        written,
                    )

            if cluster == "edge":
                _write(
                    ns_dir / f"50-nad-ue{sl.n}-rf.yaml",
                    _nad(
                        env,
                        namespace=ns,
                        nad_name=f"ue{sl.n}-sim-rf",
                        role="ue_rf",
                        address=sl.ue_rf,
                        gateway=gw,
                        prefix_len=plen,
                        master=MASTER_USRP,
                        slice_n=sl.n,
                    ),
                    written,
                )

    return written, clusters


def apply_to_gitea(req: PlApplyRequest) -> PlApplyResponse:
    try:
        written, clusters = render_profile(req)
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


# Back-compat alias
render_intent = render_profile
