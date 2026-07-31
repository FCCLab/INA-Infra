"""Render profile templates into repos/ and push to Gitea."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import IpPlan, PlApplyRequest, PlApplyResponse, PlSolveResponse, Profile, PlUndeployRequest, PlUndeployResponse
from app.services import ip_allocator
from app.services import multus_iface
from app.services import paths as ina_paths
from app.services import ran_workloads
from app.services.cmd_stream import (
    CmdResult,
    error_event,
    log_event,
    result_event,
    status_event,
    stream_cmd,
)

SITE_TO_CLUSTER = {0: "edge", 1: "regional", 2: "central"}
CLUSTER_TO_REPO = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
    "mgmt": "mgmt",
    "ue": "ue-repo",
}

def _repo_root() -> Path:
    return ina_paths.repo_root()


def _repos_dir() -> Path:
    return ina_paths.repos_dir()


def _templates_dir() -> Path:
    return ina_paths.templates_dir()


def _push_script() -> Path:
    return ina_paths.push_script()


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_templates_dir())),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _rel(path: Path) -> str:
    for root in (_repos_dir(), ina_paths.ina_infra_root(), _repo_root()):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
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
    routes: List[dict] | None = None,
) -> str:
    ctx = {
        "namespace": namespace,
        "nad_name": nad_name,
        "role": role,
        "address": address,
        "gateway": gateway,
        "prefix_len": prefix_len,
        "master": master,
        "routes": routes or [],
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


def _stable_pl_message(message: str | None) -> str:
    """Strip ephemeral persist/warn suffixes so GitOps YAML stays bit-stable."""
    msg = (message or "").strip()
    for sep in ("; saved to ", "; wrote ", "; warn:"):
        if sep in msg:
            msg = msg.split(sep, 1)[0].rstrip()
    return msg


def _summary_txt(ip_plan: IpPlan, result: PlSolveResponse) -> str:
    lines = [
        f"# INA-Infra profile={ip_plan.profile.name} subnet={ip_plan.subnet}",
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
            f"# AMF_N2={sh.amf_n2} NRF_SBI={sh.nrf_sbi} SMF_N4={sh.smf_n4}",
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
        "cluster": cluster,
        "profile": profile.model_dump(),
        "note": (
            "INA-Infra profile Apply: Multus NADs + IP ConfigMaps under "
            f"namespaces/{profile.name}/; dedicated-core on central; "
            "edge gNB (CU-CP/DU/FlexRIC/UEs) + UPF+CU-UP on PL UPF sites. "
            "IP formula host=base[role]+n."
        ),
        "ok": result.ok,
        "message": _stable_pl_message(result.message),
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


def _am_nssai_sql_literal(n_slices: int) -> str:
    """SQL string literal for AM defaultSingleNssais (slices 1..N).

    OAI UDR v2.2.1 looks up ``ueid='00101' AND servingPlmnid=''`` and returns
    this list to AMF. Must match the profile's AMF ``plmn_support_list`` NSSAI
    count — a 4-slice seed on an N=1 profile blocks Registration Accept.
    """
    import json

    n = max(int(n_slices), 1)
    payload = {
        "defaultSingleNssais": [
            {"sst": 1, "sd": f"{i:06d}"} for i in range(1, n + 1)
        ]
    }
    # Match oai_db-basic.sql style: single-quoted JSON with \" escapes.
    return json.dumps(payload, separators=(", ", ": ")).replace('"', '\\"')


def _mysql_init_configmap(namespace: str, n_slices: int) -> str:
    """Wrap oai_db SQL in a ConfigMap; pin AM NSSAI to profile slice count."""
    sql_path = _templates_dir() / "core" / "mysql" / "oai_db-basic.sql"
    if not sql_path.is_file():
        raise FileNotFoundError(f"MySQL init SQL missing: {sql_path}")
    sql = sql_path.read_text(encoding="utf-8")
    if "INA_AM_NSSAI" not in sql:
        raise ValueError(
            f"{sql_path}: missing INA_AM_NSSAI placeholder for profile AM NSSAI"
        )
    sql = sql.replace("INA_AM_NSSAI", _am_nssai_sql_literal(n_slices))
    # YAML literal block; indent each SQL line by 4 spaces under the key.
    indented = "\n".join(("    " + line) if line else "" for line in sql.splitlines())
    return (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: mysql-initialization\n"
        f"  namespace: {namespace}\n"
        "  labels:\n"
        "    app: mysql\n"
        "    app.kubernetes.io/part-of: ina-infra\n"
        "    app.kubernetes.io/component: dedicated-core\n"
        "data:\n"
        "  oai_db-basic.sql: |\n"
        f"{indented}\n"
    )


def _write_dedicated_core(
    env: Environment,
    ns_dir: Path,
    *,
    namespace: str,
    ip_plan: IpPlan,
    written: List[str],
) -> None:
    """Emit MySQL + CP NFDeployments into the profile namespace on central."""
    shared = ip_plan.shared
    plen = shared.prefix_len
    gw = shared.gw_central
    slices = list(ip_plan.slices)
    n_slices = max(len(slices), 1)

    _write(
        ns_dir / "05-secret-mysql.yaml",
        _render(env, "core/05-secret-mysql.yaml.j2", namespace=namespace),
        written,
    )
    _write(
        ns_dir / "05-service-mysql.yaml",
        _render(env, "core/05-service-mysql.yaml.j2", namespace=namespace),
        written,
    )
    _write(
        ns_dir / "05-pvc-mysql.yaml",
        _render(env, "core/05-pvc-mysql.yaml.j2", namespace=namespace),
        written,
    )
    _write(
        ns_dir / "05-deployment-mysql.yaml",
        _render(env, "core/05-deployment-mysql.yaml.j2", namespace=namespace),
        written,
    )
    _write(
        ns_dir / "05-configmap-mysql-initialization.yaml",
        _mysql_init_configmap(namespace, n_slices),
        written,
    )

    for name, tmpl in (
        ("21-nfdeployment-ausf.yaml", "core/21-nfdeployment-ausf.yaml.j2"),
        ("22-nfdeployment-udm.yaml", "core/22-nfdeployment-udm.yaml.j2"),
        ("23-nfdeployment-udr.yaml", "core/23-nfdeployment-udr.yaml.j2"),
    ):
        _write(ns_dir / name, _render(env, tmpl, namespace=namespace), written)

    _write(
        ns_dir / "20-nfdeployment-nrf.yaml",
        _render(
            env,
            "core/20-nfdeployment-nrf.yaml.j2",
            namespace=namespace,
            nrf_sbi=shared.nrf_sbi,
            gateway=gw,
            prefix_len=plen,
        ),
        written,
    )

    _write(
        ns_dir / "24-nfdeployment-amf.yaml",
        _render(
            env,
            "core/24-nfdeployment-amf.yaml.j2",
            namespace=namespace,
            amf_n2=shared.amf_n2,
            gateway=gw,
            prefix_len=plen,
            max_subscribers=64,
        ),
        written,
    )
    _write(
        ns_dir / "24-nfconfig-amf.yaml",
        _render(
            env,
            "core/24-nfconfig-amf.yaml.j2",
            namespace=namespace,
            slices=slices,
        ),
        written,
    )
    _write(
        ns_dir / "25-nfdeployment-smf.yaml",
        _render(
            env,
            "core/25-nfdeployment-smf.yaml.j2",
            namespace=namespace,
            smf_n4=shared.smf_n4,
            gateway=gw,
            prefix_len=plen,
            max_nf_connections=max(n_slices, 5),
            max_sessions=500,
            slices=slices,
        ),
        written,
    )
    _write(
        ns_dir / "25-nfconfig-smf.yaml",
        _render(
            env,
            "core/25-nfconfig-smf.yaml.j2",
            namespace=namespace,
            slices=slices,
        ),
        written,
    )
    for sl in slices:
        # UPF may land on another cluster later; SMF Config carries Multus IPs/DNN.
        _write(
            ns_dir / f"25-config-smf-upf-slice-{sl.n}.yaml",
            _render(
                env,
                "core/25-config-smf-upf-slice.yaml.j2",
                namespace=namespace,
                upf_namespace=namespace,
                n=sl.n,
                upf_n3=sl.upf_n3,
                upf_n4=sl.upf_n4,
                upf_n6=sl.upf_n6,
                dnn_cidr=sl.dnn_cidr,
                gateway=gw,
                prefix_len=plen,
            ),
            written,
        )


def render_profile(req: PlApplyRequest) -> Tuple[List[str], List[str], str]:
    """Purge+rewrite namespaces/<profile>/ from templates.

    Returns ``(written_files, clusters, multus_master_note)``.
    """
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

    # Auto-detect Multus parent NICs (site plane 10.1.137.0/24) per cluster / RF node.
    masters = multus_iface.detect_masters_for_profile(
        clusters=list(clusters),
        du_node=profile.du_node,
        ue_node=profile.ue_node,
    )
    master_note = multus_iface.format_masters(masters)

    for cluster in clusters:
        repo_name = CLUSTER_TO_REPO[cluster]
        ns_dir = repos / repo_name / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
        ns_dir.mkdir(parents=True, exist_ok=True)

        gw = _gw_for_cluster(ip_plan, cluster)
        cluster_master = masters.get(cluster) or multus_iface.detect_cluster_master(
            cluster
        )
        du_master = masters.get("du") or multus_iface.detect_host_master(
            profile.du_node
        )
        ue_master = masters.get("ue") or multus_iface.detect_host_master(
            profile.ue_node
        )

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

        # --- central: dedicated-core IP CM + AMF/SMF NADs (+ optional CP stack) ---
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
                ("amf-core-n2", "amf_n2", shared.amf_n2),
                ("nrf-core-nnrf", "nrf_sbi", shared.nrf_sbi),
                ("smf-core-n4", "smf_n4", shared.smf_n4),
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
                        master=cluster_master,
                    ),
                    written,
                )
            if req.include_core:
                _write_dedicated_core(
                    env,
                    ns_dir,
                    namespace=ns,
                    ip_plan=ip_plan,
                    written=written,
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
                ("cucp-n2", "cucp_n2", shared.cucp_n2, cluster_master),
                ("cucp-f1c", "cucp_f1c", shared.cucp_f1c, cluster_master),
                ("cucp-e1", "cucp_e1", shared.cucp_e1, cluster_master),
                ("du-f1", "du_f1", shared.du_f1, du_master),
                ("du-rf", "du_rf", shared.du_rf, du_master),
                ("flexric-e2", "flexric_e2", shared.flexric_e2, cluster_master),
                ("xapp-e2", "xapp_e2", shared.xapp_e2, cluster_master),
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

        # --- per-slice: UPF on PL upf site; CU-UP on PL cu site (may differ) ---
        # Application site is planning-only — no APP Deployments are rendered.
        for sl in ip_plan.slices:
            place = req.result.deploy_map.get(str(sl.slice_id))
            upf_cluster = (
                SITE_TO_CLUSTER.get(place.upf_id, "central") if place else "central"
            )
            cu_cluster = (
                SITE_TO_CLUSTER.get(place.cu_id, "edge") if place else "edge"
            )
            if cluster in (upf_cluster, cu_cluster):
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

            if cluster == upf_cluster:
                for nad_name, role, addr, routes in (
                    (f"upf-slice-{sl.n}-n3", "upf_n3", sl.upf_n3, None),
                    (f"upf-slice-{sl.n}-n4", "upf_n4", sl.upf_n4, None),
                    (
                        f"upf-slice-{sl.n}-n6",
                        "upf_n6",
                        sl.upf_n6,
                        [{"dst": "10.1.132.0/24", "gw": gw}],
                    ),
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
                            master=cluster_master,
                            slice_n=sl.n,
                            routes=routes,
                        ),
                        written,
                    )

            if cluster == cu_cluster:
                for nad_name, role, addr in (
                    (f"cuup-slice{sl.n}-e1", "cuup_e1", sl.cuup_e1),
                    (f"cuup-slice{sl.n}-f1u", "cuup_f1u", sl.cuup_f1u),
                    (f"cuup-slice{sl.n}-n3", "cuup_n3", sl.cuup_n3),
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
                            master=cluster_master,
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
                        master=ue_master,
                        slice_n=sl.n,
                    ),
                    written,
                )

        if req.include_ran:
            ran_workloads.write_ran_for_cluster(
                ns_dir,
                cluster=cluster,
                namespace=ns,
                ip_plan=ip_plan,
                result=req.result,
                write=_write,
                written=written,
                du_node=profile.du_node,
                ue_node=profile.ue_node,
            )

    # OAI Kopf controllers co-locate in the profile ns (not ina-cn-operators).
    # Must run after the purge+rewrite above so 70-* artifacts survive.
    written.extend(
        _render_profile_oai_controllers(
            namespace=ns,
            smf_n4=shared.smf_n4,
            nrf_sbi=shared.nrf_sbi,
        )
    )

    return written, clusters, master_note


def _render_profile_oai_controllers(
    *, namespace: str, smf_n4: str, nrf_sbi: str
) -> List[str]:
    """Render OAI controllers into namespaces/<profile>/ (70-*); drop oai-cn-operators."""
    script = ina_paths.render_oai_controllers_script()
    if not script.is_file():
        raise FileNotFoundError(f"OAI controller render script missing: {script}")

    env = ina_paths.script_env()
    env["INA_SMF_N4"] = smf_n4
    # UPF op-conf / init wait: Multus Nnrf from this profile's IP plan (not
    # host env INA_NRF_* which defaults to ina-infra .140.11).
    env["INA_NRF_SBI_IP"] = nrf_sbi
    env["INA_NRF_LB_IP"] = nrf_sbi
    # Do not point UPFs at oai-cn NRF.
    env.pop("OAI_NRF_LB_IP", None)

    proc = subprocess.run(
        ["bash", str(script), namespace],
        cwd=str(ina_paths.ina_infra_root()),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"render_oai_controllers.sh failed (rc={proc.returncode}): {detail}"
        )

    written: List[str] = []
    repos = _repos_dir()
    for repo_name in ("central-repo", "regional-repo", "edge-repo"):
        ns_dir = repos / repo_name / "namespaces" / namespace
        if not ns_dir.is_dir():
            continue
        for path in sorted(ns_dir.glob("70-*")):
            if path.is_file():
                written.append(_rel(path))
    return written


def apply_to_gitea(req: PlApplyRequest) -> PlApplyResponse:
    from app.services import profile_store

    try:
        written, clusters, master_note = render_profile(req)
    except Exception as exc:  # noqa: BLE001
        return PlApplyResponse(ok=False, message=str(exc), dry_run=req.dry_run)

    profile = req.profile or req.result.profile
    profile_name = profile.name if profile else None

    if req.dry_run:
        saved = None
        if profile_name:
            prior = profile_store.get_profile(profile_name)
            saved = profile_store.save_deploy_state(
                profile_name,
                deployed=bool(prior.deployed) if prior else False,
                deploy_files=written,
                deploy_clusters=clusters,
                pl_result=req.result if req.result.ok else None,
            )
        return PlApplyResponse(
            ok=True,
            dry_run=True,
            message=(
                f"Dry deploy: wrote {len(written)} file(s) for {clusters}; "
                f"Multus parents [{master_note}]; push skipped"
            ),
            written_files=written,
            deployed=bool(saved.deployed) if saved else False,
            profile=saved,
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
    env = ina_paths.script_env()
    env.setdefault("GITEA_HOST", "10.1.132.200")
    env.setdefault("GITEA_PORT", "3000")
    env.setdefault("GITEA_USER", "nephio")
    env.setdefault("GITEA_PASS", "secret")

    proc = subprocess.run(
        cmd,
        cwd=str(ina_paths.ina_infra_root()),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    ok = proc.returncode == 0
    saved = None
    mysql_note = ""
    if ok and profile_name:
        saved = profile_store.save_deploy_state(
            profile_name,
            deployed=True,
            deploy_files=written,
            deploy_clusters=clusters,
            pl_result=req.result if req.result.ok else None,
        )
        # PVC MySQL keeps old AM NSSAI across Apply — sync live DB to profile N.
        n_slices = 0
        if req.result.ip_plan is not None:
            n_slices = int(req.result.ip_plan.n_slices or 0)
        if n_slices < 1 and req.result.slices:
            n_slices = len(req.result.slices)
        mysql_note = _patch_profile_mysql_live(profile_name, n_slices or 1)
    elif profile_name:
        # Still record attempted file list; leave deployed flag unchanged.
        prior = profile_store.get_profile(profile_name)
        saved = profile_store.save_deploy_state(
            profile_name,
            deployed=bool(prior.deployed) if prior else False,
            deploy_files=written,
            deploy_clusters=clusters,
            pl_result=req.result if req.result.ok else None,
        )
    msg = (
        f"Deployed to Gitea (Multus parents [{master_note}])"
        if ok
        else f"Deploy failed (exit {proc.returncode})"
    )
    if mysql_note:
        msg = f"{msg}; {mysql_note}"
    return PlApplyResponse(
        ok=ok,
        dry_run=False,
        message=msg,
        written_files=written,
        push_stdout=proc.stdout,
        push_stderr=proc.stderr,
        exit_code=proc.returncode,
        deployed=bool(saved.deployed) if saved else False,
        profile=saved,
    )


def _patch_profile_mysql_live(namespace: str, n_slices: int) -> str:
    """Best-effort live MySQL AM/SM patch for profile N (existing PVC)."""
    script = ina_paths.profile_patch_mysql_script()
    if not script.is_file():
        return f"mysql patch skipped (missing {script.name})"
    proc = subprocess.run(
        ["bash", str(script), namespace, "--slices", str(max(int(n_slices), 1))],
        cwd=str(ina_paths.ina_infra_root()),
        env=ina_paths.script_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode == 0:
        return f"mysql AM NSSAI synced to N={max(int(n_slices), 1)}"
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else f"rc={proc.returncode}"
    return f"mysql patch warn: {tail}"


def _force_delete_profile_ns(cluster: str, ns: str) -> str:
    """Best-effort cluster cleanup when Config Sync prune stalls on finalizers.

    OAI controllers add finalizers on NFDeployments (``*.openairinterface.org``).
    After GitOps prune, those CRs can linger and leave the namespace ``Terminating``.
    Strip finalizers then delete the namespace. Returns a short note.
    """
    from app.services import cluster_status

    kubeconfig = cluster_status._kubeconfig_for(cluster)
    context = cluster_status._context_for(cluster)
    kc = [
        "kubectl",
        "--kubeconfig",
        kubeconfig,
        "--context",
        context,
    ]
    base = [*kc, "-n", ns]

    check = subprocess.run(
        [*kc, "get", "ns", ns],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if check.returncode != 0:
        return f"{cluster}: absent"

    # Drop operator finalizers that block prune / namespace deletion.
    # Prefer merge patch (null) — JSON remove fails when the path is already gone.
    for resource in ("nfdeployment", "nfconfig"):
        listed = subprocess.run(
            [*base, "get", resource, "-o", "name"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        for line in (listed.stdout or "").splitlines():
            obj = line.strip()
            if not obj:
                continue
            subprocess.run(
                [
                    *base,
                    "patch",
                    obj,
                    "--type",
                    "merge",
                    "-p",
                    '{"metadata":{"finalizers":null}}',
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

    deleted = subprocess.run(
        [*kc, "delete", "ns", ns, "--wait=false"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # Re-check: if still Terminating with leftover NFDeployments, strip again.
    for _ in range(3):
        phase = subprocess.run(
            [*kc, "get", "ns", ns, "-o", "jsonpath={.status.phase}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if phase.returncode != 0 or not (phase.stdout or "").strip():
            return f"{cluster}: deleted"
        if (phase.stdout or "").strip() != "Terminating":
            break
        listed = subprocess.run(
            [*base, "get", "nfdeployment", "-o", "name"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        leftover = [ln.strip() for ln in (listed.stdout or "").splitlines() if ln.strip()]
        if not leftover:
            break
        for obj in leftover:
            subprocess.run(
                [
                    *base,
                    "patch",
                    obj,
                    "--type",
                    "merge",
                    "-p",
                    '{"metadata":{"finalizers":null}}',
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        import time as _time

        _time.sleep(1)

    if deleted.returncode != 0:
        err = (deleted.stderr or deleted.stdout or "").strip()
        # Already deleting is fine.
        if "NotFound" not in err and "not found" not in err.lower():
            return f"{cluster}: delete failed ({err[:120]})"
    phase2 = subprocess.run(
        [*kc, "get", "ns", ns, "-o", "jsonpath={.status.phase}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if phase2.returncode != 0:
        return f"{cluster}: deleted"
    return f"{cluster}: {(phase2.stdout or 'unknown').strip()}"


def undeploy_from_gitea(req: PlUndeployRequest) -> PlUndeployResponse:
    """Remove namespaces/<profile>/ from GitOps repos and push (Config Sync prunes)."""
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        return PlUndeployResponse(
            ok=False, message="profile required", dry_run=req.dry_run
        )
    ns = profile.name
    clusters = [
        c
        for c in (req.clusters or ["central", "regional", "edge"])
        if c in CLUSTER_TO_REPO and c != "mgmt"
    ]
    for c in ("central", "regional", "edge"):
        if c not in clusters:
            clusters.append(c)

    repos = _repos_dir()
    would_remove: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if ns_dir.exists():
            would_remove.append(_rel(ns_dir))

    if req.dry_run:
        return PlUndeployResponse(
            ok=True,
            dry_run=True,
            message=(
                f"Dry undeploy: would remove {len(would_remove)} path(s) "
                f"for ns={ns}; push skipped"
            ),
            removed_paths=would_remove,
            deployed=True,
            profile=profile_store.get_profile(ns),
        )

    removed: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            removed.append(_rel(ns_dir))

    cluster_notes: List[str] = []
    if not removed:
        # Git tree already clean — still force-clean live clusters (orphan prune stalls).
        for cluster in clusters:
            try:
                cluster_notes.append(_force_delete_profile_ns(cluster, ns))
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                cluster_notes.append(f"{cluster}: {exc}")
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
        note = "; ".join(cluster_notes) if cluster_notes else "no cluster cleanup"
        return PlUndeployResponse(
            ok=True,
            dry_run=False,
            message=(
                f"Nothing to remove in git for ns={ns}; "
                f"forced cluster cleanup: {note}"
            ),
            removed_paths=[],
            deployed=False,
            profile=saved,
        )

    script = _push_script()
    if not script.is_file():
        return PlUndeployResponse(
            ok=False,
            message=f"Push script not found: {script}",
            removed_paths=removed,
        )

    env = ina_paths.script_env()
    env["GITEA_HOST"] = os.environ.get("GITEA_HOST", "10.1.132.200")
    env["GITEA_PORT"] = os.environ.get("GITEA_PORT", "3000")
    env["GITEA_USER"] = os.environ.get("GITEA_USER", "nephio")
    env["GITEA_PASS"] = os.environ.get("GITEA_PASS", "secret")
    proc = subprocess.run(
        ["bash", str(script), "-m", req.commit_message, *clusters],
        cwd=str(ina_paths.ina_infra_root()),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    ok = proc.returncode == 0
    saved = None
    if ok:
        for cluster in clusters:
            try:
                cluster_notes.append(_force_delete_profile_ns(cluster, ns))
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                cluster_notes.append(f"{cluster}: {exc}")
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
    note = f"; cluster cleanup: {'; '.join(cluster_notes)}" if cluster_notes else ""
    return PlUndeployResponse(
        ok=ok,
        dry_run=False,
        message=(
            f"Undeployed ns={ns} ({len(removed)} path(s)){note}"
            if ok
            else f"Undeploy push failed (exit {proc.returncode})"
        ),
        removed_paths=removed,
        push_stdout=proc.stdout,
        push_stderr=proc.stderr,
        exit_code=proc.returncode,
        deployed=False if ok else True,
        profile=saved or profile_store.get_profile(ns),
    )


def _gitea_push_env() -> Dict[str, str]:
    env = ina_paths.script_env()
    env.setdefault("GITEA_HOST", "10.1.132.200")
    env.setdefault("GITEA_PORT", "3000")
    env.setdefault("GITEA_USER", "nephio")
    env.setdefault("GITEA_PASS", "secret")
    return env


def iter_apply_sse(req: PlApplyRequest) -> Iterator[str]:
    """SSE stream for deploy (render + optional Gitea push)."""
    from app.services import profile_store

    yield status_event("Rendering manifests…")
    try:
        written, clusters, master_note = render_profile(req)
    except Exception as exc:  # noqa: BLE001
        yield result_event(
            PlApplyResponse(ok=False, message=str(exc), dry_run=req.dry_run)
        )
        return

    profile = req.profile or req.result.profile
    profile_name = profile.name if profile else None
    yield status_event(
        f"Multus parents: {master_note}",
    )
    yield status_event(
        f"Wrote {len(written)} file(s) for clusters {', '.join(clusters)}",
        files=len(written),
        clusters=clusters,
    )

    if req.dry_run:
        saved = None
        if profile_name:
            prior = profile_store.get_profile(profile_name)
            saved = profile_store.save_deploy_state(
                profile_name,
                deployed=bool(prior.deployed) if prior else False,
                deploy_files=written,
                deploy_clusters=clusters,
                pl_result=req.result if req.result.ok else None,
            )
        yield result_event(
            PlApplyResponse(
                ok=True,
                dry_run=True,
                message=(
                    f"Dry deploy: wrote {len(written)} file(s) for {clusters}; "
                    f"Multus parents [{master_note}]; push skipped"
                ),
                written_files=written,
                deployed=bool(saved.deployed) if saved else False,
                profile=saved,
            )
        )
        return

    script = _push_script()
    if not script.is_file():
        yield result_event(
            PlApplyResponse(
                ok=False,
                message=f"Push script not found: {script}",
                written_files=written,
            )
        )
        return

    cmd = ["bash", str(script), "-m", req.commit_message, *clusters]
    yield status_event(f"$ {' '.join(cmd)}")
    cmd_result: CmdResult | None = None
    for item in stream_cmd(
        cmd, cwd=str(ina_paths.ina_infra_root()), env=_gitea_push_env()
    ):
        if isinstance(item, CmdResult):
            cmd_result = item
        else:
            stream, line = item
            yield log_event(stream, line)

    assert cmd_result is not None
    ok = cmd_result.returncode == 0
    saved = None
    mysql_note = ""
    if ok and profile_name:
        saved = profile_store.save_deploy_state(
            profile_name,
            deployed=True,
            deploy_files=written,
            deploy_clusters=clusters,
            pl_result=req.result if req.result.ok else None,
        )
        n_slices = 0
        if req.result.ip_plan is not None:
            n_slices = int(req.result.ip_plan.n_slices or 0)
        if n_slices < 1 and req.result.slices:
            n_slices = len(req.result.slices)
        yield status_event(
            f"Syncing MySQL AM NSSAI to N={max(n_slices, 1)}…"
        )
        mysql_note = _patch_profile_mysql_live(profile_name, n_slices or 1)
        yield status_event(mysql_note)
    elif profile_name:
        prior = profile_store.get_profile(profile_name)
        saved = profile_store.save_deploy_state(
            profile_name,
            deployed=bool(prior.deployed) if prior else False,
            deploy_files=written,
            deploy_clusters=clusters,
            pl_result=req.result if req.result.ok else None,
        )

    msg = (
        f"Deployed to Gitea (Multus parents [{master_note}])"
        if ok
        else f"Deploy failed (exit {cmd_result.returncode})"
    )
    if mysql_note:
        msg = f"{msg}; {mysql_note}"
    yield result_event(
        PlApplyResponse(
            ok=ok,
            dry_run=False,
            message=msg,
            written_files=written,
            push_stdout=cmd_result.stdout,
            push_stderr=cmd_result.stderr,
            exit_code=cmd_result.returncode,
            deployed=bool(saved.deployed) if saved else False,
            profile=saved,
        )
    )


def iter_undeploy_sse(req: PlUndeployRequest) -> Iterator[str]:
    """SSE stream for undeploy (git prune + push + cluster cleanup)."""
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        yield result_event(
            PlUndeployResponse(ok=False, message="profile required", dry_run=req.dry_run)
        )
        return

    ns = profile.name
    clusters = [
        c
        for c in (req.clusters or ["central", "regional", "edge"])
        if c in CLUSTER_TO_REPO and c != "mgmt"
    ]
    for c in ("central", "regional", "edge"):
        if c not in clusters:
            clusters.append(c)

    yield status_event(f"Undeploying namespace {ns} from {', '.join(clusters)}…")

    repos = _repos_dir()
    would_remove: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if ns_dir.exists():
            would_remove.append(_rel(ns_dir))

    if req.dry_run:
        yield result_event(
            PlUndeployResponse(
                ok=True,
                dry_run=True,
                message=(
                    f"Dry undeploy: would remove {len(would_remove)} path(s) "
                    f"for ns={ns}; push skipped"
                ),
                removed_paths=would_remove,
                deployed=True,
                profile=profile_store.get_profile(ns),
            )
        )
        return

    removed: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            removed.append(_rel(ns_dir))
            yield status_event(f"Removed {removed[-1]}")

    cluster_notes: List[str] = []
    if not removed:
        yield status_event("Git tree already clean; forcing cluster cleanup…")
        for cluster in clusters:
            try:
                note = _force_delete_profile_ns(cluster, ns)
                cluster_notes.append(note)
                yield status_event(note)
            except Exception as exc:  # noqa: BLE001
                note = f"{cluster}: {exc}"
                cluster_notes.append(note)
                yield error_event(note)
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
        note = "; ".join(cluster_notes) if cluster_notes else "no cluster cleanup"
        yield result_event(
            PlUndeployResponse(
                ok=True,
                dry_run=False,
                message=(
                    f"Nothing to remove in git for ns={ns}; "
                    f"forced cluster cleanup: {note}"
                ),
                removed_paths=[],
                deployed=False,
                profile=saved,
            )
        )
        return

    script = _push_script()
    if not script.is_file():
        yield result_event(
            PlUndeployResponse(
                ok=False,
                message=f"Push script not found: {script}",
                removed_paths=removed,
            )
        )
        return

    cmd = ["bash", str(script), "-m", req.commit_message, *clusters]
    yield status_event(f"$ {' '.join(cmd)}")
    cmd_result: CmdResult | None = None
    for item in stream_cmd(
        cmd, cwd=str(ina_paths.ina_infra_root()), env=_gitea_push_env()
    ):
        if isinstance(item, CmdResult):
            cmd_result = item
        else:
            stream, line = item
            yield log_event(stream, line)

    assert cmd_result is not None
    ok = cmd_result.returncode == 0
    saved = None
    if ok:
        yield status_event("Forcing cluster cleanup…")
        for cluster in clusters:
            try:
                note = _force_delete_profile_ns(cluster, ns)
                cluster_notes.append(note)
                yield status_event(note)
            except Exception as exc:  # noqa: BLE001
                note = f"{cluster}: {exc}"
                cluster_notes.append(note)
                yield error_event(note)
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
    note = f"; cluster cleanup: {'; '.join(cluster_notes)}" if cluster_notes else ""
    yield result_event(
        PlUndeployResponse(
            ok=ok,
            dry_run=False,
            message=(
                f"Undeployed ns={ns} ({len(removed)} path(s)){note}"
                if ok
                else f"Undeploy push failed (exit {cmd_result.returncode})"
            ),
            removed_paths=removed,
            push_stdout=cmd_result.stdout,
            push_stderr=cmd_result.stderr,
            exit_code=cmd_result.returncode,
            deployed=False if ok else True,
            profile=saved or profile_store.get_profile(ns),
        )
    )


# Back-compat alias
render_intent = render_profile
