"""REST routes for the multi-cluster dashboard."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app import schemas
from app.services import clusters as cluster_svc
from app.services import inventory, net_ifaces, topology
from app.services import topology_layout

router = APIRouter(prefix="/api/v1")


def _known(name: str) -> str:
    try:
        return cluster_svc.assert_known(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/health",
    response_model=schemas.HealthResponse,
    tags=["System"],
    summary="Liveness probe",
)
def health() -> schemas.HealthResponse:
    return schemas.HealthResponse(status="ok")


@router.get(
    "/clusters",
    response_model=schemas.ClusterListResponse,
    tags=["Clusters"],
    summary="List all clusters with live summaries",
)
def list_clusters() -> schemas.ClusterListResponse:
    items = inventory.summarize_all()
    return schemas.ClusterListResponse(
        clusters=[schemas.ClusterSummary(**c) for c in items]
    )


@router.get(
    "/clusters/{name}",
    response_model=schemas.ClusterSummary,
    tags=["Clusters"],
    summary="Single cluster summary",
)
def get_cluster(name: str) -> schemas.ClusterSummary:
    cluster = _known(name)
    return schemas.ClusterSummary(**inventory.summarize_cluster(cluster))


@router.get(
    "/clusters/{name}/nodes",
    response_model=schemas.NodeListResponse,
    tags=["Clusters"],
    summary="Nodes for a cluster",
)
def get_nodes(name: str) -> schemas.NodeListResponse:
    cluster = _known(name)
    raw = inventory.fetch_nodes(cluster)
    return schemas.NodeListResponse(
        cluster=raw["cluster"],
        items=[schemas.NodeInfo(**n) for n in raw["items"]],
        error=raw.get("error"),
    )


@router.get(
    "/clusters/{name}/pods",
    response_model=schemas.PodListResponse,
    tags=["Clusters"],
    summary="Pods for a cluster",
)
def get_pods(
    name: str,
    namespace: Optional[str] = Query(None, description="Limit to one namespace"),
) -> schemas.PodListResponse:
    cluster = _known(name)
    raw = inventory.fetch_pods(cluster, namespace=namespace)
    return schemas.PodListResponse(
        cluster=raw["cluster"],
        items=[schemas.PodInfo(**p) for p in raw["items"]],
        error=raw.get("error"),
    )


@router.get(
    "/clusters/{name}/workloads",
    response_model=schemas.WorkloadListResponse,
    tags=["Clusters"],
    summary="Deployments and StatefulSets",
)
def get_workloads(name: str) -> schemas.WorkloadListResponse:
    cluster = _known(name)
    raw = inventory.fetch_workloads(cluster)
    return schemas.WorkloadListResponse(
        cluster=raw["cluster"],
        deployments=[schemas.WorkloadInfo(**d) for d in raw["deployments"]],
        statefulsets=[schemas.WorkloadInfo(**s) for s in raw["statefulsets"]],
        error=raw.get("error"),
    )


@router.get(
    "/clusters/{name}/metrics",
    response_model=schemas.MetricsResponse,
    tags=["Clusters"],
    summary="Aggregates for charts",
)
def get_metrics(name: str) -> schemas.MetricsResponse:
    cluster = _known(name)
    raw = inventory.fetch_metrics(cluster)
    return schemas.MetricsResponse(**raw)


@router.get(
    "/clusters/{name}/nodes/{node}/interfaces",
    response_model=schemas.NodeInterfacesResponse,
    tags=["Clusters"],
    summary="Per-node NIC RX/TX rates from Prometheus node_exporter",
)
def get_node_interfaces(name: str, node: str) -> schemas.NodeInterfacesResponse:
    cluster = _known(name)
    raw = net_ifaces.fetch_node_interfaces(cluster, node)
    hist_raw = raw.get("history") or {}
    return schemas.NodeInterfacesResponse(
        cluster=raw["cluster"],
        node=raw["node"],
        source=raw.get("source") or "prometheus",
        interfaces=[schemas.NodeInterfaceInfo(**i) for i in raw.get("interfaces") or []],
        history=schemas.NodeInterfaceHistory(
            labels=list(hist_raw.get("labels") or []),
            series=dict(hist_raw.get("series") or {}),
        ),
        error=raw.get("error"),
    )


@router.get(
    "/topology",
    response_model=schemas.TopologyResponse,
    tags=["Topology"],
    summary="Multi-cluster graph for React Flow",
)
def get_topology() -> schemas.TopologyResponse:
    raw = topology.build_topology()
    return schemas.TopologyResponse(
        nodes=[schemas.TopologyNode(**n) for n in raw["nodes"]],
        edges=[schemas.TopologyEdge(**e) for e in raw["edges"]],
    )


@router.get(
    "/topology/layout",
    response_model=schemas.TopologyLayoutResponse,
    tags=["Topology"],
    summary="Load saved cluster positions and viewport",
)
def get_topology_layout() -> schemas.TopologyLayoutResponse:
    full = topology_layout.load_full()
    clusters = full.get("clusters") or {}
    viewport = full.get("viewport")
    return schemas.TopologyLayoutResponse(
        clusters={k: schemas.TopologyLayoutPosition(**v) for k, v in clusters.items()},
        viewport=schemas.TopologyViewport(**viewport) if viewport else None,
        path=str(topology_layout.layout_path()),
    )


@router.put(
    "/topology/layout",
    response_model=schemas.TopologyLayoutResponse,
    tags=["Topology"],
    summary="Save cluster positions and/or viewport",
)
def put_topology_layout(body: schemas.TopologyLayout) -> schemas.TopologyLayoutResponse:
    try:
        saved = topology_layout.save_layout(
            clusters=(
                {k: v.model_dump() for k, v in body.clusters.items()}
                if body.clusters is not None
                else None
            ),
            viewport=body.viewport.model_dump() if body.viewport is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clusters = saved.get("clusters") or {}
    viewport = saved.get("viewport")
    return schemas.TopologyLayoutResponse(
        clusters={k: schemas.TopologyLayoutPosition(**v) for k, v in clusters.items()},
        viewport=schemas.TopologyViewport(**viewport) if viewport else None,
        path=str(topology_layout.layout_path()),
    )
