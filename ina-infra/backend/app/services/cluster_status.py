"""Query cluster Deployment readiness for a profile namespace (central/regional/edge)."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.ran_workloads import expected_deployments

# Expected dedicated-core Deployments on central (Apply include_core=true).
CORE_DEPLOYMENTS = (
    "mysql",
    "nrf-core",
    "ausf-core",
    "udm-core",
    "udr-core",
    "amf-core",
    "smf-core",
)

# cluster -> (kubeconfig env keys, default filename under ~/.kube, context)
_CLUSTER_SPECS: Dict[str, Tuple[Tuple[str, ...], str, str]] = {
    "mgmt": (("INA_MGMT_KUBECONFIG", "KUBECONFIG_MGMT"), "config", "mgmt@mgmt"),
    "central": (("INA_CENTRAL_KUBECONFIG", "KUBECONFIG_CENTRAL"), "config-central", "central@central"),
    "regional": (("INA_REGIONAL_KUBECONFIG", "KUBECONFIG_REGIONAL"), "config-regional", "regional@regional"),
    "edge": (("INA_EDGE_KUBECONFIG", "KUBECONFIG_EDGE"), "config-edge", "edge@edge"),
}

_SYNC_CLUSTERS = ("mgmt", "central", "regional", "edge")

_CONFIGSYNC_NS = "config-management-system"


def _rootsync_name(cluster: str) -> str:
    if cluster == "mgmt":
        return "mgmt"
    return f"{cluster}-repo"


def _short_commit(sha: Optional[str]) -> str:
    if not sha:
        return ""
    return sha[:7]


def _cond_status(conditions: List[dict], ctype: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    for c in conditions or []:
        if c.get("type") == ctype:
            return c.get("status"), c.get("reason"), c.get("message")
    return None, None, None


def _phase_err_count(phase: Optional[dict]) -> int:
    if not phase:
        return 0
    summary = phase.get("errorSummary") or {}
    try:
        return int(summary.get("totalCount") or 0)
    except (TypeError, ValueError):
        return 0


def _config_sync_status(cluster: str) -> Dict[str, Any]:
    """Query RootSync git sync progress (Config Sync) for a cluster."""
    name = _rootsync_name(cluster)
    raw, err = _kubectl_json(
        cluster,
        ["-n", _CONFIGSYNC_NS, "get", "rootsync", name],
        timeout=12.0,
    )
    base: Dict[str, Any] = {
        "name": name,
        "namespace": _CONFIGSYNC_NS,
        "exists": False,
        "overall": "missing",
        "summary": "RootSync missing",
        "source_commit": "",
        "render_commit": "",
        "sync_commit": "",
        "last_synced_commit": "",
        "syncing": False,
        "stalled": False,
        "reconciling": False,
        "error_count": 0,
        "message": "",
        "updated_at": None,
        "error": None,
    }
    if err:
        if "NotFound" in err:
            return base
        base["overall"] = "error"
        base["summary"] = "kubectl error"
        base["error"] = err
        return base
    if not raw:
        return base

    spec_git = ((raw.get("spec") or {}).get("git")) or {}
    st = raw.get("status") or {}
    conditions = st.get("conditions") or []
    source = st.get("source") or {}
    rendering = st.get("rendering") or {}
    sync = st.get("sync") or {}

    source_commit = source.get("commit") or ""
    render_commit = rendering.get("commit") or ""
    sync_commit = sync.get("commit") or ""
    last_synced = st.get("lastSyncedCommit") or ""

    syncing_s, _, syncing_msg = _cond_status(conditions, "Syncing")
    stalled_s, _, stalled_msg = _cond_status(conditions, "Stalled")
    reconciling_s, _, _ = _cond_status(conditions, "Reconciling")
    syncing = syncing_s == "True"
    stalled = stalled_s == "True"
    reconciling = reconciling_s == "True"

    err_count = (
        _phase_err_count(source)
        + _phase_err_count(rendering)
        + _phase_err_count(sync)
    )
    updated = (
        sync.get("lastUpdate")
        or rendering.get("lastUpdate")
        or source.get("lastUpdate")
    )
    message = (syncing_msg or stalled_msg or "").strip()

    short_src = _short_commit(source_commit)
    short_applied = _short_commit(last_synced or sync_commit)
    commits_aligned = bool(
        source_commit
        and source_commit == sync_commit
        and source_commit == last_synced
    )

    if stalled or err_count > 0:
        overall = "error"
        summary = "Stalled" if stalled and err_count == 0 else "Errors"
    elif syncing:
        overall = "syncing"
        if short_src and short_applied and short_src != short_applied:
            # Git moved; cluster still catching up.
            summary = f"Syncing {short_applied}→{short_src}"
        elif commits_aligned and short_src:
            # Commit fetched+recorded, but apply still running (e.g. ns Terminating).
            summary = f"Applying {short_src}"
        else:
            summary = f"Syncing {short_src}" if short_src else "Syncing"
    elif commits_aligned and not syncing:
        overall = "synced"
        summary = f"Synced {short_src}" if short_src else "Synced"
    elif last_synced and err_count == 0 and not syncing:
        overall = "synced"
        summary = f"Synced {_short_commit(last_synced)}"
    elif reconciling:
        overall = "syncing"
        summary = "Reconciling"
    elif source_commit or sync_commit:
        overall = "syncing"
        summary = "Settling"
    else:
        overall = "unknown"
        summary = "Unknown"

    return {
        "name": name,
        "namespace": _CONFIGSYNC_NS,
        "exists": True,
        "overall": overall,
        "summary": summary,
        "source_commit": short_src,
        "render_commit": _short_commit(render_commit),
        "sync_commit": _short_commit(sync_commit),
        "last_synced_commit": _short_commit(last_synced),
        "syncing": syncing,
        "stalled": stalled,
        "reconciling": reconciling,
        "error_count": err_count,
        "message": message[:160] if message else "",
        "updated_at": updated,
        "error": None,
        "repo": spec_git.get("repo") or "",
        "branch": spec_git.get("branch") or "",
    }


def _kubeconfig_for(cluster: str) -> str:
    env_keys, filename, _ctx = _CLUSTER_SPECS[cluster]
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            return val
    home = Path.home()
    cand = home / ".kube" / filename
    if cand.is_file():
        return str(cand)
    return os.environ.get("KUBECONFIG", str(home / ".kube" / "config"))


def _context_for(cluster: str) -> str:
    env_map = {
        "mgmt": "INA_MGMT_CONTEXT",
        "central": "INA_CENTRAL_CONTEXT",
        "regional": "INA_REGIONAL_CONTEXT",
        "edge": "INA_EDGE_CONTEXT",
    }
    return os.environ.get(env_map.get(cluster, ""), _CLUSTER_SPECS[cluster][2])


def _kubectl_json(
    cluster: str,
    args: List[str],
    timeout: float = 15.0,
) -> tuple[Optional[dict], Optional[str]]:
    cmd = [
        "kubectl",
        "--kubeconfig",
        _kubeconfig_for(cluster),
        "--context",
        _context_for(cluster),
        *args,
        "-o",
        "json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None, "kubectl not found on API host"
    except subprocess.TimeoutExpired:
        return None, "kubectl timed out"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return None, err
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid kubectl json: {exc}"


def _is_ue_deployment(name: str) -> bool:
    """On-demand UE (and extra client UE) Deployments — omit from status bar."""
    return str(name).startswith("oai-ue-")


def _infer_kind(name: str, item: Optional[dict] = None) -> str:
    labels = ((item or {}).get("metadata") or {}).get("labels") or {}
    role = str(labels.get("ina.lab/role") or labels.get("ina-infra.nephio.lab/role") or "").lower()
    if role in ("server", "app"):
        return "app"
    if role == "client":
        return "client"
    if role in ("ue_rf", "cu_cp", "du", "cu_up", "flexric"):
        return "ran"
    if name in CORE_DEPLOYMENTS:
        return "core"
    if name.startswith("oai-ue-") and "-client-" in name:
        return "client"
    if name.startswith(("oai-", "upf-slice-")):
        return "ran"
    if name == "cctv" or (
        name.startswith("slice")
        and (
            "-physical-ai" in name
            or "-ott-" in name
            or "-iot-" in name
            or "-custom-" in name
        )
    ):
        return "app"
    return "other"


def _deploy_row(name: str, item: Optional[dict], kind: str = "") -> Dict[str, Any]:
    kind = kind or _infer_kind(name, item)
    if item is None:
        return {
            "name": name,
            "exists": False,
            "ready": 0,
            "desired": 0,
            "available": 0,
            "up_to_date": 0,
            "ready_text": "—",
            "status": "Missing",
            "ok": False,
            "kind": kind,
        }
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    desired = int(spec.get("replicas") if spec.get("replicas") is not None else 1)
    ready = int(status.get("readyReplicas") or 0)
    available = int(status.get("availableReplicas") or 0)
    up_to_date = int(status.get("updatedReplicas") or 0)
    ok = desired > 0 and ready >= desired
    return {
        "name": name,
        "exists": True,
        "ready": ready,
        "desired": desired,
        "available": available,
        "up_to_date": up_to_date,
        "ready_text": f"{ready}/{desired}",
        "status": "Running" if ok else ("Progressing" if ready > 0 else "NotReady"),
        "ok": ok,
        "kind": kind or _infer_kind(name, item),
    }


def _placement_hints(cluster: str, namespace: str) -> Tuple[int, Dict[str, Any]]:
    """Read placement ConfigMap for n_slices + deploy_map (best-effort)."""
    raw, err = _kubectl_json(cluster, ["-n", namespace, "get", "cm", "ina-pl-placement"])
    if err or not raw:
        return 0, {}
    data = (raw.get("data") or {})
    blob = data.get("placement.json") or data.get("placement") or ""
    if not blob:
        return 0, {}
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return 0, {}
    deploy_map = payload.get("deploy_map") or {}
    ip_plan = payload.get("ip_plan") or {}
    n_slices = int(ip_plan.get("n_slices") or len(payload.get("slices") or []) or 0)
    return n_slices, deploy_map


def _status_one_cluster(
    cluster: str,
    namespace: str,
    *,
    expected: List[str],
) -> Dict[str, Any]:
    ns_obj, ns_err = _kubectl_json(cluster, ["get", "ns", namespace])
    namespace_exists = ns_obj is not None and ns_err is None
    namespace_phase = None
    if namespace_exists:
        namespace_phase = ((ns_obj or {}).get("status") or {}).get("phase")

    deploy_list: Dict[str, dict] = {}
    list_err: Optional[str] = None
    if namespace_exists:
        raw, list_err = _kubectl_json(cluster, ["-n", namespace, "get", "deploy"])
        if raw and isinstance(raw.get("items"), list):
            for it in raw["items"]:
                name = ((it.get("metadata") or {}).get("name")) or ""
                if name:
                    deploy_list[name] = it
        elif list_err and "NotFound" in list_err:
            list_err = None

    expected = [n for n in expected if not _is_ue_deployment(n)]
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for name in expected:
        if _is_ue_deployment(name):
            continue
        ordered.append(_deploy_row(name, deploy_list.get(name)))
        seen.add(name)
    for name in sorted(deploy_list.keys()):
        if name not in seen and not _is_ue_deployment(name):
            ordered.append(_deploy_row(name, deploy_list[name]))

    if not namespace_exists:
        err = None
        if ns_err and "NotFound" not in ns_err:
            err = ns_err
        summary = "Namespace missing"
        overall = "missing"
    elif list_err:
        summary = f"kubectl error: {list_err}"
        overall = "error"
        err = list_err
    elif not any(d["exists"] for d in ordered):
        summary = "No Deployments"
        overall = "empty"
        err = None
    else:
        expected_rows = (
            [d for d in ordered if d["name"] in expected] if expected else ordered
        )
        if expected and all(d["exists"] and d["ok"] for d in expected_rows):
            if cluster == "central" and all(
                any(d["name"] == n and d["ok"] for d in ordered) for n in CORE_DEPLOYMENTS
            ):
                summary = (
                    "Core Ready"
                    if len(expected) <= len(CORE_DEPLOYMENTS)
                    else "Ready"
                )
            else:
                summary = "Ready"
            overall = "ready"
        elif any(d["exists"] and not d["ok"] for d in ordered):
            summary = "Degraded"
            overall = "degraded"
        elif expected and any(not d["exists"] for d in expected_rows):
            summary = "Partial"
            overall = "partial"
        elif all(d["ok"] for d in ordered if d["exists"]):
            summary = f"{sum(1 for d in ordered if d['exists'])} Ready"
            overall = "ready"
        else:
            summary = "Degraded"
            overall = "degraded"
        err = None

    config_sync = _config_sync_status(cluster)

    return {
        "cluster": cluster,
        "context": _context_for(cluster),
        "namespace": namespace,
        "namespace_exists": namespace_exists,
        "namespace_phase": namespace_phase,
        "overall": overall,
        "summary": summary,
        "error": err,
        "deployments": ordered,
        "expected": list(expected),
        "config_sync": config_sync,
    }


def _deploy_map_from_pl(result: Any) -> Tuple[int, Dict[str, Any]]:
    """Extract n_slices + deploy_map from a saved PlSolveResponse (best-effort)."""
    if result is None or not getattr(result, "ok", False):
        return 0, {}
    dm_raw = getattr(result, "deploy_map", None) or {}
    deploy_map: Dict[str, Any] = {}
    for k, v in dm_raw.items():
        if hasattr(v, "model_dump"):
            deploy_map[str(k)] = v.model_dump()
        elif isinstance(v, dict):
            deploy_map[str(k)] = v
    ip = getattr(result, "ip_plan", None)
    n_slices = 0
    if ip is not None:
        n_slices = int(getattr(ip, "n_slices", 0) or 0)
    if n_slices <= 0:
        slices = getattr(result, "slices", None) or []
        n_slices = len(slices) or len(deploy_map)
    return n_slices, deploy_map


def profile_cluster_status(namespace: str) -> Dict[str, Any]:
    """Return per-cluster Deployment status for the profile namespace."""
    from app.services import profile_store

    clusters = ("central", "regional", "edge")
    rec = profile_store.get_profile(namespace)
    deployed = bool(rec.deployed) if rec is not None else False

    # Live placement CM first (only present while namespace exists).
    n_slices, deploy_map = _placement_hints("central", namespace)
    if n_slices <= 0:
        for c in ("regional", "edge"):
            n_slices, deploy_map = _placement_hints(c, namespace)
            if n_slices > 0:
                break
    live_placement = n_slices > 0

    # Saved PL only when still marked deployed (show expected Missing while syncing).
    if not live_placement and deployed and rec is not None:
        n_slices, deploy_map = _deploy_map_from_pl(rec.pl_result)

    expected_by: Dict[str, List[str]] = {c: [] for c in clusters}
    # Same pattern on all clusters:
    #   undeployed → empty lists (no phantom core-only Missing on central)
    #   deployed / live → full expected (core + UPF/CU-UP/RAN per placement)
    if (deployed or live_placement) and n_slices > 0:
        for c in clusters:
            expected_by[c] = expected_deployments(
                c,
                n_slices=n_slices,
                deploy_map=deploy_map,
                include_core=True,
                include_ran=True,
            )
            if rec is not None:
                from app.services import application_deploy

                expected_by[c].extend(
                    application_deploy.expected_app_deployments(c, rec)
                )

    results: Dict[str, Dict[str, Any]] = {}

    def _run(c: str) -> Tuple[str, Dict[str, Any]]:
        return c, _status_one_cluster(c, namespace, expected=expected_by[c])

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(_run, c) for c in clusters]
        for fut in as_completed(futs):
            name, st = fut.result()
            results[name] = st

    any_ns_live = any(results[c].get("namespace_exists") for c in clusters)

    # Undeployed and nothing live: keep all clusters identical (empty + Not deployed).
    if not deployed and not any_ns_live:
        for c in clusters:
            st = results[c]
            st["deployments"] = []
            st["expected"] = []
            st["summary"] = "Not deployed"
            st["overall"] = "missing"

    ordered_clusters = [results[c] for c in clusters]
    central = results["central"]
    others = [results["regional"], results["edge"]]

    if not deployed and not any_ns_live:
        overall = "missing"
        summary = "Not deployed"
    elif any(c["overall"] == "error" for c in ordered_clusters):
        overall = "error"
        summary = "Cluster query error"
    elif all(c["overall"] == "missing" for c in ordered_clusters):
        overall = "missing"
        summary = "Namespaces missing"
    elif central["overall"] == "ready" and all(
        o["overall"] in ("empty", "ready", "missing", "partial") for o in others
    ):
        if any(o["overall"] == "missing" for o in others):
            overall = "partial"
            summary = "Core ready · site NS missing"
        elif any(o["overall"] in ("partial", "empty", "degraded") for o in others):
            overall = "partial"
            summary = "Core ready · sites syncing"
        else:
            overall = "ready"
            summary = "Ready"
    elif central["overall"] in ("degraded", "partial"):
        overall = central["overall"]
        summary = f"Central {central['summary']}"
    elif central["overall"] == "missing":
        overall = "missing"
        summary = "Central namespace missing"
    elif central["overall"] == "empty":
        overall = "empty"
        summary = "No central Deployments"
    else:
        overall = central["overall"]
        summary = central["summary"]

    # Config Sync: mgmt + workload clusters (mgmt has UI GitOps, not profile NFs).
    sync_by: Dict[str, Dict[str, Any]] = {
        c: (results[c].get("config_sync") or {}) for c in clusters
    }
    sync_by["mgmt"] = _config_sync_status("mgmt")

    config_syncs = [
        {
            "cluster": c,
            "context": _context_for(c),
            "config_sync": sync_by[c],
        }
        for c in _SYNC_CLUSTERS
    ]

    sync_states = [
        (row.get("config_sync") or {}).get("overall", "unknown") for row in config_syncs
    ]
    if any(s == "error" for s in sync_states):
        sync_overall = "error"
        sync_summary = "Config Sync errors"
    elif any(s == "missing" for s in sync_states):
        sync_overall = "missing"
        sync_summary = "RootSync missing"
    elif any(s == "syncing" for s in sync_states):
        sync_overall = "syncing"
        sync_summary = "Config Sync in progress"
    elif all(s == "synced" for s in sync_states):
        sync_overall = "synced"
        sync_summary = "All clusters synced"
    else:
        sync_overall = "unknown"
        sync_summary = "Config Sync unknown"

    return {
        "namespace": namespace,
        "cluster": "central",
        "context": _context_for("central"),
        "namespace_exists": central["namespace_exists"],
        "namespace_phase": central["namespace_phase"],
        "overall": overall,
        "summary": summary,
        "error": next((c["error"] for c in ordered_clusters if c.get("error")), None),
        "deployments": central["deployments"],
        "expected": central.get("expected") or [],
        "clusters": ordered_clusters,
        "config_syncs": config_syncs,
        "config_sync_overall": sync_overall,
        "config_sync_summary": sync_summary,
    }


def list_edge_nodes() -> Dict[str, Any]:
    """Auto-detect Ready edge cluster nodes for DU / UE placement."""
    from app.services import multus_iface

    raw, err = _kubectl_json("edge", ["get", "nodes"], timeout=20.0)
    if err or not raw:
        return {
            "cluster": "edge",
            "nodes": [],
            "error": err or "kubectl returned empty",
            "default_du": "usrp",
            "default_ue": "usrp",
        }

    nodes: List[Dict[str, Any]] = []
    for item in raw.get("items") or []:
        md = item.get("metadata") or {}
        status = item.get("status") or {}
        name = md.get("name") or ""
        if not name:
            continue
        labels = md.get("labels") or {}
        roles = sorted(
            k.replace("node-role.kubernetes.io/", "")
            for k in labels
            if k.startswith("node-role.kubernetes.io/")
        )
        ready = False
        for cond in status.get("conditions") or []:
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready = True
                break
        internal_ip = ""
        for addr in status.get("addresses") or []:
            if addr.get("type") == "InternalIP":
                internal_ip = addr.get("address") or ""
                break
        cap = status.get("capacity") or {}
        master = ""
        try:
            master = multus_iface.detect_host_master(name)
        except Exception:
            master = ""
        nodes.append(
            {
                "name": name,
                "ready": ready,
                "internal_ip": internal_ip,
                "roles": roles,
                "multus_master": master,
                "capacity_cpu": str(cap.get("cpu") or ""),
                "capacity_memory": str(cap.get("memory") or ""),
            }
        )

    # Prefer usrp for rfsim defaults; else first Ready non-control-plane worker.
    ready_names = [n["name"] for n in nodes if n["ready"]]
    workers = [
        n["name"]
        for n in nodes
        if n["ready"] and "control-plane" not in (n.get("roles") or [])
    ]
    default = "usrp"
    if "usrp" in ready_names:
        default = "usrp"
    elif workers:
        default = workers[0]
    elif ready_names:
        default = ready_names[0]

    # Sort cluster nodes in alphabetical order.
    nodes.sort(key=lambda n: n["name"].lower())

    return {
        "cluster": "edge",
        "nodes": nodes,
        "error": None,
        "default_du": default,
        "default_ue": default,
    }


_CLIENT_SIDECAR_NAMES = {
    "cctv-publisher",
    "aiperf",
    "ott-client",
    "iot-client",
    "custom-client",
}


def _ue_deploy_name(slice_id: int, client_index: int) -> str:
    return f"oai-ue-slice-{int(slice_id)}-client-{int(client_index)}"


def _parse_ue_slice_id(name: str) -> Optional[int]:
    m = re.match(r"^oai-ue-slice-(\d+)", str(name))
    if m:
        return int(m.group(1))
    m_legacy = re.match(r"^oai-ue-(\d+)", str(name))
    if m_legacy:
        return int(m_legacy.group(1))
    return None


def _has_client_sidecar(item: Optional[dict]) -> bool:
    if not item:
        return False
    containers = (
        ((item.get("spec") or {}).get("template") or {}).get("spec") or {}
    ).get("containers") or []
    names = {str(c.get("name") or "") for c in containers if isinstance(c, dict)}
    return bool(names & _CLIENT_SIDECAR_NAMES)


def _ue_item_row(name: str, item: Optional[dict]) -> Dict[str, Any]:
    row = _deploy_row(name, item, kind="client")
    sidecar = _has_client_sidecar(item)
    return {
        "name": name,
        "exists": bool(row.get("exists")),
        "ready": int(row.get("ready") or 0),
        "desired": int(row.get("desired") or 0),
        "ready_text": str(row.get("ready_text") or "—"),
        "status": str(row.get("status") or "Missing"),
        "ok": bool(row.get("ok")),
        "client_sidecar": sidecar,
    }


def profile_ue_client_status(profile_name: str) -> Dict[str, Any]:
    """Live edge status for on-demand oai-ue-* client Deployments (not the status bar)."""
    from app.services import profile_store

    rec = profile_store.get_profile(profile_name)
    namespace = profile_name
    ns_obj, ns_err = _kubectl_json("edge", ["get", "ns", namespace])
    namespace_exists = ns_obj is not None and ns_err is None

    live_by_name: Dict[str, dict] = {}
    list_err: Optional[str] = None
    if namespace_exists:
        raw, list_err = _kubectl_json("edge", ["-n", namespace, "get", "deploy"])
        if raw and isinstance(raw.get("items"), list):
            for it in raw["items"]:
                name = str(((it.get("metadata") or {}).get("name")) or "")
                if _is_ue_deployment(name):
                    live_by_name[name] = it

    slice_ids: List[int] = []
    apps: Dict[str, Any] = {}
    if rec is not None:
        apps = dict(rec.applications or {})
        slice_ids = [int(s.id) for s in (rec.slices or [])]
    for key in apps:
        try:
            sid = int(key)
        except (TypeError, ValueError):
            continue
        if sid not in slice_ids:
            slice_ids.append(sid)
    for name in live_by_name:
        sid = _parse_ue_slice_id(name)
        if sid is not None and sid not in slice_ids:
            slice_ids.append(sid)
    slice_ids.sort()

    slices_out: Dict[str, Any] = {}
    for sid in slice_ids:
        cfg = apps.get(str(sid))
        params = {}
        if cfg is not None:
            params = getattr(cfg, "params", None) or {}
            if not isinstance(params, dict):
                params = {}
        expected_n = int(params.get("client_count") or params.get("client_replicas") or 1)
        enabled = True
        app_type = "none"
        if cfg is not None:
            enabled = bool(getattr(cfg, "enabled", True))
            app_type = str(getattr(cfg, "app_type", "") or "none").lower()
        if not enabled or app_type == "none":
            expected_n = 0
        else:
            live_clients = sum(
                1
                for n, it in live_by_name.items()
                if _parse_ue_slice_id(n) == sid and _has_client_sidecar(it)
            )
            expected_n = max(expected_n, live_clients)

        expected_names = [_ue_deploy_name(sid, i) for i in range(1, max(expected_n, 0) + 1)]
        expected_set = set(expected_names)
        extra_live = [
            n
            for n in live_by_name
            if _parse_ue_slice_id(n) == sid and n not in expected_set
        ]
        ordered = list(expected_names) + sorted(extra_live)

        deployments = [_ue_item_row(n, live_by_name.get(n)) for n in ordered]
        present = sum(1 for d in deployments if d["exists"])
        client_ready = sum(
            1
            for d in deployments
            if d["name"] in expected_set
            and d["exists"]
            and d["client_sidecar"]
            and d["ok"]
        )
        sidecar_present = sum(
            1
            for d in deployments
            if d["name"] in expected_set and d["exists"] and d["client_sidecar"]
        )
        ran_up = any(d["exists"] and int(d["ready"] or 0) > 0 for d in deployments)

        if expected_n <= 0:
            overall = "idle"
            summary = "No client UEs configured"
        elif client_ready >= expected_n and expected_n > 0:
            overall = "ready"
            summary = f"{client_ready}/{expected_n} Ready"
        elif sidecar_present > 0:
            overall = "partial" if client_ready > 0 else "degraded"
            summary = f"{client_ready}/{expected_n} Ready"
        elif ran_up:
            overall = "ran_only"
            summary = "RAN UE up · client not applied"
        elif present > 0:
            overall = "degraded"
            summary = f"{present} UE deploy(s) not ready"
        else:
            overall = "missing"
            summary = "Not on cluster"

        slices_out[str(sid)] = {
            "slice_id": sid,
            "expected": expected_n,
            "present": present,
            "client_ready": client_ready,
            "overall": overall,
            "summary": summary,
            "deployments": deployments,
        }

    return {
        "namespace": namespace,
        "cluster": "edge",
        "namespace_exists": namespace_exists,
        "error": None if namespace_exists else (ns_err or list_err),
        "slices": slices_out,
    }
