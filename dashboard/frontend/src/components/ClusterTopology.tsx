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
import { api, type TopologyEdge, type TopologyNode } from "../api/client";
import { readThemeColors } from "../lib/theme";
import ClusterNode from "./ClusterNode";
import K8sNode from "./K8sNode";

const nodeTypes: NodeTypes = { cluster: ClusterNode, k8sNode: K8sNode };

const DEFAULT_VIEWPORT: Viewport = { x: 20, y: 10, zoom: 0.85 };

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
    return {
      id: n.id,
      type: n.type || "cluster",
      position: n.position,
      parentId: n.parentId ?? undefined,
      extent: (n.extent as "parent" | undefined) ?? undefined,
      expandParent: false,
      draggable: n.draggable ?? isCluster,
      style,
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
    return {
      ...n,
      position: keepPos ? old.position : n.position,
      selected: old.selected,
      data: { ...n.data, selected: old.data?.selected },
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
  const nodesRef = useRef<Node[]>([]);
  const viewportRef = useRef<Viewport>(DEFAULT_VIEWPORT);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

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

  const load = useCallback(async () => {
    try {
      const [topo, layout] = await Promise.all([api.topology(), api.topologyLayout()]);
      const nextNodes = toFlowNodes(topo.nodes, selectedCluster, selectedNode);
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
      }
      loadedOnce.current = true;
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [selectedCluster, selectedNode]);

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
