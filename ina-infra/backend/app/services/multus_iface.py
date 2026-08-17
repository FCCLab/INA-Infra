"""Detect Multus macvlan parent NIC for NAD templates.

Lab Multus parents are the site/k8s-node plane (typically ``10.1.137.0/24``).
Detection SSHes to the live Kubernetes node and finds the iface that carries
that prefix (override via env). Node inventories come from kubectl, not a
hardcoded hostname list.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# Fallbacks when detection is disabled or SSH fails.
FALLBACK_DEFAULT = os.environ.get("INA_MULTUS_MASTER_DEFAULT", "enp7s0")

# Address prefix that marks the Multus / site L2 parent (kubelet --node-ip plane).
DETECT_PREFIX = os.environ.get("INA_MULTUS_DETECT_PREFIX", "10.1.137.")

MULTUS_LABEL = "ina-infra.nephio.lab/multus-master"

_CLUSTER_CTX = {
    "mgmt": ("INA_MGMT_KUBECONFIG", "INA_MGMT_CONTEXT", "config", "mgmt@mgmt"),
    "central": ("INA_CENTRAL_KUBECONFIG", "INA_CENTRAL_CONTEXT", "config-central", "central@central"),
    "regional": ("INA_REGIONAL_KUBECONFIG", "INA_REGIONAL_CONTEXT", "config-regional", "regional@regional"),
    "edge": ("INA_EDGE_KUBECONFIG", "INA_EDGE_CONTEXT", "config-edge", "edge@edge"),
}

_lock = threading.Lock()
_cache: Dict[str, str] = {}


def _ssh_cfg() -> Path:
    from app.services import paths as ina_paths

    return ina_paths.ssh_config()


def _detect_enabled() -> bool:
    return os.environ.get("INA_MULTUS_DETECT", "1").strip() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _force_master() -> Optional[str]:
    v = (os.environ.get("INA_MULTUS_MASTER") or "").strip()
    return v or None


def scheduling_node_selector(arch: str, master: str) -> Dict[str, str]:
    """Pin to arch + Multus parent label — not to a hostname list."""
    sel: Dict[str, str] = {"kubernetes.io/arch": arch}
    if master:
        sel[MULTUS_LABEL] = master
    return sel


def _kubeconfig_for(cluster: str) -> Optional[str]:
    spec = _CLUSTER_CTX.get(cluster)
    if not spec:
        return os.environ.get("KUBECONFIG")
    env_key, _, default_name, _ = spec
    explicit = os.environ.get(env_key)
    if explicit:
        return explicit
    home = Path.home() / ".kube"
    if cluster == "mgmt":
        cand = home / "config"
    else:
        cand = home / default_name
    if cand.is_file():
        return str(cand)
    return os.environ.get("KUBECONFIG")


def _context_for(cluster: str) -> str:
    spec = _CLUSTER_CTX.get(cluster)
    if not spec:
        return os.environ.get("KUBECTL_CONTEXT") or f"{cluster}@{cluster}"
    _, ctx_env, _, default_ctx = spec
    return os.environ.get(ctx_env) or default_ctx


def list_cluster_nodes(cluster: str) -> List[Dict[str, Any]]:
    """Live kubectl node inventory (name, roles, arch, gpu, ready)."""
    cmd = ["kubectl", "--context", _context_for(cluster), "get", "nodes", "-o", "json"]
    kc = _kubeconfig_for(cluster)
    if kc:
        cmd[1:1] = ["--kubeconfig", kc]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=20)
        data = json.loads(raw)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return []
    out: List[Dict[str, Any]] = []
    for item in data.get("items") or []:
        md = item.get("metadata") or {}
        name = md.get("name") or ""
        if not name:
            continue
        labels = md.get("labels") or {}
        status = item.get("status") or {}
        roles = sorted(
            k.replace("node-role.kubernetes.io/", "")
            for k in labels
            if k.startswith("node-role.kubernetes.io/")
        )
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in (status.get("conditions") or [])
        )
        alloc = status.get("allocatable") or {}
        gpu = str(alloc.get("nvidia.com/gpu") or "0")
        has_gpu = gpu not in ("", "0")
        out.append(
            {
                "name": name,
                "ready": ready,
                "roles": roles,
                "arch": labels.get("kubernetes.io/arch") or "",
                "gpu": has_gpu,
                "multus_master": labels.get(MULTUS_LABEL) or "",
            }
        )
    return out


def _probe_host(cluster: str) -> str:
    """Pick a live node to SSH for cluster-scoped NAD parent detection."""
    env_host = (os.environ.get(f"INA_MULTUS_PROBE_{cluster.upper()}") or "").strip()
    if env_host:
        return env_host
    nodes = list_cluster_nodes(cluster)
    cps = [n for n in nodes if n.get("ready") and "control-plane" in (n.get("roles") or [])]
    if cps:
        return cps[0]["name"]
    ready = [n for n in nodes if n.get("ready")]
    if ready:
        return ready[0]["name"]
    if nodes:
        return nodes[0]["name"]
    return cluster


def _ssh_detect(host: str) -> Optional[str]:
    """Return iface carrying DETECT_PREFIX, or None on failure."""
    if not host:
        return None
    cfg = _ssh_cfg()
    prefix_re = DETECT_PREFIX.replace(".", r"\.")
    remote = (
        "ip -4 -o addr show | "
        f"awk '$4 ~ /^{prefix_re}/ {{print $2; exit}}'"
    )
    cmd = [
        "ssh",
        "-F",
        str(cfg),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
        host,
        remote,
    ]
    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    if not out:
        return None
    return out.split("@", 1)[0].split(":", 1)[0]


def detect_host_master(host: str, *, use_cache: bool = True) -> str:
    """Multus parent NIC for a specific SSH host / k8s node name."""
    host = (host or "").strip()
    forced = _force_master()
    if forced:
        return forced
    if not host:
        return FALLBACK_DEFAULT
    if use_cache:
        with _lock:
            if host in _cache:
                return _cache[host]

    master = FALLBACK_DEFAULT
    if _detect_enabled():
        found = _ssh_detect(host)
        if found:
            master = found

    if use_cache:
        with _lock:
            _cache[host] = master
    return master


def detect_cluster_master(cluster: str, *, use_cache: bool = True) -> str:
    """Multus parent for cluster-scoped NADs (probe a live node on that cluster)."""
    return detect_host_master(_probe_host(cluster), use_cache=use_cache)


def pick_gpu_worker(
    cluster: str,
    *,
    arch: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Prefer gpu-* workers (gpu-a40 / gpu-gh81 / gpu-gh82); skip usrp."""
    nodes = list_cluster_nodes(cluster)
    gpu_nodes = [
        n
        for n in nodes
        if n.get("ready")
        and n.get("gpu")
        and str(n.get("name") or "") != "usrp"
    ]
    if arch:
        gpu_nodes = [n for n in gpu_nodes if n.get("arch") == arch]
        if not gpu_nodes:
            return None
    named = [n for n in gpu_nodes if str(n.get("name") or "").startswith("gpu-")]
    chosen = named or gpu_nodes
    return chosen[0] if chosen else None


def detect_gpu_worker_master(
    cluster: str,
    *,
    use_cache: bool = True,
    arch: Optional[str] = None,
) -> str:
    """Multus parent on a GPU worker. arch=None accepts any GPU arch."""
    worker = pick_gpu_worker(cluster, arch=arch)
    if worker:
        return detect_host_master(worker["name"], use_cache=use_cache)
    return detect_cluster_master(cluster, use_cache=use_cache)


def detect_masters_for_profile(
    *,
    clusters: list[str],
    du_node: str,
    ue_node: str,
) -> Dict[str, str]:
    """Return a map of logical keys → detected Multus parent.

    Keys: ``central``, ``regional``, ``edge``, ``du``, ``ue`` (plus any cluster).
    """
    out: Dict[str, str] = {}
    for c in clusters:
        out[c] = detect_cluster_master(c)
    out["du"] = detect_host_master(du_node)
    out["ue"] = detect_host_master(ue_node)
    return out


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def format_masters(masters: Dict[str, str]) -> str:
    order = ["central", "regional", "edge", "du", "ue"]
    parts = []
    seen = set()
    for k in order:
        if k in masters:
            parts.append(f"{k}={masters[k]}")
            seen.add(k)
    for k, v in sorted(masters.items()):
        if k not in seen:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def label_node_multus_master(
    node: str,
    master: str,
    *,
    context: Optional[str] = None,
) -> None:
    """Set ina-infra.nephio.lab/multus-master=<iface> on a Kubernetes node."""
    ctx = context or os.environ.get("KUBECTL_CONTEXT") or ""
    cmd = ["kubectl"]
    if ctx:
        cmd += ["--context", ctx]
    cmd += [
        "label",
        "node",
        node,
        f"{MULTUS_LABEL}={master}",
        "--overwrite",
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def label_cluster_nodes_multus_master(
    cluster: str,
    *,
    context: Optional[str] = None,
    nodes: Optional[list[str]] = None,
) -> Dict[str, str]:
    """Detect parent NIC per live node and label them for scheduling.

    Returns ``{node: master}``.
    """
    ctx = context or _context_for(cluster)
    if nodes is not None:
        targets = list(nodes)
    else:
        targets = [n["name"] for n in list_cluster_nodes(cluster)]

    out: Dict[str, str] = {}
    for node in targets:
        master = detect_host_master(node, use_cache=True)
        try:
            label_node_multus_master(node, master, context=ctx)
            out[node] = master
        except (subprocess.CalledProcessError, OSError):
            continue
    return out
