import { useEffect, useRef, useState } from "react";
import { api, type Metrics, type NodeInfo } from "../api/client";
import { fmtNum, isFiniteNumber } from "../lib/format";
import InterfaceCharts from "./InterfaceCharts";
import ResourceCharts from "./ResourceCharts";

type Props = {
  cluster: string | null;
  selectedNode: string | null;
  onSelectNode: (nodeName: string | null) => void;
  refreshToken: number;
};

export default function ClusterDetailPanel({
  cluster,
  selectedNode,
  onSelectNode,
  refreshToken,
}: Props) {
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const clusterRef = useRef(cluster);
  const inFlight = useRef<string | null>(null);

  useEffect(() => {
    clusterRef.current = cluster;
  }, [cluster]);

  useEffect(() => {
    if (!cluster) {
      inFlight.current = null;
      setNodes([]);
      setMetrics(null);
      setError(null);
      setLoading(false);
      return;
    }

    // /metrics often takes > poll interval; stacking requests means every
    // response is treated as stale and the panel stays empty.
    if (inFlight.current === cluster) return;

    const reqCluster = cluster;
    inFlight.current = reqCluster;
    setLoading(true);
    (async () => {
      try {
        const [n, m] = await Promise.all([api.nodes(reqCluster), api.metrics(reqCluster)]);
        if (clusterRef.current !== reqCluster) return;
        setNodes(n.items);
        setMetrics(m);
        setError(n.error || m.error || null);
      } catch (err) {
        if (clusterRef.current !== reqCluster) return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (inFlight.current === reqCluster) inFlight.current = null;
        if (clusterRef.current === reqCluster) setLoading(false);
      }
    })();
  }, [cluster, refreshToken]);

  if (!cluster) {
    return (
      <div className="panel-body">
        <p className="muted">Click a cluster or node on the topology to inspect metrics.</p>
      </div>
    );
  }

  const usageByName = new Map(
    (metrics?.resources?.nodes || []).map((n) => [n.name, n]),
  );
  const gpuByName = new Map(
    (metrics?.resources?.gpus?.nodes || []).map((n) => [n.name, n]),
  );
  const showGpuCols =
    nodes.some((n) => (n.gpu_count || 0) > 0) || gpuByName.size > 0;

  return (
    <div className="panel-body">
      {error ? <div className="error-banner">{error}</div> : null}
      {loading && !metrics ? <p className="muted">Loading metrics…</p> : null}
      <ResourceCharts metrics={metrics} focusNode={selectedNode} nodes={nodes} />
      {selectedNode ? (
        <InterfaceCharts
          cluster={cluster}
          node={selectedNode}
          refreshToken={refreshToken}
        />
      ) : null}
      <div className="chart-card" style={{ marginTop: "0.85rem" }}>
        <h3>
          Nodes — {cluster}
          {selectedNode ? (
            <button
              type="button"
              className="icon-btn"
              style={{ marginLeft: 10, padding: "3px 8px", fontSize: 11 }}
              onClick={() => onSelectNode(null)}
            >
              Clear node
            </button>
          ) : null}
        </h3>
        {nodes.length === 0 ? (
          <p className="muted">{loading ? "Loading nodes…" : "No nodes returned."}</p>
        ) : (
          <table className="node-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Ready</th>
                <th>CPU</th>
                <th>Mem</th>
                {showGpuCols ? <th>GPU</th> : null}
                {showGpuCols ? <th>vRAM</th> : null}
                <th>Roles</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n) => {
                const usage = usageByName.get(n.name);
                const gpu = gpuByName.get(n.name)?.gpus?.[0];
                const active = selectedNode === n.name;
                return (
                  <tr
                    key={n.name}
                    className={active ? "is-selected" : undefined}
                    onClick={() => onSelectNode(active ? null : n.name)}
                    style={{ cursor: "pointer" }}
                  >
                    <td>{n.name}</td>
                    <td>
                      <span className="node-status">
                        <span
                          className={`dot ${n.ready ? "healthy" : "error"}`}
                          aria-hidden="true"
                        />
                        <span>{n.ready ? "Ready" : "NotReady"}</span>
                      </span>
                    </td>
                    <td className="mono">
                      {usage ? fmtNum(usage.cpu_cores, 2) : "—"}
                    </td>
                    <td className="mono">
                      {usage && isFiniteNumber(usage.memory_bytes)
                        ? `${fmtNum(usage.memory_bytes / 1024 ** 3, 2)} Gi`
                        : "—"}
                    </td>
                    {showGpuCols ? (
                      <td className="mono">
                        {gpu && isFiniteNumber(gpu.util_pct)
                          ? `${Math.round(gpu.util_pct)}%`
                          : n.gpu_count
                            ? "…"
                            : "—"}
                      </td>
                    ) : null}
                    {showGpuCols ? (
                      <td className="mono">
                        {gpu &&
                        isFiniteNumber(gpu.memory_used_mib) &&
                        isFiniteNumber(gpu.memory_total_mib)
                          ? `${fmtNum(gpu.memory_used_mib / 1024, 1)}/${fmtNum(gpu.memory_total_mib / 1024, 0)}`
                          : "—"}
                      </td>
                    ) : null}
                    <td>{n.roles.join(", ") || "worker"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
