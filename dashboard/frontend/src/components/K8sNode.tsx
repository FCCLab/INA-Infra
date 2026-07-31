import { memo } from "react";
import { type NodeProps } from "@xyflow/react";

export type K8sNodeData = {
  label: string;
  cluster: string;
  ready: boolean;
  roles: string[];
  kubelet_version?: string;
  selected?: boolean;
};

function shortName(name: string): string {
  const parts = name.split(".");
  return parts[0] || name;
}

function roleLabel(roles: string[]): string {
  if (!roles.length) return "Worker";
  return roles
    .slice(0, 2)
    .map((r) => r.charAt(0).toUpperCase() + r.slice(1))
    .join(" · ");
}

function K8sNodeComponent({ data, selected }: NodeProps & { data: K8sNodeData }) {
  const d = data;
  const isSelected = Boolean(selected || d.selected);
  return (
    <div
      className={
        "k8s-node" +
        (d.ready ? " ready" : " not-ready") +
        (isSelected ? " selected" : "")
      }
    >
      <div className="k8s-name">
        <span className={`dot ${d.ready ? "healthy" : "error"}`} aria-hidden="true" />
        <span className="k8s-name-text">{shortName(d.label)}</span>
      </div>
      <div className="k8s-meta">
        {roleLabel(d.roles || [])}
        {d.ready ? "" : " · Not ready"}
      </div>
    </div>
  );
}

export default memo(K8sNodeComponent);
