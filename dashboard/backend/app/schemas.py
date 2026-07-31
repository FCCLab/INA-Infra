"""Pydantic response models for the dashboard API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class ClusterSummary(BaseModel):
    name: str
    reachable: bool
    latency_ms: float = 0
    nodes: int = 0
    nodes_ready: int = 0
    pods: int = 0
    pods_running: int = 0
    deployments_desired: int = 0
    deployments_ready: int = 0
    health: str = "unreachable"
    error: Optional[str] = None
    kubeconfig: str = ""
    context: str = ""


class ClusterListResponse(BaseModel):
    clusters: List[ClusterSummary]


class NodeCapacity(BaseModel):
    cpu: str = ""
    memory: str = ""
    pods: str = ""


class NodeInfo(BaseModel):
    name: str
    ready: bool
    conditions: Dict[str, str] = Field(default_factory=dict)
    roles: List[str] = Field(default_factory=list)
    kubelet_version: str = ""
    gpu_count: int = 0
    capacity: NodeCapacity = Field(default_factory=NodeCapacity)
    allocatable: NodeCapacity = Field(default_factory=NodeCapacity)


class NodeListResponse(BaseModel):
    cluster: str
    items: List[NodeInfo]
    error: Optional[str] = None


class PodInfo(BaseModel):
    name: str
    namespace: str
    phase: str
    node: str = ""
    ready: bool = False
    restarts: int = 0


class PodListResponse(BaseModel):
    cluster: str
    items: List[PodInfo]
    error: Optional[str] = None


class WorkloadInfo(BaseModel):
    name: str
    namespace: str
    desired: int = 0
    ready: int = 0
    available: int = 0
    healthy: bool = True


class WorkloadListResponse(BaseModel):
    cluster: str
    deployments: List[WorkloadInfo]
    statefulsets: List[WorkloadInfo]
    error: Optional[str] = None


class MetricsResponse(BaseModel):
    cluster: str
    pod_phases: Dict[str, int] = Field(default_factory=dict)
    node_ready: Dict[str, int] = Field(default_factory=dict)
    workloads: Dict[str, int] = Field(default_factory=dict)
    resources: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class NodeInterfaceInfo(BaseModel):
    name: str
    kind: str  # physical | kubernetes | other
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_bps: Optional[float] = None
    tx_bps: Optional[float] = None
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None


class NodeInterfaceHistory(BaseModel):
    labels: List[str] = Field(default_factory=list)
    series: Dict[str, Dict[str, List[Optional[float]]]] = Field(default_factory=dict)


class NodeInterfacesResponse(BaseModel):
    cluster: str
    node: str
    source: str = "prometheus"
    interfaces: List[NodeInterfaceInfo] = Field(default_factory=list)
    history: Optional[NodeInterfaceHistory] = None
    error: Optional[str] = None


class TopologyNode(BaseModel):
    id: str
    type: str = "cluster"
    position: Dict[str, float]
    data: Dict[str, Any]
    parentId: Optional[str] = None
    extent: Optional[str] = None
    expandParent: Optional[bool] = None
    draggable: Optional[bool] = None
    style: Optional[Dict[str, Any]] = None


class TopologyEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)
    animated: bool = False


class TopologyResponse(BaseModel):
    nodes: List[TopologyNode]
    edges: List[TopologyEdge]


class TopologyLayoutPosition(BaseModel):
    x: float
    y: float


class TopologyViewport(BaseModel):
    x: float
    y: float
    zoom: float


class TopologyLayout(BaseModel):
    clusters: Optional[Dict[str, TopologyLayoutPosition]] = None
    viewport: Optional[TopologyViewport] = None


class TopologyLayoutResponse(BaseModel):
    clusters: Dict[str, TopologyLayoutPosition] = Field(default_factory=dict)
    viewport: Optional[TopologyViewport] = None
    path: str = ""
