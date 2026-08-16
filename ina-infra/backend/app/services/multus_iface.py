"""Detect Multus macvlan parent NIC for NAD templates.

Lab Multus parents are the site/k8s-node plane (typically ``10.1.137.0/24``):
``enp7s0`` on VM workers, ``enp4s0f0`` on usrp. Detection SSHes to the node and
finds the iface that carries that prefix (override via env).
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

# Fallbacks when detection is disabled or fails.
FALLBACK_DEFAULT = os.environ.get("INA_MULTUS_MASTER_DEFAULT", "enp7s0")
FALLBACK_USRP = os.environ.get("INA_MULTUS_MASTER_USRP", "enp4s0f0")

# Address prefix that marks the Multus / site L2 parent (kubelet --node-ip plane).
DETECT_PREFIX = os.environ.get("INA_MULTUS_DETECT_PREFIX", "10.1.137.")

CLUSTER_PROBE_HOST: Dict[str, str] = {
    "central": "cpu-central-0",
    "regional": "cpu-regional-0",
    "edge": "cpu-edge-0",
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


def _fallback_for_host(host: str) -> str:
    h = (host or "").strip()
    if h == "usrp":
        return FALLBACK_USRP
    # Bare-metal edge workers often use eno1 (see edge-2 / gpu-a40).
    if h in ("edge-2", "gpu-a40", "edge-3") or (
        h.startswith("edge-") and h not in ("edge-0", "edge-1", "cpu-edge-0", "cpu-edge-1")
    ):
        return os.environ.get("INA_MULTUS_MASTER_BAREMETAL", "eno1")
    return FALLBACK_DEFAULT


def _ssh_detect(host: str) -> Optional[str]:
    """Return iface carrying DETECT_PREFIX, or None on failure."""
    cfg = _ssh_cfg()
    # Escape dots for awk regex (10.1.137. → 10\.1\.137\.).
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
    # Strip VLAN / @peer suffixes if any (e.g. enp7s0.100@enp7s0 → enp7s0.100).
    if not out:
        return None
    return out.split("@", 1)[0].split(":", 1)[0]


def detect_host_master(host: str, *, use_cache: bool = True) -> str:
    """Multus parent NIC for a specific SSH host / k8s node name."""
    host = (host or "").strip() or "cpu-edge-0"
    forced = _force_master()
    if forced:
        return forced
    if use_cache:
        with _lock:
            if host in _cache:
                return _cache[host]

    master = _fallback_for_host(host)
    if _detect_enabled():
        found = _ssh_detect(host)
        if found:
            master = found

    if use_cache:
        with _lock:
            _cache[host] = master
    return master


def detect_cluster_master(cluster: str, *, use_cache: bool = True) -> str:
    """Multus parent for cluster-scoped NADs (probe the cluster control plane)."""
    host = CLUSTER_PROBE_HOST.get(cluster, cluster)
    return detect_host_master(host, use_cache=use_cache)


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
        f"ina-infra.nephio.lab/multus-master={master}",
        "--overwrite",
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def label_cluster_nodes_multus_master(
    cluster: str,
    *,
    context: Optional[str] = None,
    nodes: Optional[list[str]] = None,
) -> Dict[str, str]:
    """Detect parent NIC per node and label them for UPF/AMF/SMF scheduling.

    Returns ``{node: master}``.
    """
    # Default worker names in this lab when not provided.
    defaults = {
        "central": ["cpu-central-0", "cpu-central-1", "gpu-gh81"],
        "regional": ["cpu-regional-0", "cpu-regional-1", "gpu-gh82"],
        "edge": ["cpu-edge-0", "cpu-edge-1", "edge-2", "usrp", "gpu-a40"],
    }
    targets = nodes if nodes is not None else defaults.get(cluster, [CLUSTER_PROBE_HOST.get(cluster, cluster)])
    # Prefer kube context name like edge@edge
    ctx = context
    if not ctx:
        ctx = {
            "central": "central@central",
            "regional": "regional@regional",
            "edge": "edge@edge",
        }.get(cluster)

    out: Dict[str, str] = {}
    for node in targets:
        # Skip nodes that are not Ready / don't exist — label will fail quietly.
        master = detect_host_master(node, use_cache=True)
        try:
            label_node_multus_master(node, master, context=ctx)
            out[node] = master
        except (subprocess.CalledProcessError, OSError):
            continue
    return out
