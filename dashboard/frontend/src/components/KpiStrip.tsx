import type { ReactNode } from "react";
import type { ClusterSummary } from "../api/client";

type Props = {
  clusters: ClusterSummary[];
};

const ICONS = {
  grid: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
    </svg>
  ),
  server: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="4" width="18" height="6" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <path d="M7 7h.01M7 17h.01" strokeLinecap="round" />
    </svg>
  ),
  layers: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 2 2 7l10 5 10-5-10-5Z" />
      <path d="m2 17 10 5 10-5M2 12l10 5 10-5" />
    </svg>
  ),
  alert: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
    </svg>
  ),
};

type KpiItem = {
  label: string;
  kicker?: string;
  value: string;
  unit?: string;
  icon: ReactNode;
  bad?: boolean;
};

export default function KpiStrip({ clusters }: Props) {
  const up = clusters.filter((c) => c.reachable).length;
  const nodes = clusters.reduce((n, c) => n + c.nodes, 0);
  const pods = clusters.reduce((n, c) => n + c.pods, 0);
  const unhealthy = clusters.filter(
    (c) => !c.reachable || c.health === "degraded" || c.health === "error",
  ).length;

  const items: KpiItem[] = [
    {
      label: "Clusters up",
      kicker: "mgmt · central · regional · edge",
      value: `${up}`,
      unit: `/${clusters.length || 4}`,
      icon: ICONS.grid,
    },
    {
      label: "Nodes",
      kicker: "across clusters",
      value: String(nodes),
      icon: ICONS.server,
    },
    {
      label: "Pods",
      kicker: "running inventory",
      value: String(pods),
      icon: ICONS.layers,
    },
    {
      label: "Unhealthy",
      kicker: "degraded or down",
      value: String(unhealthy),
      icon: ICONS.alert,
      bad: unhealthy > 0,
    },
  ];

  return (
    <div className="kpi-strip" data-cols={items.length}>
      {items.map((it) => (
        <div key={it.label} className={"kpi-card" + (it.bad ? " kpi-bad" : "")}>
          <span className="kpi-icon">{it.icon}</span>
          <div className="kpi-label">
            {it.label}
            {it.kicker ? <span className="kpi-kicker">{it.kicker}</span> : null}
          </div>
          <div className="kpi-value">
            {it.value}
            {it.unit ? <span className="kpi-unit">{it.unit}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}
