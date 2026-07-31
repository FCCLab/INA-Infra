"""Multi-cluster topology: central ↔ regional ↔ edge, with k8s nodes nested inside."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from app.services import clusters as cluster_svc
from app.services.inventory import fetch_nodes, summarize_all
from app.services.topology_layout import load_layout

# Default cluster group positions (overridden by saved layout).
_CLUSTER_POSITIONS = {
    "mgmt": {"x": 380, "y": 20},
    "central": {"x": 40, "y": 280},
    "regional": {"x": 380, "y": 280},
    "edge": {"x": 720, "y": 280},
}

# Site chain; mgmt fans out to central / regional / edge from above.
_EDGES = [
    {
        "id": "central-regional",
        "source": "central",
        "target": "regional",
        "label": "",
        "bidirectional": True,
    },
    {
        "id": "regional-edge",
        "source": "regional",
        "target": "edge",
        "label": "",
        "bidirectional": True,
    },
    {
        "id": "mgmt-central",
        "source": "mgmt",
        "target": "central",
        "label": "",
        "bidirectional": False,
        "from_top": True,
    },
    {
        "id": "mgmt-regional",
        "source": "mgmt",
        "target": "regional",
        "label": "",
        "bidirectional": False,
        "from_top": True,
    },
    {
        "id": "mgmt-edge",
        "source": "mgmt",
        "target": "edge",
        "label": "",
        "bidirectional": False,
        "from_top": True,
    },
]

# Cluster chrome + nested k8s-node layout (sync with frontend CSS).
_HEADER_H = 96
_SLOT_PAD_X = 10
_SLOT_PAD_TOP = 8
_SLOT_PAD_BOTTOM = 10
_CHILD_W = 196
_CHILD_H = 38
_GAP_Y = 6
_PAD_X = _SLOT_PAD_X
_PAD_TOP = _HEADER_H + _SLOT_PAD_TOP
_PAD_BOTTOM = _SLOT_PAD_BOTTOM
_CLUSTER_W = _SLOT_PAD_X * 2 + _CHILD_W


def _cluster_size(n_children: int) -> Tuple[float, float]:
    height = (
        _PAD_TOP
        + max(n_children, 1) * (_CHILD_H + _GAP_Y)
        - _GAP_Y
        + _PAD_BOTTOM
    )
    return float(_CLUSTER_W), float(height)


def _fetch_all_nodes() -> Dict[str, List[Dict[str, Any]]]:
    names = cluster_svc.list_clusters()
    out: Dict[str, List[Dict[str, Any]]] = {n: [] for n in names}
    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        futs = {pool.submit(fetch_nodes, n): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                raw = fut.result()
                out[name] = list(raw.get("items") or [])
            except Exception:  # noqa: BLE001
                out[name] = []
    return out


def build_topology() -> Dict[str, Any]:
    summaries = {s["name"]: s for s in summarize_all()}
    node_inventory = _fetch_all_nodes()
    saved = load_layout()

    nodes: List[Dict[str, Any]] = []
    for name, default_pos in _CLUSTER_POSITIONS.items():
        pos = saved.get(name) or default_pos
        s = summaries.get(name) or {
            "name": name,
            "reachable": False,
            "health": "unreachable",
            "nodes": 0,
            "pods": 0,
            "error": "missing summary",
        }
        children = node_inventory.get(name) or []
        width, height = _cluster_size(len(children))
        nodes.append(
            {
                "id": name,
                "type": "cluster",
                "position": pos,
                "style": {"width": width, "height": height},
                "data": {
                    "label": name,
                    "health": s.get("health", "unreachable"),
                    "reachable": bool(s.get("reachable")),
                    "nodes": int(s.get("nodes") or 0),
                    "nodes_ready": int(s.get("nodes_ready") or 0),
                    "pods": int(s.get("pods") or 0),
                    "pods_running": int(s.get("pods_running") or 0),
                    "latency_ms": s.get("latency_ms"),
                    "error": s.get("error"),
                    "header_h": _HEADER_H,
                },
            }
        )
        for i, kn in enumerate(children):
            kn_name = kn.get("name") or f"node-{i}"
            nodes.append(
                {
                    "id": f"{name}::{kn_name}",
                    "type": "k8sNode",
                    "parentId": name,
                    "extent": "parent",
                    "expandParent": False,
                    "draggable": False,
                    "position": {
                        "x": float(_PAD_X),
                        "y": float(_PAD_TOP + i * (_CHILD_H + _GAP_Y)),
                    },
                    "style": {
                        "width": _CHILD_W,
                        "height": _CHILD_H,
                        "padding": 0,
                        "margin": 0,
                    },
                    "data": {
                        "label": kn_name,
                        "cluster": name,
                        "ready": bool(kn.get("ready")),
                        "roles": list(kn.get("roles") or []),
                        "kubelet_version": kn.get("kubelet_version") or "",
                    },
                }
            )

    edges: List[Dict[str, Any]] = []
    for e in _EDGES:
        src = summaries.get(e["source"], {})
        tgt = summaries.get(e["target"], {})
        ok = bool(src.get("reachable")) and bool(tgt.get("reachable"))
        edges.append(
            {
                "id": e["id"],
                "source": e["source"],
                "target": e["target"],
                "label": e.get("label") or "",
                "data": {
                    "ok": ok,
                    "bidirectional": bool(e.get("bidirectional")),
                    "from_top": bool(e.get("from_top")),
                },
                "animated": ok,
            }
        )

    return {"nodes": nodes, "edges": edges}
