"""Cluster inventory: nodes, pods, workloads, metrics aggregates."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException

from app.services import clusters as cluster_svc
from app.services import prometheus as prom
from app.services.gpu_metrics import fetch_cluster_gpu_metrics
from app.services.k8s_client import (
    api_exc_message,
    apps_v1,
    close_quietly,
    core_v1,
    custom_objects,
    load_api_client,
    with_timeout,
)


def _parse_cpu(val: Optional[str]) -> float:
    """Return CPU cores as float."""
    if not val:
        return 0.0
    s = str(val)
    if s.endswith("n"):
        return int(s[:-1]) / 1e9
    if s.endswith("u"):
        return int(s[:-1]) / 1e6
    if s.endswith("m"):
        return int(s[:-1]) / 1000.0
    return float(s)


def _parse_mem_bytes(val: Optional[str]) -> float:
    if not val:
        return 0.0
    s = str(val)
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, mult in units.items():
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    if s.endswith("i"):
        return float(s[:-1])
    return float(s)


def _node_ready(conditions: Optional[List[Any]]) -> bool:
    for c in conditions or []:
        if getattr(c, "type", None) == "Ready":
            return getattr(c, "status", None) == "True"
    return False


def _condition_map(conditions: Optional[List[Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in conditions or []:
        t = getattr(c, "type", None)
        if t:
            out[str(t)] = str(getattr(c, "status", "") or "")
    return out


def _kubelet_version(status: Any) -> str:
    if not status:
        return ""
    info = getattr(status, "node_info", None)
    return str(getattr(info, "kubelet_version", "") or "") if info else ""


def fetch_nodes(cluster: str) -> Dict[str, Any]:
    api, err = load_api_client(cluster)
    if err or api is None:
        return {"cluster": cluster, "items": [], "error": err or "no client"}
    try:
        v1 = core_v1(api)
        resp = v1.list_node(**with_timeout())
        items = []
        for n in resp.items or []:
            meta = n.metadata
            status = n.status
            capacity = getattr(status, "capacity", None) or {}
            allocatable = getattr(status, "allocatable", None) or {}
            gpu_count = 0
            try:
                gpu_count = int(str(allocatable.get("nvidia.com/gpu") or "0"))
            except ValueError:
                gpu_count = 0
            items.append(
                {
                    "name": meta.name if meta else "",
                    "ready": _node_ready(getattr(status, "conditions", None)),
                    "conditions": _condition_map(getattr(status, "conditions", None)),
                    "roles": _node_roles(meta.labels if meta else None),
                    "kubelet_version": _kubelet_version(status),
                    "gpu_count": gpu_count,
                    "capacity": {
                        "cpu": str(capacity.get("cpu", "")),
                        "memory": str(capacity.get("memory", "")),
                        "pods": str(capacity.get("pods", "")),
                    },
                    "allocatable": {
                        "cpu": str(allocatable.get("cpu", "")),
                        "memory": str(allocatable.get("memory", "")),
                        "pods": str(allocatable.get("pods", "")),
                    },
                }
            )
        return {"cluster": cluster, "items": items, "error": None}
    except ApiException as exc:
        return {"cluster": cluster, "items": [], "error": api_exc_message(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"cluster": cluster, "items": [], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        close_quietly(api)


def _node_roles(labels: Optional[Dict[str, str]]) -> List[str]:
    labels = labels or {}
    roles = []
    for k in labels:
        if k.startswith("node-role.kubernetes.io/"):
            role = k.split("/", 1)[-1] or "node"
            roles.append(role)
    if not roles and labels.get("node.kubernetes.io/role"):
        roles.append(labels["node.kubernetes.io/role"])
    return sorted(set(roles)) or ["worker"]


def fetch_pods(cluster: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    api, err = load_api_client(cluster)
    if err or api is None:
        return {"cluster": cluster, "items": [], "error": err or "no client"}
    try:
        v1 = core_v1(api)
        if namespace:
            resp = v1.list_namespaced_pod(namespace, **with_timeout())
        else:
            resp = v1.list_pod_for_all_namespaces(**with_timeout())
        items = []
        for p in resp.items or []:
            meta = p.metadata
            status = p.status
            items.append(
                {
                    "name": meta.name if meta else "",
                    "namespace": meta.namespace if meta else "",
                    "phase": (status.phase if status else None) or "Unknown",
                    "node": (status.node_name if status else None) or "",
                    "ready": _pod_ready(status),
                    "restarts": _pod_restarts(status),
                }
            )
        return {"cluster": cluster, "items": items, "error": None}
    except ApiException as exc:
        return {"cluster": cluster, "items": [], "error": api_exc_message(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"cluster": cluster, "items": [], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        close_quietly(api)


def _pod_ready(status: Any) -> bool:
    if not status:
        return False
    for c in status.conditions or []:
        if getattr(c, "type", None) == "Ready":
            return getattr(c, "status", None) == "True"
    return False


def _pod_restarts(status: Any) -> int:
    total = 0
    for cs in (status.container_statuses or []) if status else []:
        total += int(getattr(cs, "restart_count", 0) or 0)
    return total


def fetch_workloads(cluster: str) -> Dict[str, Any]:
    api, err = load_api_client(cluster)
    if err or api is None:
        return {
            "cluster": cluster,
            "deployments": [],
            "statefulsets": [],
            "error": err or "no client",
        }
    try:
        apps = apps_v1(api)
        deps = apps.list_deployment_for_all_namespaces(**with_timeout())
        sts = apps.list_stateful_set_for_all_namespaces(**with_timeout())
        deployments = []
        for d in deps.items or []:
            meta = d.metadata
            spec = d.spec
            status = d.status
            desired = int(getattr(spec, "replicas", 0) or 0)
            ready = int(getattr(status, "ready_replicas", 0) or 0)
            available = int(getattr(status, "available_replicas", 0) or 0)
            deployments.append(
                {
                    "name": meta.name if meta else "",
                    "namespace": meta.namespace if meta else "",
                    "desired": desired,
                    "ready": ready,
                    "available": available,
                    "healthy": ready >= desired and desired > 0 or desired == 0,
                }
            )
        statefulsets = []
        for s in sts.items or []:
            meta = s.metadata
            spec = s.spec
            status = s.status
            desired = int(getattr(spec, "replicas", 0) or 0)
            ready = int(getattr(status, "ready_replicas", 0) or 0)
            statefulsets.append(
                {
                    "name": meta.name if meta else "",
                    "namespace": meta.namespace if meta else "",
                    "desired": desired,
                    "ready": ready,
                    "available": ready,
                    "healthy": ready >= desired and desired > 0 or desired == 0,
                }
            )
        return {
            "cluster": cluster,
            "deployments": deployments,
            "statefulsets": statefulsets,
            "error": None,
        }
    except ApiException as exc:
        return {
            "cluster": cluster,
            "deployments": [],
            "statefulsets": [],
            "error": api_exc_message(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "cluster": cluster,
            "deployments": [],
            "statefulsets": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        close_quietly(api)


def _metrics_server_missing(err: Optional[str]) -> bool:
    """True when metrics.k8s.io is simply not installed (expected in this lab)."""
    if not err:
        return False
    low = err.lower()
    return (
        "404" in low
        or "not found" in low
        or "could not find the requested resource" in low
        or "the server could not find the requested resource" in low
    )


def _fetch_metrics_server(api) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Try metrics.k8s.io node metrics; return (data, error)."""
    try:
        co = custom_objects(api)
        nodes = co.list_cluster_custom_object(
            "metrics.k8s.io",
            "v1beta1",
            "nodes",
            **with_timeout(),
        )
        items = nodes.get("items") or []
        cpu_usage = 0.0
        mem_usage = 0.0
        per_node = []
        for it in items:
            meta = it.get("metadata") or {}
            usage = it.get("usage") or {}
            cpu = _parse_cpu(usage.get("cpu"))
            mem = _parse_mem_bytes(usage.get("memory"))
            cpu_usage += cpu
            mem_usage += mem
            per_node.append(
                {
                    "name": meta.get("name", ""),
                    "cpu_cores": round(cpu, 4),
                    "memory_bytes": mem,
                }
            )
        return {
            "source": "metrics-server",
            "cpu_usage_cores": round(cpu_usage, 4),
            "memory_usage_bytes": mem_usage,
            "nodes": per_node,
        }, None
    except ApiException as exc:
        return None, api_exc_message(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _fetch_prometheus_node_usage(
    cluster: str,
    node_names: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """CPU cores + memory bytes per node from node_exporter via PromQL.

    Prefer a single scrape job (kubernetes-pods) so dual pod+endpoints discovery
    does not double-count CPUs. Always emit an entry for every known k8s node
    name so the UI can resolve every live k8s node even while rate() warms up.
    """
    # job filter avoids double series from headless Service + pod annotations.
    cpu_q = (
        "sum by (node) ("
        "  (1 - avg by (node) ("
        "    rate(node_cpu_seconds_total{mode=\"idle\",job=\"kubernetes-pods\"}[5m])"
        "  ))"
        "  * count by (node) ("
        "    node_cpu_seconds_total{mode=\"idle\",job=\"kubernetes-pods\"}"
        "  )"
        ")"
    )
    mem_q = (
        "max by (node) ("
        "  node_memory_MemTotal_bytes{job=\"kubernetes-pods\"}"
        "  - node_memory_MemAvailable_bytes{job=\"kubernetes-pods\"}"
        ")"
    )
    # Fallback without job filter if pods job has not scraped yet.
    cpu_q_any = (
        "sum by (node) ("
        "  (1 - avg by (node) (rate(node_cpu_seconds_total{mode=\"idle\"}[5m])))"
        "  * count by (node) (count by (node, cpu) (node_cpu_seconds_total{mode=\"idle\"}))"
        ")"
    )
    mem_q_any = (
        "max by (node) ("
        "  node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes"
        ")"
    )

    errors: List[str] = []
    cpu_res, cpu_err = prom.query(cluster, cpu_q)
    if cpu_err:
        errors.append(cpu_err)
        cpu_res = []
    cpu_by = prom.vector_by_label(cpu_res, "node")
    if not cpu_by:
        cpu_res2, cpu_err2 = prom.query(cluster, cpu_q_any)
        if cpu_err2:
            errors.append(cpu_err2)
        else:
            cpu_by = prom.vector_by_label(cpu_res2, "node")

    mem_res, mem_err = prom.query(cluster, mem_q)
    if mem_err:
        errors.append(mem_err)
        mem_res = []
    mem_by = prom.vector_by_label(mem_res, "node")
    if not mem_by:
        mem_res2, mem_err2 = prom.query(cluster, mem_q_any)
        if mem_err2:
            errors.append(mem_err2)
        else:
            mem_by = prom.vector_by_label(mem_res2, "node")

    if not cpu_by and not mem_by:
        return None, "; ".join(errors) if errors else "no node_exporter samples"

    names = sorted(set(cpu_by) | set(mem_by) | set(node_names or []))
    per_node = []
    cpu_usage = 0.0
    mem_usage = 0.0
    for name in names:
        sampled = name in cpu_by or name in mem_by
        cpu = prom.finite(cpu_by.get(name)) or 0.0
        mem = prom.finite(mem_by.get(name)) or 0.0
        if sampled:
            cpu_usage += cpu
            mem_usage += mem
        per_node.append(
            {
                "name": name,
                "cpu_cores": round(cpu, 4),
                "memory_bytes": int(mem),
                "sampled": sampled,
            }
        )
    return {
        "source": "prometheus",
        "cpu_usage_cores": round(cpu_usage, 4),
        "memory_usage_bytes": int(mem_usage),
        "nodes": per_node,
    }, ("; ".join(errors) if errors and not per_node else None)


def _fetch_prometheus_gpus(cluster: str) -> Dict[str, Any]:
    """GPU util + framebuffer from DCGM series in Prometheus.

    Uses last_over_time so briefly stale scrapes (e.g. Prom→pod timeout to GH200)
    still surface recent values. Prefer kubernetes_node / Hostname as node name.
    """
    # Look back far enough that a Prom restart / scrape outage still has series.
    util_q = "max by (Hostname, kubernetes_node, node, gpu, modelName, UUID) (last_over_time(DCGM_FI_DEV_GPU_UTIL[14d]))"
    used_q = "max by (Hostname, kubernetes_node, node, gpu, modelName, UUID) (last_over_time(DCGM_FI_DEV_FB_USED[14d]))"
    total_q = "max by (Hostname, kubernetes_node, node, gpu, modelName, UUID) (last_over_time(DCGM_FI_DEV_FB_TOTAL[14d]))"
    util_res, util_err = prom.query(cluster, util_q)
    if util_err:
        return {"source": "prometheus", "nodes": [], "error": util_err}
    used_res, _ = prom.query(cluster, used_q)
    total_res, _ = prom.query(cluster, total_q)

    def _gpu_key(metric: Dict[str, str]) -> Tuple[str, int]:
        host = (
            metric.get("kubernetes_node")
            or metric.get("node")
            or metric.get("Hostname")
            or metric.get("hostname")
            or ""
        )
        idx_raw = metric.get("gpu") or "0"
        if isinstance(idx_raw, str) and idx_raw.startswith("nvidia"):
            idx_raw = idx_raw.replace("nvidia", "") or "0"
        try:
            idx = int(float(idx_raw))
        except (TypeError, ValueError):
            idx = 0
        return host, idx

    by_gpu: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for sample in util_res:
        metric = sample.get("metric") or {}
        host, idx = _gpu_key(metric)
        if not host:
            continue
        val = prom.sample_value(sample)
        if val is None:
            continue
        rec = by_gpu.setdefault(
            (host, idx),
            {
                "node": host,
                "index": idx,
                "model": metric.get("modelName") or metric.get("model") or "GPU",
                "uuid": metric.get("UUID") or metric.get("uuid") or "",
                "util_pct": None,
                "memory_used_mib": None,
                "memory_total_mib": None,
            },
        )
        rec["util_pct"] = val

    for sample in used_res:
        metric = sample.get("metric") or {}
        host, idx = _gpu_key(metric)
        if not host:
            continue
        val = prom.sample_value(sample)
        if val is None:
            continue
        rec = by_gpu.setdefault(
            (host, idx),
            {
                "node": host,
                "index": idx,
                "model": metric.get("modelName") or metric.get("model") or "GPU",
                "uuid": metric.get("UUID") or metric.get("uuid") or "",
                "util_pct": None,
                "memory_used_mib": None,
                "memory_total_mib": None,
            },
        )
        rec["memory_used_mib"] = val

    for sample in total_res:
        metric = sample.get("metric") or {}
        host, idx = _gpu_key(metric)
        if not host:
            continue
        val = prom.sample_value(sample)
        if val is None:
            continue
        rec = by_gpu.setdefault(
            (host, idx),
            {
                "node": host,
                "index": idx,
                "model": metric.get("modelName") or metric.get("model") or "GPU",
                "uuid": metric.get("UUID") or metric.get("uuid") or "",
                "util_pct": None,
                "memory_used_mib": None,
                "memory_total_mib": None,
            },
        )
        rec["memory_total_mib"] = val

    by_node: Dict[str, Dict[str, Any]] = {}
    for (host, _idx), rec in sorted(by_gpu.items()):
        used = prom.finite(rec.get("memory_used_mib")) or 0.0
        total = prom.finite(rec.get("memory_total_mib")) or 0.0
        util = prom.finite(rec.get("util_pct")) or 0.0
        gpu = {
            "index": int(rec["index"]),
            "model": str(rec["model"]),
            "util_pct": round(util, 2),
            "memory_used_mib": round(used, 2),
            "memory_total_mib": round(total, 2),
            "memory_used_bytes": int(used * 1024 * 1024),
            "memory_total_bytes": int(total * 1024 * 1024),
        }
        node = by_node.setdefault(
            host, {"name": host, "gpu_count": 0, "gpus": [], "error": None}
        )
        node["gpus"].append(gpu)
        node["gpu_count"] = len(node["gpus"])

    return {
        "source": "prometheus",
        "nodes": list(by_node.values()),
        "error": None if by_node else "no DCGM samples in prometheus",
    }


def fetch_metrics(cluster: str) -> Dict[str, Any]:
    api, err = load_api_client(cluster)
    if err or api is None:
        return {
            "cluster": cluster,
            "pod_phases": {},
            "node_ready": {"ready": 0, "not_ready": 0},
            "workloads": {"desired": 0, "ready": 0, "unhealthy": 0},
            "resources": None,
            "error": err or "no client",
        }

    pod_phases: Dict[str, int] = {}
    node_ready = {"ready": 0, "not_ready": 0}
    workloads = {"desired": 0, "ready": 0, "unhealthy": 0}
    resources: Optional[Dict[str, Any]] = None
    errors: List[str] = []

    try:
        v1 = core_v1(api)
        nodes = v1.list_node(**with_timeout())
        alloc_cpu = 0.0
        alloc_mem = 0.0
        k8s_node_names: List[str] = []
        for n in nodes.items or []:
            if n.metadata and n.metadata.name:
                k8s_node_names.append(n.metadata.name)
            if _node_ready(getattr(n.status, "conditions", None)):
                node_ready["ready"] += 1
            else:
                node_ready["not_ready"] += 1
            alloc = (n.status.allocatable if n.status else None) or {}
            alloc_cpu += _parse_cpu(str(alloc.get("cpu", "0")))
            alloc_mem += _parse_mem_bytes(str(alloc.get("memory", "0")))

        pods = v1.list_pod_for_all_namespaces(**with_timeout())
        req_cpu = 0.0
        req_mem = 0.0
        for p in pods.items or []:
            phase = (p.status.phase if p.status else None) or "Unknown"
            pod_phases[phase] = pod_phases.get(phase, 0) + 1
            for c in (p.spec.containers if p.spec else None) or []:
                req = (c.resources.requests if c.resources else None) or {}
                req_cpu += _parse_cpu(str(req.get("cpu", "0") or "0"))
                req_mem += _parse_mem_bytes(str(req.get("memory", "0") or "0"))

        apps = apps_v1(api)
        for d in (apps.list_deployment_for_all_namespaces(**with_timeout()).items or []):
            desired = int(getattr(d.spec, "replicas", 0) or 0)
            ready = int(getattr(d.status, "ready_replicas", 0) or 0)
            workloads["desired"] += desired
            workloads["ready"] += ready
            if desired > 0 and ready < desired:
                workloads["unhealthy"] += 1
        for s in (apps.list_stateful_set_for_all_namespaces(**with_timeout()).items or []):
            desired = int(getattr(s.spec, "replicas", 0) or 0)
            ready = int(getattr(s.status, "ready_replicas", 0) or 0)
            workloads["desired"] += desired
            workloads["ready"] += ready
            if desired > 0 and ready < desired:
                workloads["unhealthy"] += 1

        usage, usage_err = _fetch_prometheus_node_usage(cluster, k8s_node_names)
        if usage:
            resources = {
                **usage,
                "cpu_allocatable_cores": round(alloc_cpu, 4),
                "memory_allocatable_bytes": alloc_mem,
                "cpu_request_cores": round(req_cpu, 4),
                "memory_request_bytes": req_mem,
            }
        else:
            if usage_err:
                errors.append(usage_err)
            resources = {
                "source": "prometheus",
                "cpu_usage_cores": 0.0,
                "memory_usage_bytes": 0,
                "cpu_allocatable_cores": round(alloc_cpu, 4),
                "memory_allocatable_bytes": alloc_mem,
                "cpu_request_cores": round(req_cpu, 4),
                "memory_request_bytes": req_mem,
                "nodes": [
                    {
                        "name": name,
                        "cpu_cores": 0.0,
                        "memory_bytes": 0,
                        "sampled": False,
                    }
                    for name in k8s_node_names
                ],
            }

        gpu = _fetch_prometheus_gpus(cluster)
        # Live DCGM scrape via port-forward when Prom→pod scrape is down (GH200).
        try:
            gpu_fb = fetch_cluster_gpu_metrics(cluster, api)
            if gpu_fb.get("nodes"):
                gpu = {**gpu_fb, "source": "dcgm-exporter"}
            elif not gpu.get("nodes") and gpu_fb.get("error"):
                gpu["error"] = gpu.get("error") or gpu_fb.get("error")
        except Exception as exc:  # noqa: BLE001
            if not gpu.get("nodes"):
                gpu["error"] = f"{gpu.get('error') or 'prometheus'}; fallback: {exc}"
        resources["gpus"] = gpu
        if gpu.get("error") and not gpu.get("nodes"):
            if str(gpu["error"]).startswith("prometheus:"):
                errors.append(f"gpu: {gpu['error']}")
    except ApiException as exc:
        errors.append(api_exc_message(exc))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        close_quietly(api)

    return {
        "cluster": cluster,
        "pod_phases": pod_phases,
        "node_ready": node_ready,
        "workloads": workloads,
        "resources": resources,
        "error": "; ".join(errors) if errors else None,
    }


def summarize_cluster(cluster: str) -> Dict[str, Any]:
    t0 = time.monotonic()
    api, err = load_api_client(cluster)
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    if err or api is None:
        return {
            "name": cluster,
            "reachable": False,
            "latency_ms": latency_ms,
            "nodes": 0,
            "nodes_ready": 0,
            "pods": 0,
            "pods_running": 0,
            "deployments_desired": 0,
            "deployments_ready": 0,
            "health": "unreachable",
            "error": err or "no client",
            "kubeconfig": str(kubeconfig_path_safe(cluster)),
            "context": cluster_svc.kube_context(cluster),
        }

    nodes_total = nodes_ready = 0
    pods_total = pods_running = 0
    dep_desired = dep_ready = 0
    probe_err: Optional[str] = None
    try:
        t0 = time.monotonic()
        v1 = core_v1(api)
        node_list = v1.list_node(**with_timeout())
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        for n in node_list.items or []:
            nodes_total += 1
            if _node_ready(getattr(n.status, "conditions", None)):
                nodes_ready += 1
        pod_list = v1.list_pod_for_all_namespaces(**with_timeout())
        for p in pod_list.items or []:
            pods_total += 1
            if (p.status.phase if p.status else None) == "Running":
                pods_running += 1
        apps = apps_v1(api)
        for d in (apps.list_deployment_for_all_namespaces(**with_timeout()).items or []):
            desired = int(getattr(d.spec, "replicas", 0) or 0)
            ready = int(getattr(d.status, "ready_replicas", 0) or 0)
            dep_desired += desired
            dep_ready += ready
    except ApiException as exc:
        probe_err = api_exc_message(exc)
    except Exception as exc:  # noqa: BLE001
        probe_err = f"{type(exc).__name__}: {exc}"
    finally:
        close_quietly(api)

    if probe_err:
        health = "error"
        reachable = False
    elif nodes_total == 0:
        health = "degraded"
        reachable = True
    elif nodes_ready < nodes_total or (dep_desired > 0 and dep_ready < dep_desired):
        health = "degraded"
        reachable = True
    else:
        health = "healthy"
        reachable = True

    return {
        "name": cluster,
        "reachable": reachable,
        "latency_ms": latency_ms,
        "nodes": nodes_total,
        "nodes_ready": nodes_ready,
        "pods": pods_total,
        "pods_running": pods_running,
        "deployments_desired": dep_desired,
        "deployments_ready": dep_ready,
        "health": health,
        "error": probe_err,
        "kubeconfig": str(kubeconfig_path_safe(cluster)),
        "context": cluster_svc.kube_context(cluster),
    }


def kubeconfig_path_safe(cluster: str) -> str:
    try:
        return str(cluster_svc.kubeconfig_path(cluster))
    except Exception:  # noqa: BLE001
        return ""


def summarize_all() -> List[Dict[str, Any]]:
    names = cluster_svc.list_clusters()
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futs = {pool.submit(summarize_cluster, n): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[name] = {
                    "name": name,
                    "reachable": False,
                    "latency_ms": 0,
                    "nodes": 0,
                    "nodes_ready": 0,
                    "pods": 0,
                    "pods_running": 0,
                    "deployments_desired": 0,
                    "deployments_ready": 0,
                    "health": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "kubeconfig": str(kubeconfig_path_safe(name)),
                    "context": cluster_svc.kube_context(name),
                }
    return [results[n] for n in names]
