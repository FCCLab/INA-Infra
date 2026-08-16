import type { ReactNode } from "react";

export type KpiItem = {
  label: string;
  kicker?: string;
  value: ReactNode;
  unit?: string;
  icon?: ReactNode;
  bad?: boolean;
};

export default function KpiStrip({ items }: { items: KpiItem[] }) {
  const list = Array.isArray(items) ? items : [];
  const cols = Math.max(2, Math.min(5, list.length || 5));
  return (
    <div className="kpi-strip" data-cols={cols}>
      {list.map((it, i) => (
        <div
          key={it.label || i}
          className={"kpi-card" + (it.bad ? " kpi-bad" : "")}
        >
          {it.icon && <span className="kpi-icon">{it.icon}</span>}
          <div className="kpi-label">
            {it.label}
            {it.kicker && <span className="kpi-kicker">{it.kicker}</span>}
          </div>
          <div className="kpi-value">
            {it.value}
            {it.unit && <span className="kpi-unit">{it.unit}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
