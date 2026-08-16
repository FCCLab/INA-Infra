"""Per-node NVIDIA GPU util + vRAM from dcgm-exporter (DCGM).

Follows the NVIDIA DCGM-on-Kubernetes model: scrape Prometheus text from
nvidia-dcgm-exporter (:9400) for DCGM_FI_DEV_GPU_UTIL / FB_USED / FB_FREE.

The apiserver HTTP proxy to exporter pods times out in this lab, so we briefly
kubectl port-forward each exporter pod and scrape localhost (same data path
Prometheus would use).
"""

from __future__ import annotations

import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException

from app.services.clusters import kube_context, kubeconfig_path
from app.services.k8s_client import api_exc_message, core_v1, with_timeout

_GPU_NS = "gpu-operator"
_DCGM_LABEL = "app=nvidia-dcgm-exporter"
_DCGM_PORT = 9400
_METRIC_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>[^}]*)\}\s+(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)'
)
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"')


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _parse_labels(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _LABEL_RE.finditer(raw):
        out[m.group(1)] = m.group(2).encode("utf-8").decode("unicode_escape")
    return out


def _parse_dcgm_text(text: str) -> List[Dict[str, Any]]:
    """Collapse DCGM series into one record per (Hostname, gpu index)."""
    # key -> partial fields
    by_gpu: Dict[Tuple[str, int], Dict[str, Any]] = {}

    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name not in (
            "DCGM_FI_DEV_GPU_UTIL",
            "DCGM_FI_DEV_FB_USED",
            "DCGM_FI_DEV_FB_FREE",
            "DCGM_FI_DEV_FB_TOTAL",
        ):
            continue
        labels = _parse_labels(m.group("labels"))
        host = labels.get("Hostname") or labels.get("hostname") or ""
        if not host:
            continue
        try:
            idx = int(labels.get("gpu", "0"))
        except ValueError:
            idx = 0
        key = (host, idx)
        rec = by_gpu.setdefault(
            key,
            {
                "index": idx,
                "model": labels.get("modelName") or labels.get("device") or "NVIDIA GPU",
                "uuid": labels.get("UUID") or "",
                "util_pct": None,
                "memory_used_mib": None,
                "memory_free_mib": None,
                "memory_total_mib": None,
            },
        )
        if labels.get("modelName"):
            rec["model"] = labels["modelName"]
        try:
            val = float(m.group("value"))
        except ValueError:
            continue
        if val != val or val in (float("inf"), float("-inf")):
            continue
        if name == "DCGM_FI_DEV_GPU_UTIL":
            # Prefer highest util if multiple series (pod-labeled duplicates).
            prev = rec["util_pct"]
            rec["util_pct"] = val if prev is None else max(prev, val)
        elif name == "DCGM_FI_DEV_FB_USED":
            prev = rec["memory_used_mib"]
            rec["memory_used_mib"] = val if prev is None else max(prev, val)
        elif name == "DCGM_FI_DEV_FB_FREE":
            prev = rec["memory_free_mib"]
            # Prefer lowest free with highest used (same sample family).
            rec["memory_free_mib"] = val if prev is None else min(prev, val)
        elif name == "DCGM_FI_DEV_FB_TOTAL":
            rec["memory_total_mib"] = val

    gpus: List[Dict[str, Any]] = []
    for (host, _idx), rec in sorted(by_gpu.items()):
        used = rec.get("memory_used_mib")
        free = rec.get("memory_free_mib")
        total = rec.get("memory_total_mib")
        if total is None and used is not None and free is not None:
            total = used + free
        if used is None and total is not None and free is not None:
            used = max(0.0, total - free)
        if rec.get("util_pct") is None and used is None:
            continue
        used_f = float(used or 0.0)
        total_f = float(total or 0.0)
        gpus.append(
            {
                "node": host,
                "index": int(rec["index"]),
                "model": str(rec["model"]),
                "uuid": str(rec.get("uuid") or ""),
                "util_pct": round(float(rec.get("util_pct") or 0.0), 2),
                "memory_used_mib": round(used_f, 2),
                "memory_total_mib": round(total_f, 2),
                "memory_used_bytes": int(used_f * 1024 * 1024),
                "memory_total_bytes": int(total_f * 1024 * 1024),
            }
        )
    return gpus


def _scrape_dcgm_pod(cluster: str, pod_name: str) -> str:
    """Port-forward to dcgm-exporter pod and return /metrics body."""
    kc = str(kubeconfig_path(cluster))
    ctx = kube_context(cluster)
    local = _free_port()
    cmd = [
        "kubectl",
        "--kubeconfig",
        kc,
        "--context",
        ctx,
        "-n",
        _GPU_NS,
        "port-forward",
        f"pod/{pod_name}",
        f"{local}:{_DCGM_PORT}",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    last_err: Optional[BaseException] = None
    try:
        for _ in range(40):
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
                raise RuntimeError(f"port-forward exited: {err.strip() or proc.returncode}")
            time.sleep(0.15)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{local}/metrics",
                    timeout=3,
                ) as resp:
                    return resp.read().decode("utf-8", "replace")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_err = exc
                continue
        raise TimeoutError(f"dcgm scrape timeout: {last_err}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _dcgm_pods(v1) -> Dict[str, str]:
    """node name -> dcgm-exporter pod name."""
    out: Dict[str, str] = {}
    try:
        pods = v1.list_namespaced_pod(
            _GPU_NS,
            label_selector=_DCGM_LABEL,
            **with_timeout(),
        )
    except ApiException:
        return out
    for p in pods.items or []:
        meta = p.metadata
        spec = p.spec
        if not meta or not spec or not spec.node_name:
            continue
        phase = (p.status.phase if p.status else None) or ""
        if phase and phase != "Running":
            continue
        out[spec.node_name] = meta.name
    return out


def fetch_cluster_gpu_metrics(cluster: str, api) -> Dict[str, Any]:
    """Return GPU samples from dcgm-exporter, keyed by node.

    Shape:
      {
        "source": "dcgm-exporter",
        "nodes": [
          {
            "name": "<k8s-node>",
            "gpu_count": 1,
            "gpus": [{index, model, util_pct, memory_used_mib, ...}],
          },
          ...
        ],
        "error": null | str,
      }
    """
    v1 = core_v1(api)
    errors: List[str] = []
    exporters = _dcgm_pods(v1)
    if not exporters:
        return {
            "source": "dcgm-exporter",
            "nodes": [],
            "error": "no Running nvidia-dcgm-exporter pods",
        }

    # Collect by node from Hostname labels (may differ slightly; prefer pod's node).
    by_node: Dict[str, List[Dict[str, Any]]] = {n: [] for n in exporters}
    for node_name, pod_name in sorted(exporters.items()):
        try:
            text = _scrape_dcgm_pod(cluster, pod_name)
            parsed = _parse_dcgm_text(text)
            # Prefer Hostname match; else attach all samples from this exporter to its node.
            matched = [g for g in parsed if g.get("node") == node_name]
            use = matched or parsed
            cleaned = []
            for g in use:
                cleaned.append({k: v for k, v in g.items() if k != "node"})
            by_node[node_name] = cleaned
            if not cleaned:
                errors.append(f"{node_name}: no DCGM GPU series in scrape")
        except Exception as exc:  # noqa: BLE001
            msg = api_exc_message(exc) if isinstance(exc, ApiException) else f"{type(exc).__name__}: {exc}"
            errors.append(f"{node_name}: {msg}")
            by_node[node_name] = []

    items: List[Dict[str, Any]] = []
    for node_name, gpus in sorted(by_node.items()):
        items.append(
            {
                "name": node_name,
                "gpu_count": len(gpus) or 1,
                "gpus": gpus,
                "error": None if gpus else "no DCGM samples",
            }
        )

    return {
        "source": "dcgm-exporter",
        "nodes": items,
        "error": "; ".join(errors) if errors else None,
    }
