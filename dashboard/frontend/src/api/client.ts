export type ClusterSummary = {
  name: string;
  reachable: boolean;
  latency_ms: number;
  nodes: number;
  nodes_ready: number;
  pods: number;
  pods_running: number;
  deployments_desired: number;
  deployments_ready: number;
  health: string;
  error?: string | null;
  kubeconfig: string;
  context: string;
};

export type NodeInfo = {
  name: string;
  ready: boolean;
  conditions: Record<string, string>;
  roles: string[];
  kubelet_version: string;
  gpu_count?: number;
  capacity: { cpu: string; memory: string; pods: string };
  allocatable: { cpu: string; memory: string; pods: string };
};

export type GpuDeviceMetric = {
  index: number;
  model: string;
  util_pct: number;
  memory_used_mib: number;
  memory_total_mib: number;
  memory_used_bytes?: number;
  memory_total_bytes?: number;
};

export type GpuNodeMetric = {
  name: string;
  gpu_count: number;
  gpus: GpuDeviceMetric[];
  error?: string | null;
};

export type PodInfo = {
  name: string;
  namespace: string;
  phase: string;
  node: string;
  ready: boolean;
  restarts: number;
};

export type WorkloadInfo = {
  name: string;
  namespace: string;
  desired: number;
  ready: number;
  available: number;
  healthy: boolean;
};

export type NodeMetric = {
  name: string;
  cpu_cores: number;
  memory_bytes: number;
  /** false when k8s node is known but node_exporter has not produced a sample yet */
  sampled?: boolean;
};

export type Metrics = {
  cluster: string;
  pod_phases: Record<string, number>;
  node_ready: Record<string, number>;
  workloads: Record<string, number>;
  resources: {
    source?: string;
    cpu_usage_cores?: number;
    memory_usage_bytes?: number;
    cpu_allocatable_cores?: number;
    memory_allocatable_bytes?: number;
    cpu_request_cores?: number;
    memory_request_bytes?: number;
    nodes?: NodeMetric[];
    gpus?: {
      source?: string;
      nodes?: GpuNodeMetric[];
      error?: string | null;
    };
  } | null;
  error?: string | null;
};

export type TopologyNode = {
  id: string;
  type?: string;
  position: { x: number; y: number };
  parentId?: string | null;
  extent?: string | null;
  draggable?: boolean | null;
  style?: Record<string, unknown> | null;
  data: Record<string, unknown> & {
    label: string;
    health?: string;
    reachable?: boolean;
    nodes?: number;
    nodes_ready?: number;
    pods?: number;
    pods_running?: number;
    latency_ms?: number;
    error?: string | null;
    cluster?: string;
    ready?: boolean;
    roles?: string[];
  };
};

export type TopologyEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  data?: { ok?: boolean; bidirectional?: boolean; from_top?: boolean };
  animated?: boolean;
};

export type NodeInterface = {
  name: string;
  kind: "physical" | "kubernetes" | "other" | string;
  rx_bytes: number;
  tx_bytes: number;
  rx_bps?: number | null;
  tx_bps?: number | null;
  rx_mbps?: number | null;
  tx_mbps?: number | null;
};

export type NodeInterfaceHistory = {
  labels: string[];
  series: Record<string, { rx_mbps?: (number | null)[]; tx_mbps?: (number | null)[] }>;
};

export type NodeInterfacesResponse = {
  cluster: string;
  node: string;
  source: string;
  interfaces: NodeInterface[];
  history?: NodeInterfaceHistory | null;
  error?: string | null;
};

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => getJson<{ status: string }>("/api/v1/health"),
  clusters: () => getJson<{ clusters: ClusterSummary[] }>("/api/v1/clusters"),
  cluster: (name: string) => getJson<ClusterSummary>(`/api/v1/clusters/${name}`),
  nodes: (name: string) =>
    getJson<{ cluster: string; items: NodeInfo[]; error?: string | null }>(
      `/api/v1/clusters/${name}/nodes`,
    ),
  pods: (name: string, namespace?: string) => {
    const q = namespace ? `?namespace=${encodeURIComponent(namespace)}` : "";
    return getJson<{ cluster: string; items: PodInfo[]; error?: string | null }>(
      `/api/v1/clusters/${name}/pods${q}`,
    );
  },
  workloads: (name: string) =>
    getJson<{
      cluster: string;
      deployments: WorkloadInfo[];
      statefulsets: WorkloadInfo[];
      error?: string | null;
    }>(`/api/v1/clusters/${name}/workloads`),
  metrics: (name: string) => getJson<Metrics>(`/api/v1/clusters/${name}/metrics`),
  nodeInterfaces: (cluster: string, node: string) =>
    getJson<NodeInterfacesResponse>(
      `/api/v1/clusters/${encodeURIComponent(cluster)}/nodes/${encodeURIComponent(node)}/interfaces`,
    ),
  topology: () =>
    getJson<{ nodes: TopologyNode[]; edges: TopologyEdge[] }>("/api/v1/topology"),
  topologyLayout: () =>
    getJson<{
      clusters: Record<string, { x: number; y: number }>;
      viewport?: { x: number; y: number; zoom: number } | null;
      path: string;
    }>("/api/v1/topology/layout"),
  saveTopologyLayout: (body: {
    clusters?: Record<string, { x: number; y: number }>;
    viewport?: { x: number; y: number; zoom: number };
  }) =>
    putJson<{
      clusters: Record<string, { x: number; y: number }>;
      viewport?: { x: number; y: number; zoom: number } | null;
      path: string;
    }>("/api/v1/topology/layout", body),
};
