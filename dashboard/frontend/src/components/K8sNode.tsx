import { memo } from "react";
import { type NodeProps } from "@xyflow/react";
import { finiteOrZero, fmtNum } from "../lib/format";

export type K8sNodeUsage = {
  /** null = no sample yet */
  cpu_pct: number | null;
  mem_pct: number | null;
  /** node has nvidia.com/gpu or a DCGM sample */
  has_gpu?: boolean;
  /** null = no GPU sample yet (still show row when has_gpu) */
  gpu_pct: number | null;
  vram_pct: number | null;
  vram_used_gib?: number | null;
  vram_total_gib?: number | null;
  sampled?: boolean;
};

export type K8sNodeData = {
  label: string;
  cluster: string;
  ready: boolean;
  roles: string[];
  kubelet_version?: string;
  selected?: boolean;
  gpu_count?: number;
  has_gpu?: boolean;
  usage?: K8sNodeUsage | null;
};

/** Keep in sync with topology.py + ClusterTopology layout constants. */
export const K8S_NODE_H = 42;
export const K8S_NODE_H_GPU = 56;

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

function meterTone(pct: number | null): string {
  if (pct == null || !Number.isFinite(pct)) return "";
  if (pct >= 90) return " hot";
  if (pct >= 70) return " warm";
  return "";
}

function fmtPct(pct: number | null): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  return `${Math.round(pct)}%`;
}

function Meter({
  label,
  pct,
  text,
}: {
  label: string;
  pct: number | null;
  text?: string;
}) {
  const fill = pct == null ? 0 : Math.min(100, Math.max(0, finiteOrZero(pct)));
  const value = text ?? fmtPct(pct);
  return (
    <div className={"k8s-meter" + meterTone(pct)} title={`${label} ${value}`}>
      <span className="k8s-meter-label">{label}</span>
      <span className="k8s-meter-track" aria-hidden="true">
        <span className="k8s-meter-fill" style={{ width: `${fill}%` }} />
      </span>
      <span className="k8s-meter-val">{value}</span>
    </div>
  );
}

function K8sNodeComponent({ data, selected }: NodeProps & { data: K8sNodeData }) {
  const d = data;
  const isSelected = Boolean(selected || d.selected);
  const u = d.usage;
  const hasUsage = Boolean(u);
  const hasGpu = Boolean(u?.has_gpu ?? d.has_gpu ?? (d.gpu_count || 0) > 0);
  const vramText =
    hasGpu &&
    u?.vram_used_gib != null &&
    u?.vram_total_gib != null &&
    Number.isFinite(u.vram_used_gib) &&
    Number.isFinite(u.vram_total_gib)
      ? `${fmtNum(u.vram_used_gib, 1)}/${fmtNum(u.vram_total_gib, 0)}`
      : undefined;

  return (
    <div
      className={
        "k8s-node" +
        (d.ready ? " ready" : " not-ready") +
        (isSelected ? " selected" : "") +
        (hasUsage ? " has-usage" : "") +
        (hasGpu ? " has-gpu" : "")
      }
    >
      <div className="k8s-head">
        <span className={`dot ${d.ready ? "healthy" : "error"}`} aria-hidden="true" />
        <span className="k8s-name-text">{shortName(d.label)}</span>
      </div>
      {hasUsage ? (
        <>
          <div className="k8s-meter-row">
            <Meter label="CPU" pct={u?.cpu_pct ?? null} />
            <Meter label="MEM" pct={u?.mem_pct ?? null} />
          </div>
          {hasGpu ? (
            <div className="k8s-meter-row">
              <Meter label="GPU" pct={u?.gpu_pct ?? null} />
              <Meter label="VR" pct={u?.vram_pct ?? null} text={vramText} />
            </div>
          ) : null}
        </>
      ) : (
        <div className="k8s-meta">
          {roleLabel(d.roles || [])}
          {d.ready ? "" : " · Not ready"}
        </div>
      )}
    </div>
  );
}

export default memo(K8sNodeComponent);
