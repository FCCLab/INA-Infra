"""Render profile templates into repos/ and push to Gitea."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import (
    IpPlan,
    PlApplyRequest,
    PlApplyResponse,
    PlPushRequest,
    PlPushResponse,
    PlSolveResponse,
    Profile,
    ProfileRecord,
    PlUndeployRequest,
    PlUndeployResponse,
)
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


class _LiteralStr(str):
    """Force YAML `|` block scalars so ConfigMap Python/HTML keeps indent."""


def _represent_literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(_LiteralStr, _represent_literal)


def _dump(doc: dict) -> str:
    if isinstance(doc, dict) and doc.get("kind") == "ConfigMap":
        data = doc.get("data")
        if isinstance(data, dict):
            doc = dict(doc)
            doc["data"] = {
                k: _LiteralStr(v) if isinstance(v, str) and ("\n" in v or len(v) > 80) else v
                for k, v in data.items()
            }
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
    )


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
    ipam_mode: str = "static",
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
        "ipam_mode": ipam_mode,
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
        "# slice | CU | UPF | APP | UPF_N3 | CUUP_N3 | UE_RF | APP_N6 | DNN",
    ]
    for s in ip_plan.slices:
        place = result.deploy_map.get(str(s.slice_id))
        cu = place.cu if place else s.site_cu
        upf = place.upf if place else s.site_upf
        app = place.app if place else s.site_app
        lines.append(
            f"# {s.n:>5} | {cu:<8} | {upf:<8} | {app:<8} | "
            f"{s.upf_n3} | {s.cuup_n3} | {s.ue_rf} | {s.app_ip} | {s.dnn_cidr}"
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
                        upf_n6="dhcp:10.1.137.0/24",
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
                for nad_name, role, addr, routes, ipam_mode in (
                    (f"upf-slice-{sl.n}-n3", "upf_n3", sl.upf_n3, None, "static"),
                    (f"upf-slice-{sl.n}-n4", "upf_n4", sl.upf_n4, None, "static"),
                    # N6: bare macvlan; UPF init dhclient → Glass 10.1.137 (no static IP).
                    (f"upf-slice-{sl.n}-n6", "upf_n6", "", None, "none"),
                ):
                    _write(
                        ns_dir / f"32-nad-{nad_name}.yaml",
                        _nad(
                            env,
                            namespace=ns,
                            nad_name=nad_name,
                            role=role,
                            address=addr or "0.0.0.0",
                            gateway=gw if ipam_mode == "static" else "0.0.0.0",
                            prefix_len=plen,
                            master=cluster_master,
                            slice_n=sl.n,
                            routes=routes,
                            ipam_mode=ipam_mode,
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

        # --- Render Application Servers on target cluster via GitOps ---
        from app.services import application_deploy, profile_store
        prof_rec = profile_store.get_profile(ns)
        if prof_rec and prof_rec.applications:
            for sl in ip_plan.slices:
                app_cfg = prof_rec.applications.get(str(sl.n))
                if app_cfg and app_cfg.enabled and app_cfg.app_type.lower() != "none":
                    app_cluster = application_deploy.resolve_target_cluster(
                        app_cfg, req.result
                    )
                    if cluster == app_cluster:
                        srv_manifests = application_deploy.generate_server_manifests(
                            ns, app_cfg, cluster, profile_subnet=ip_plan.subnet
                        )
                        for idx, doc in enumerate(srv_manifests):
                            kind = doc.get("kind", "Manifest").lower()
                            name = doc.get("metadata", {}).get("name", f"srv-{idx}")
                            _write(
                                ns_dir / f"60-app-{sl.n}-{name}-{kind}.yaml",
                                _dump(doc),
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


def _format_cluster_cleanup(notes: List[str]) -> str:
    """Align cluster cleanup notes for console display."""
    if not notes:
        return ""
    rows: List[Tuple[str, str]] = []
    for note in notes:
        text = (note or "").strip()
        if not text:
            continue
        if ": " in text:
            cluster, status = text.split(": ", 1)
        elif ":" in text:
            cluster, status = text.split(":", 1)
            status = status.lstrip()
        else:
            cluster, status = text, ""
        rows.append((cluster.strip(), status.strip()))
    if not rows:
        return ""
    width = max(len(c) for c, _ in rows)
    lines = ["Cluster cleanup"]
    for cluster, status in rows:
        lines.append(f"  {cluster:<{width}}  {status}")
    return "\n".join(lines)


def _format_undeploy_message(
    ns: str,
    *,
    ok: bool,
    n_paths: int,
    notes: List[str],
    empty_git: bool = False,
    exit_code: int | None = None,
) -> str:
    if not ok:
        return f"Undeploy push failed (exit {exit_code})"
    if empty_git:
        head = f"Undeployed  ns={ns}\nPaths       none (git already clean)"
    else:
        head = f"Undeployed  ns={ns}\nPaths       {n_paths} removed"
    cleanup = _format_cluster_cleanup(notes)
    return f"{head}\n{cleanup}" if cleanup else head


def _strip_namespaced_finalizers(
    base: List[str], resources: tuple[str, ...] = ("nfdeployment", "nfconfig")
) -> int:
    """Clear finalizers on listed namespaced resources. Returns objects touched."""
    patched = 0
    for resource in resources:
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
            # Empty list is more reliable than null across CRDs; JSON remove as fallback.
            ok = subprocess.run(
                [
                    *base,
                    "patch",
                    obj,
                    "--type",
                    "merge",
                    "-p",
                    '{"metadata":{"finalizers":[]}}',
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if ok.returncode != 0:
                subprocess.run(
                    [
                        *base,
                        "patch",
                        obj,
                        "--type",
                        "json",
                        "-p",
                        '[{"op":"remove","path":"/metadata/finalizers"}]',
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            patched += 1
    return patched


def _force_delete_profile_ns(cluster: str, ns: str, *, wait_sec: int = 90) -> str:
    """Best-effort cluster cleanup when Config Sync prune stalls on finalizers.

    RAN / OAI controllers add finalizers on NFDeployments (e.g.
    ``batch.tutorial.kubebuilder.io/finalizer``). After GitOps prune those CRs
    linger and leave the namespace ``Terminating``, which blocks RootSync
    (KNV2009). Strip finalizers, delete the namespace, and wait until gone.
    """
    from app.services import cluster_status
    import time as _time

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

    _strip_namespaced_finalizers(base)

    deleted = subprocess.run(
        [*kc, "delete", "ns", ns, "--wait=false", "--ignore-not-found=true"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    deadline = _time.monotonic() + max(5, wait_sec)
    while _time.monotonic() < deadline:
        phase = subprocess.run(
            [*kc, "get", "ns", ns, "-o", "jsonpath={.status.phase}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if phase.returncode != 0 or not (phase.stdout or "").strip():
            return f"{cluster}: deleted"
        _strip_namespaced_finalizers(base)
        # Namespace-level finalizers (rare) also block deletion.
        subprocess.run(
            [
                *kc,
                "patch",
                "ns",
                ns,
                "--type",
                "merge",
                "-p",
                '{"metadata":{"finalizers":[]}}',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        _time.sleep(2)

    if deleted.returncode != 0:
        err = (deleted.stderr or deleted.stdout or "").strip()
        if "NotFound" not in err and "not found" not in err.lower():
            return f"{cluster}: delete failed ({err[:120]})"
    phase2 = subprocess.run(
        [*kc, "get", "ns", ns, "-o", "jsonpath={.status.phase}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if phase2.returncode != 0 or not (phase2.stdout or "").strip():
        return f"{cluster}: deleted"
    return f"{cluster}: stuck {(phase2.stdout or 'unknown').strip()}"


def _profile_ns_clusters(ns: str, clusters: List[str]) -> List[str]:
    """Normalize cluster list for profile namespace ops."""
    out = [
        c
        for c in (clusters or ["central", "regional", "edge"])
        if c in CLUSTER_TO_REPO and c != "mgmt"
    ]
    for c in ("central", "regional", "edge"):
        if c not in out:
            out.append(c)
    return out


# Platform fallback when no profile SA remains on a shared OAI operator CRB.
_CRB_FALLBACK_NS = "oai-cn-operators"


def _clear_profile_crb_subjects(ns: str, clusters: List[str]) -> List[str]:
    """Drop profile SA subjects from shared cluster/ ClusterRoleBindings.

    Profile Apply used to replace the single CRB subject with the active
    profile, which broke multi-profile and left dead ina-infra* subjects after
    Clear. Clear/Undeploy removes this profile's subjects; if none remain,
    restore the oai-cn-operators placeholder (RAN CRBs are left alone).
    """
    repos = _repos_dir()
    touched: List[str] = []
    for cluster in clusters:
        repo = CLUSTER_TO_REPO.get(cluster)
        if not repo:
            continue
        cluster_dir = repos / repo / "cluster"
        if not cluster_dir.is_dir():
            continue
        for path in sorted(
            cluster_dir.glob(
                "clusterrolebinding-oai-*-operator-rolebinding-cluster.yaml"
            )
        ):
            # Leave RAN operator CRB on oai-slice-deployment.
            if "oai-ran-operator" in path.name:
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(doc, dict) or doc.get("kind") != "ClusterRoleBinding":
                continue
            subjects = list(doc.get("subjects") or [])
            kept = [s for s in subjects if s.get("namespace") != ns]
            if kept == subjects:
                continue
            if not kept:
                # Infer SA name from remaining role naming convention.
                sa = None
                for s in subjects:
                    if s.get("name"):
                        sa = s["name"]
                        break
                if not sa:
                    # oai-amf-operator-rolebinding-cluster → oai-amf-operator
                    stem = path.name.replace("clusterrolebinding-", "").replace(
                        "-rolebinding-cluster.yaml", ""
                    )
                    sa = stem
                kept = [
                    {
                        "kind": "ServiceAccount",
                        "name": sa,
                        "namespace": _CRB_FALLBACK_NS,
                    }
                ]
            doc["subjects"] = kept
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            touched.append(_rel(path))
    return touched


def _remove_profile_ns_dirs(ns: str, clusters: List[str]) -> List[str]:
    repos = _repos_dir()
    removed: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            removed.append(_rel(ns_dir))
    removed.extend(_clear_profile_crb_subjects(ns, clusters))
    return removed


def _list_profile_ns_files(ns: str, clusters: List[str]) -> List[str]:
    repos = _repos_dir()
    files: List[str] = []
    for cluster in clusters:
        ns_dir = repos / CLUSTER_TO_REPO[cluster] / "namespaces" / ns
        if not ns_dir.is_dir():
            continue
        for path in sorted(ns_dir.rglob("*")):
            if path.is_file():
                files.append(_rel(path))
    return files


def attach_local_deploy_files(rec: ProfileRecord) -> ProfileRecord:
    """Prefer on-disk namespaces/<profile>/ as the source of generated files.

    Keeps Clear/Push enabled after Generate even if SQLite deploy_files was
    cleared or never written (e.g. profile created outside the UI).
    """
    ns = rec.profile.name
    files = _list_profile_ns_files(ns, _profile_ns_clusters(ns, []))
    if files == list(rec.deploy_files):
        return rec
    return rec.model_copy(update={"deploy_files": files})


def undeploy_from_gitea(req: PlUndeployRequest) -> PlUndeployResponse:
    """Clear local namespaces/<profile>/; optionally push + cluster cleanup.

    dry_run=True  → Clear (local remove + clear deploy state; no push)
    dry_run=False → Undeploy (clear + push + force cluster cleanup)
    """
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        return PlUndeployResponse(
            ok=False, message="profile required", dry_run=req.dry_run
        )
    ns = profile.name
    clusters = _profile_ns_clusters(ns, req.clusters)

    removed = _remove_profile_ns_dirs(ns, clusters)

    if req.dry_run:
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
        return PlUndeployResponse(
            ok=True,
            dry_run=True,
            message=(
                f"Cleared local config for ns={ns} "
                f"({len(removed)} path(s)); push skipped"
            ),
            removed_paths=removed,
            deployed=False,
            profile=saved,
        )

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
        return PlUndeployResponse(
            ok=True,
            dry_run=False,
            message=_format_undeploy_message(
                ns, ok=True, n_paths=0, notes=cluster_notes, empty_git=True
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
    return PlUndeployResponse(
        ok=ok,
        dry_run=False,
        message=_format_undeploy_message(
            ns,
            ok=ok,
            n_paths=len(removed),
            notes=cluster_notes,
            exit_code=proc.returncode,
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


def push_to_gitea(req: PlPushRequest) -> PlPushResponse:
    """Push already-rendered repos to Gitea (no re-render)."""
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        return PlPushResponse(ok=False, message="profile required")
    ns = profile.name
    clusters = _profile_ns_clusters(ns, req.clusters)
    files = _list_profile_ns_files(ns, clusters)

    script = _push_script()
    if not script.is_file():
        return PlPushResponse(
            ok=False,
            message=f"Push script not found: {script}",
            written_files=files,
        )

    env = _gitea_push_env()
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
        # Push of remaining files → deployed; push after Clear (empty) → not deployed
        saved = profile_store.save_deploy_state(
            ns,
            deployed=bool(files),
            deploy_files=files,
            deploy_clusters=clusters if files else [],
        )
    else:
        prior = profile_store.get_profile(ns)
        saved = prior
    return PlPushResponse(
        ok=ok,
        message=(
            f"Pushed ns={ns} ({len(files)} file(s)) to Gitea"
            if ok
            else f"Push failed (exit {proc.returncode})"
        ),
        written_files=files,
        push_stdout=proc.stdout,
        push_stderr=proc.stderr,
        exit_code=proc.returncode,
        deployed=bool(saved.deployed) if saved else False,
        profile=saved,
    )


def iter_push_sse(req: PlPushRequest) -> Iterator[str]:
    """SSE stream for push-only (no re-render)."""
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        yield result_event(PlPushResponse(ok=False, message="profile required"))
        return

    ns = profile.name
    clusters = _profile_ns_clusters(ns, req.clusters)
    files = _list_profile_ns_files(ns, clusters)
    yield status_event(
        f"Pushing ns={ns} ({len(files)} local file(s)) for "
        f"{', '.join(clusters)}…"
    )

    script = _push_script()
    if not script.is_file():
        yield result_event(
            PlPushResponse(
                ok=False,
                message=f"Push script not found: {script}",
                written_files=files,
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
        saved = profile_store.save_deploy_state(
            ns,
            deployed=bool(files),
            deploy_files=files,
            deploy_clusters=clusters if files else [],
        )
    yield result_event(
        PlPushResponse(
            ok=ok,
            message=(
                f"Pushed ns={ns} ({len(files)} file(s)) to Gitea"
                if ok
                else f"Push failed (exit {cmd_result.returncode})"
            ),
            written_files=files,
            push_stdout=cmd_result.stdout,
            push_stderr=cmd_result.stderr,
            exit_code=cmd_result.returncode,
            deployed=bool(saved.deployed) if saved else False,
            profile=saved or profile_store.get_profile(ns),
        )
    )


def iter_undeploy_sse(req: PlUndeployRequest) -> Iterator[str]:
    """SSE: Clear (dry_run) or Undeploy (clear + push + cluster cleanup)."""
    from app.services import profile_store

    profile = req.profile
    if profile is None:
        yield result_event(
            PlUndeployResponse(ok=False, message="profile required", dry_run=req.dry_run)
        )
        return

    ns = profile.name
    clusters = _profile_ns_clusters(ns, req.clusters)

    yield status_event(
        f"{'Clearing' if req.dry_run else 'Undeploying'} namespace {ns} "
        f"from {', '.join(clusters)}…"
    )

    removed = _remove_profile_ns_dirs(ns, clusters)
    for path in removed:
        yield status_event(f"Removed {path}")

    if req.dry_run:
        saved = profile_store.save_deploy_state(
            ns,
            deployed=False,
            deploy_files=[],
            deploy_clusters=[],
        )
        yield result_event(
            PlUndeployResponse(
                ok=True,
                dry_run=True,
                message=(
                    f"Cleared local config for ns={ns} "
                    f"({len(removed)} path(s)); push skipped"
                ),
                removed_paths=removed,
                deployed=False,
                profile=saved,
            )
        )
        return

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
        yield result_event(
            PlUndeployResponse(
                ok=True,
                dry_run=False,
                message=_format_undeploy_message(
                    ns, ok=True, n_paths=0, notes=cluster_notes, empty_git=True
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
    yield result_event(
        PlUndeployResponse(
            ok=ok,
            dry_run=False,
            message=_format_undeploy_message(
                ns,
                ok=ok,
                n_paths=len(removed),
                notes=cluster_notes,
                exit_code=cmd_result.returncode,
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
