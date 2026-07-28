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
    "central": "central-0",
    "regional": "regional-0",
    "edge": "edge-0",
    "ue": "ue-0",
}

_lock = threading.Lock()
_cache: Dict[str, str] = {}


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def _ssh_cfg() -> Path:
    env = os.environ.get("SSH_CFG")
    if env:
        return Path(env)
    return _repo_root() / "utils" / "ssh_config" / "config"


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
    host = (host or "").strip() or "edge-0"
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
