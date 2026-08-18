import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ConfigSyncStatus } from "../api/client";
import ConfigSyncIcon from "./ConfigSyncIcon";

export type ClusterNodeData = {
  label: string;
  health: string;
  reachable: boolean;
  nodes: number;
  nodes_ready: number;
  pods: number;
  pods_running: number;
  latency_ms?: number;
  error?: string | null;
  selected?: boolean;
  config_sync?: ConfigSyncStatus | null;
};

const CLUSTER_TITLES: Record<string, string> = {
  mgmt: "Management",
  central: "Central",
  regional: "Regional",
  edge: "Edge",
};

const HEALTH_LABELS: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  error: "Error",
  unreachable: "Unreachable",
};

function titleFor(label: string): string {
  return CLUSTER_TITLES[label] || label.charAt(0).toUpperCase() + label.slice(1);
}

function ClusterNodeComponent({ data, selected }: NodeProps & { data: ClusterNodeData }) {
  const d = data;
  const health = d.reachable ? d.health || "unreachable" : "unreachable";
  const healthLabel = HEALTH_LABELS[health] || health;
  const title = titleFor(d.label);

  return (
    <div className={`cluster-node group${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Left} id="left" />
      <Handle type="target" position={Position.Top} id="top" />

      <div className="cluster-header-zone">
        <div className="cluster-title-row">
          <span className={`dot ${health}`} />
          <span className="cluster-title">{title}</span>
          <span className="cluster-status-icons">
            <span className={`health-badge ${health}`}>{healthLabel}</span>
            <ConfigSyncIcon status={d.config_sync} showLabel />
          </span>
        </div>

        <div className="cluster-props">
          <div className="prop-line">
            <span className="prop-key">ID</span>
            <span className="prop-val">{d.label}</span>
          </div>
          <div className="prop-line">
            <span className="prop-key">Nodes</span>
            <span className="prop-val">
              {d.nodes_ready}/{d.nodes}
            </span>
          </div>
          <div className="prop-line">
            <span className="prop-key">Pods</span>
            <span className="prop-val">
              {d.pods_running}/{d.pods}
            </span>
          </div>
          {typeof d.latency_ms === "number" && Number.isFinite(d.latency_ms) ? (
            <div className="prop-line">
              <span className="prop-key">API</span>
              <span className="prop-val">{Math.round(d.latency_ms)} ms</span>
            </div>
          ) : (
            <div className="prop-line">
              <span className="prop-key">API</span>
              <span className="prop-val">—</span>
            </div>
          )}
        </div>
      </div>

      <div className="cluster-nodes-slot" aria-hidden="true" />

      <Handle type="source" position={Position.Right} id="right" />
      <Handle type="source" position={Position.Bottom} id="bottom" />
    </div>
  );
}

export default memo(ClusterNodeComponent);
