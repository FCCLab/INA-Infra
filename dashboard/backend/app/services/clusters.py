"""Lab cluster names, kubeconfig paths, and contexts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple

CLUSTER_NAMES: Tuple[str, ...] = ("mgmt", "central", "regional", "edge")

# Control-plane operator mgmt IPs (enp1s0) — same as cluster_lib.sh dashboard_mgmt_ip.
_CLUSTER_MGMT_IP: Dict[str, str] = {
    "mgmt": "10.1.132.200",
    "central": "10.1.132.210",
    "regional": "10.1.132.220",
    "edge": "10.1.132.230",
}

# Per-cluster Prometheus NodePort (scripts/render_prometheus_gitops.sh).
_DEFAULT_PROM_NODEPORT = "30909"

# cluster -> (env keys to try, default filename under ~/.kube, context)
_CLUSTER_SPECS: Dict[str, Tuple[Tuple[str, ...], str, str]] = {
    "mgmt": (
        ("DASHBOARD_MGMT_KUBECONFIG", "KUBECONFIG_MGMT", "INA_MGMT_KUBECONFIG"),
        "config",
        "mgmt@mgmt",
    ),
    "central": (
        ("DASHBOARD_CENTRAL_KUBECONFIG", "KUBECONFIG_CENTRAL", "INA_CENTRAL_KUBECONFIG"),
        "config-central",
        "central@central",
    ),
    "regional": (
        ("DASHBOARD_REGIONAL_KUBECONFIG", "KUBECONFIG_REGIONAL", "INA_REGIONAL_KUBECONFIG"),
        "config-regional",
        "regional@regional",
    ),
    "edge": (
        ("DASHBOARD_EDGE_KUBECONFIG", "KUBECONFIG_EDGE", "INA_EDGE_KUBECONFIG"),
        "config-edge",
        "edge@edge",
    ),
}


def list_clusters() -> List[str]:
    return list(CLUSTER_NAMES)


def kubeconfig_path(cluster: str) -> Path:
    if cluster not in _CLUSTER_SPECS:
        raise KeyError(f"unknown cluster: {cluster}")
    env_keys, default_name, _ = _CLUSTER_SPECS[cluster]
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            return Path(val).expanduser()
    home = Path.home() / ".kube"
    if cluster == "mgmt":
        return home / "config"
    return home / default_name


def kube_context(cluster: str) -> str:
    if cluster not in _CLUSTER_SPECS:
        raise KeyError(f"unknown cluster: {cluster}")
    return _CLUSTER_SPECS[cluster][2]


def assert_known(cluster: str) -> str:
    name = cluster.strip().lower()
    if name not in _CLUSTER_SPECS:
        raise KeyError(f"unknown cluster: {cluster}")
    return name


def cluster_mgmt_ip(cluster: str) -> str:
    name = assert_known(cluster)
    env_key = f"DASHBOARD_{name.upper()}_MGMT_IP"
    return os.environ.get(env_key) or _CLUSTER_MGMT_IP[name]


def prometheus_base_url(cluster: str) -> str:
    """HTTP base URL for that cluster's Prometheus (NodePort on CP mgmt IP)."""
    name = assert_known(cluster)
    explicit = os.environ.get(f"DASHBOARD_{name.upper()}_PROMETHEUS_URL") or os.environ.get(
        f"PROMETHEUS_URL_{name.upper()}"
    )
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("DASHBOARD_PROMETHEUS_NODEPORT") or os.environ.get(
        "PROM_NODEPORT", _DEFAULT_PROM_NODEPORT
    )
    return f"http://{cluster_mgmt_ip(name)}:{port}"
