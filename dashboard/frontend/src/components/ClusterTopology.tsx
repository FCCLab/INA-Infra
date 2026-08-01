import { useCallback, useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodeTypes,
  type Viewport,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  api,
  type Metrics,
  type NodeInfo,
  type TopologyEdge,
  type TopologyNode,
} from "../api/client";
import { finiteOrZero } from "../lib/format";
import { parseCpuCores, parseMemBytes } from "../lib/k8sUnits";
import { readThemeColors } from "../lib/theme";
import ClusterNode from "./ClusterNode";
import K8sNode, { K8S_NODE_H, K8S_NODE_H_GPU, type K8sNodeUsage } from "./K8sNode";

const nodeTypes: NodeTypes = { cluster: ClusterNode, k8sNode: K8sNode };

const DEFAULT_VIEWPORT: Viewport = { x: 20, y: 10, zoom: 0.85 };

/** cluster → nodeName → usage snapshot for topology chips */
type UsageByCluster = Record<string, Record<string, K8sNodeUsage>>;

function pctOf(used: number, total: number): number | null {
  const u = finiteOrZero(used);
  const t = finiteOrZero(total);
  if (t <= 0) return null;
  const p = (u / t) * 100;
  if (!Number.isFinite(p)) return null;
  return Math.min(100, Math.max(0, p));
}

function usageFromCluster(nodes: NodeInfo[], metrics: Metrics): Record<string, K8sNodeUsage> {
  const usageByName = new Map((metrics.resources?.nodes || []).map((n) => [n.name, n]));
  const gpuByName = new Map((metrics.resources?.gpus?.nodes || []).map((n) => [n.name, n]));
  const out: Record<string, K8sNodeUsage> = {};

  const names = new Set<string>([
    ...nodes.map((n) => n.name),
    ...usageByName.keys(),
    ...gpuByName.keys(),
  ]);

  for (const name of names) {
    const info = nodes.find((n) => n.name === name);
    const usage = usageByName.get(name);
    const gpu0 = gpuByName.get(name)?.gpus?.[0];
    const sampled = usage == null ? false : usage.sampled !== false;
    const cpuAlloc = parseCpuCores(info?.allocatable?.cpu || info?.capacity?.cpu);
    const memAlloc = parseMemBytes(info?.allocatable?.memory || info?.capacity?.memory);
    const hasGpu = Boolean(gpu0) || (info?.gpu_count || 0) > 0;

    let cpu_pct: number | null = null;
    let mem_pct: number | null = null;
    if (usage && sampled) {
      cpu_pct = pctOf(usage.cpu_cores, cpuAlloc);
      mem_pct = pctOf(usage.memory_bytes, memAlloc);
    }

    let gpu_pct: number | null = null;
    let vram_pct: number | null = null;
    let vram_used_gib: number | null = null;
    let vram_total_gib: number | null = null;
    if (hasGpu) {
      if (gpu0 && Number.isFinite(gpu0.util_pct)) {
        gpu_pct = Math.min(100, Math.max(0, finiteOrZero(gpu0.util_pct)));
      } else {
        gpu_pct = null;
      }
      if (
        gpu0 &&
        Number.isFinite(gpu0.memory_used_mib) &&
        Number.isFinite(gpu0.memory_total_mib) &&
        gpu0.memory_total_mib > 0
      ) {
        vram_pct = pctOf(gpu0.memory_used_mib, gpu0.memory_total_mib);
        vram_used_gib = gpu0.memory_used_mib / 1024;
        vram_total_gib = gpu0.memory_total_mib / 1024;
      }
    }

    out[name] = {
      cpu_pct,
      mem_pct,
      has_gpu: hasGpu,
      gpu_pct: hasGpu ? gpu_pct : null,
      vram_pct: hasGpu ? vram_pct : null,
      vram_used_gib: hasGpu ? vram_used_gib : null,
      vram_total_gib: hasGpu ? vram_total_gib : null,
      sampled,
    };
  }
  return out;
}

async function fetchAllNodeUsage(clusterNames: string[]): Promise<UsageByCluster> {
  const unique = [...new Set(clusterNames.filter(Boolean))];
  const out: UsageByCluster = {};
  // Sequential — parallel 4× /metrics starves the detail panel.
  for (const cluster of unique) {
    try {
      const [n, m] = await Promise.all([api.nodes(cluster), api.metrics(cluster)]);
      out[cluster] = usageFromCluster(n.items || [], m);
    } catch {
      out[cluster] = {};
    }
  }
  return out;
}

const CHILD_H = K8S_NODE_H;
const CHILD_H_GPU = K8S_NODE_H_GPU;
const GAP_Y = 5;
const PAD_TOP = 96 + 8; // header + slot pad top
const PAD_BOTTOM = 10;
const CLUSTER_W = 10 * 2 + 220;

function childHeight(hasGpu: boolean): number {
  return hasGpu ? CHILD_H_GPU : CHILD_H;
}

function applyUsage(nodes: Node[], usage: UsageByCluster): Node[] {
  const withUsage = nodes.map((n) => {
    if (n.type !== "k8sNode") return n;
    const cluster = String((n.data as { cluster?: string }).cluster || "");
    const label = String((n.data as { label?: string }).label || "");
    const snap = usage[cluster]?.[label];
    if (!snap) return n;
    const hasGpu = Boolean(snap.has_gpu);
    return {
      ...n,
      style: {
        ...(n.style || {}),
        height: childHeight(hasGpu),
      },
      data: {
        ...n.data,
        usage: snap,
        has_gpu: hasGpu,
        gpu_count: hasGpu
          ? Math.max(1, Number((n.data as { gpu_count?: number }).gpu_count || 0))
          : 0,
      },
    };
  });

  // Restack children + resize cluster parents for flexible chip heights.
  const childrenByCluster = new Map<string, Node[]>();
  for (const n of withUsage) {
    if (n.type !== "k8sNode" || !n.parentId) continue;
    const list = childrenByCluster.get(n.parentId) || [];
    list.push(n);
    childrenByCluster.set(n.parentId, list);
  }

  const stacked = new Map<string, Node>();
  const clusterHeights = new Map<string, number>();
  for (const [clusterId, kids] of childrenByCluster) {
    kids.sort((a, b) => a.position.y - b.position.y);
    let y = PAD_TOP;
    for (const kid of kids) {
      const hasGpu = Boolean(
        (kid.data as { has_gpu?: boolean; usage?: { has_gpu?: boolean } }).usage
          ?.has_gpu ?? (kid.data as { has_gpu?: boolean }).has_gpu,
      );
      const h = childHeight(hasGpu);
      stacked.set(kid.id, {
        ...kid,
        position: { x: kid.position.x, y },
        height: h,
        style: { ...(kid.style || {}), height: h },
      });
      y += h + GAP_Y;
    }
    const body =
      kids.length === 0
        ? CHILD_H
        : y - PAD_TOP - GAP_Y; // y already includes trailing gap after last child
    clusterHeights.set(clusterId, PAD_TOP + body + PAD_BOTTOM);
  }

  return withUsage.map((n) => {
    if (n.type === "k8sNode") return stacked.get(n.id) || n;
    if (n.type === "cluster" && clusterHeights.has(n.id)) {
      return {
        ...n,
        style: {
          ...(n.style || {}),
          width: CLUSTER_W,
          height: clusterHeights.get(n.id),
        },
      };
    }
    return n;
  });
}

type Props = {
  selectedCluster: string | null;
  selectedNode: string | null;
  onSelectCluster: (name: string) => void;
  onSelectNode: (cluster: string, nodeName: string | null) => void;
  refreshToken: number;
};

function clusterIdOf(node: TopologyNode | Node): string {
  const data = node.data as { cluster?: string };
  if (node.type === "k8sNode" && data.cluster) return data.cluster;
  if ("parentId" in node && node.parentId) return String(node.parentId);
  return node.id;
}

function clusterPositions(nodes: Node[]): Record<string, { x: number; y: number }> {
  const clusters: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    if (n.type !== "cluster") continue;
    clusters[n.id] = { x: n.position.x, y: n.position.y };
  }
  return clusters;
}

function toFlowNodes(
  nodes: TopologyNode[],
  selectedCluster: string | null,
  selectedNode: string | null,
): Node[] {
  return nodes.map((n) => {
    const cluster = clusterIdOf(n);
    const isCluster = n.type === "cluster" || !n.parentId;
    const nodeName = String((n.data as { label?: string }).label || "");
    const isSelectedNode =
      !isCluster && selectedCluster === cluster && selectedNode === nodeName;
    const style = {
      ...(n.style || {}),
      ...(isCluster
        ? {}
        : { padding: 0, margin: 0, border: "none", background: "transparent" }),
    };
    const w = Number((n.style as { width?: number } | null | undefined)?.width);
    const h = Number((n.style as { height?: number } | null | undefined)?.height);
    return {
      id: n.id,
      type: n.type || "cluster",
      position: n.position,
      parentId: n.parentId ?? undefined,
      extent: (n.extent as "parent" | undefined) ?? undefined,
      expandParent: false,
      draggable: n.draggable ?? isCluster,
      style,
      ...(Number.isFinite(w) ? { width: w } : {}),
      ...(Number.isFinite(h) ? { height: h } : {}),
      data: {
        ...n.data,
        selected: isCluster
          ? selectedCluster === cluster && !selectedNode
          : isSelectedNode,
      },
      selected: isCluster
        ? selectedCluster === cluster && !selectedNode
        : isSelectedNode,
    };
  });
}

function toFlowEdges(edges: TopologyEdge[]): Edge[] {
  const c = readThemeColors();
  return edges.map((e) => {
    const ok = Boolean(e.data?.ok);
    const bi = Boolean(e.data?.bidirectional);
    const fromTop = Boolean(e.data?.from_top) || e.source === "mgmt";
    const color = ok ? c.accent2 : c.textDim;
    const label = (e.label || "").trim();
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: fromTop ? "bottom" : "right",
      targetHandle: fromTop ? "top" : "left",
      label: label || undefined,
      animated: Boolean(e.animated && ok),
      style: { stroke: color, strokeWidth: 2 },
      labelStyle: label ? { fill: c.textDim, fontSize: 10 } : undefined,
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      markerStart: bi
        ? { type: MarkerType.ArrowClosed, color, width: 16, height: 16 }
        : undefined,
    };
  });
}

function mergeLiveData(prev: Node[], next: Node[]): Node[] {
  const prevById = new Map(prev.map((n) => [n.id, n]));
  return next.map((n) => {
    const old = prevById.get(n.id);
    if (!old) return n;
    const keepPos = n.type === "cluster";
    const oldData = (old.data || {}) as Record<string, unknown>;
    const nextData = (n.data || {}) as Record<string, unknown>;
    return {
      ...n,
      position: keepPos ? old.position : n.position,
      selected: old.selected,
      data: {
        ...oldData,
        ...nextData,
        // Keep last usage until the metrics refresh replaces it.
        usage: nextData.usage ?? oldData.usage,
        selected: oldData.selected,
      },
    };
  });
}

export default function ClusterTopology({
  selectedCluster,
  selectedNode,
  onSelectCluster,
  onSelectNode,
  refreshToken,
}: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  /** Remount key once saved viewport is known so defaultViewport applies correctly. */
  const [flowKey, setFlowKey] = useState("boot");
  const [initialViewport, setInitialViewport] = useState<Viewport>(DEFAULT_VIEWPORT);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const loadedOnce = useRef(false);
  const canPersist = useRef(false);
  const saveSeq = useRef(0);
  const loadInFlight = useRef(false);
  const usageInFlight = useRef(false);
  const lastUsageAt = useRef(0);
  const usageTimer = useRef<number | null>(null);
  const nodesRef = useRef<Node[]>([]);
  const viewportRef = useRef<Viewport>(DEFAULT_VIEWPORT);
  const selectedClusterRef = useRef(selectedCluster);
  const selectedNodeRef = useRef(selectedNode);

  useEffect(() => {
    selectedClusterRef.current = selectedCluster;
  }, [selectedCluster]);
  useEffect(() => {
    selectedNodeRef.current = selectedNode;
  }, [selectedNode]);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    return () => {
      if (usageTimer.current != null) window.clearTimeout(usageTimer.current);
    };
  }, []);

  const persistLayout = useCallback(
    async (opts: {
      clusters?: Record<string, { x: number; y: number }>;
      viewport?: Viewport;
    }) => {
      if (!canPersist.current) return;
      const seq = ++saveSeq.current;
      setSaving(true);
      setSaveMsg("Saving…");
      try {
        await api.saveTopologyLayout(opts);
        if (seq !== saveSeq.current) return;
        setSaveMsg("Saved");
      } catch (err) {
        if (seq !== saveSeq.current) return;
        setSaveMsg(err instanceof Error ? err.message : String(err));
      } finally {
        if (seq === saveSeq.current) setSaving(false);
      }
    },
    [],
  );

  const enrichUsage = useCallback(() => {
    if (usageInFlight.current) return;
    const now = Date.now();
    if (lastUsageAt.current !== 0 && now - lastUsageAt.current < 30_000) return;

    usageInFlight.current = true;
    const selected = selectedClusterRef.current;
    const clusterNames = ["mgmt", "central", "regional", "edge"].sort((a, b) => {
      if (a === selected) return 1;
      if (b === selected) return -1;
      return 0;
    });
    void fetchAllNodeUsage(clusterNames)
      .then((usage) => {
        lastUsageAt.current = Date.now();
        setNodes((prev) => (prev.length ? applyUsage(prev, usage) : prev));
      })
      .finally(() => {
        usageInFlight.current = false;
      });
  }, []);

  const load = useCallback(async () => {
    // Never stack topology fetches — a 10s poll was aborting slower /topology
    // calls so the canvas stayed empty.
    if (loadInFlight.current) return;
    loadInFlight.current = true;
    try {
      const [topo, layout] = await Promise.all([api.topology(), api.topologyLayout()]);

      const nextNodes = toFlowNodes(
        topo.nodes,
        selectedClusterRef.current,
        selectedNodeRef.current,
      );
      const nextEdges = toFlowEdges(topo.edges);
      setNodes((prev) => (loadedOnce.current ? mergeLiveData(prev, nextNodes) : nextNodes));
      setEdges(nextEdges);
      if (!loadedOnce.current) {
        const vp = layout.viewport || DEFAULT_VIEWPORT;
        setInitialViewport(vp);
        viewportRef.current = vp;
        setFlowKey(`vp-${vp.x.toFixed(1)}-${vp.y.toFixed(1)}-${vp.zoom.toFixed(3)}`);
        window.setTimeout(() => {
          canPersist.current = true;
        }, 300);
        loadedOnce.current = true;
        // Detail panel gets a head start before chip usage scraping.
        if (usageTimer.current != null) window.clearTimeout(usageTimer.current);
        usageTimer.current = window.setTimeout(() => enrichUsage(), 2500);
      } else {
        enrichUsage();
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      loadInFlight.current = false;
    }
  }, [enrichUsage]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => {
        const cluster = clusterIdOf(n);
        const isCluster = n.type === "cluster";
        const nodeName = String((n.data as { label?: string }).label || "");
        const isSelectedNode =
          !isCluster && selectedCluster === cluster && selectedNode === nodeName;
        const isSelectedCluster =
          isCluster && selectedCluster === cluster && !selectedNode;
        return {
          ...n,
          selected: isCluster ? isSelectedCluster : isSelectedNode,
          data: {
            ...n.data,
            selected: isCluster ? isSelectedCluster : isSelectedNode,
          },
        };
      }),
    );
  }, [selectedCluster, selectedNode]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((nds) => {
        const next = applyNodeChanges(changes, nds);
        const dragEnded = changes.some(
          (c) => c.type === "position" && c.dragging === false,
        );
        if (dragEnded) {
          void persistLayout({
            clusters: clusterPositions(next),
            viewport: viewportRef.current,
          });
        }
        return next;
      });
    },
    [persistLayout],
  );

  const onMove = useCallback((_: unknown, nextViewport: Viewport) => {
    viewportRef.current = nextViewport;
  }, []);

  const onMoveEnd = useCallback(
    (_: unknown, nextViewport: Viewport) => {
      viewportRef.current = nextViewport;
      void persistLayout({
        clusters: clusterPositions(nodesRef.current),
        viewport: nextViewport,
      });
    },
    [persistLayout],
  );

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      const cluster = clusterIdOf(node);
      if (node.type === "k8sNode") {
        const name = String((node.data as { label?: string }).label || "");
        onSelectNode(cluster, name || null);
      } else {
        onSelectCluster(cluster);
        onSelectNode(cluster, null);
      }
    },
    [onSelectCluster, onSelectNode],
  );

  const theme = readThemeColors();

  return (
    <div className="topology-wrap">
      {saveMsg ? (
        <div className="topology-toolbar">
          <div className="topology-toolbar-actions">
            <span className={saving ? "muted" : saveMsg === "Saved" ? "save-ok" : "save-err"}>
              {saveMsg}
            </span>
          </div>
        </div>
      ) : null}
      {error ? <div className="error-banner">Topology: {error}</div> : null}
      {!error && nodes.length === 0 ? (
        <div className="topology-empty muted">Loading topology…</div>
      ) : null}
      <ReactFlow
        key={flowKey}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={onNodeClick}
        onMove={onMove}
        onMoveEnd={onMoveEnd}
        defaultViewport={initialViewport}
        minZoom={0.2}
        maxZoom={2}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        fitView={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1} color={theme.grid} />
        <Controls showInteractive={false} showFitView showZoom position="bottom-left" />
      </ReactFlow>
    </div>
  );
}
