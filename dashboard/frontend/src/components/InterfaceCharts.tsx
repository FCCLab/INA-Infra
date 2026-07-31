import { useEffect, useMemo, useState } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";
import { api, type NodeInterface } from "../api/client";
import { finiteOrNull, fmtNum, isFiniteNumber } from "../lib/format";
import { readThemeColors } from "../lib/theme";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

type Props = {
  cluster: string;
  node: string;
  refreshToken: number;
};

const PHYS_COLORS = (accent: string, accent2: string) => [
  accent,
  accent2,
  "#f5a623",
  "#15D6C6",
  "#3E9BFF",
];

export default function InterfaceCharts({ cluster, node, refreshToken }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState("prometheus");
  const [ifaces, setIfaces] = useState<NodeInterface[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [series, setSeries] = useState<
    Record<string, { rx_mbps?: (number | null)[]; tx_mbps?: (number | null)[] }>
  >({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.nodeInterfaces(cluster, node);
        if (cancelled) return;
        setError(res.error || null);
        setSource(res.source || "prometheus");
        const physical = (res.interfaces || []).filter((i) => i.kind === "physical");
        setIfaces(physical);
        const hist = res.history;
        setLabels(hist?.labels || []);
        setSeries(hist?.series || {});
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cluster, node, refreshToken]);

  const colors = readThemeColors();
  const physNames = useMemo(() => {
    const fromIfaces = ifaces.map((i) => i.name);
    if (fromIfaces.length) return [...fromIfaces].sort();
    return Object.keys(series).sort();
  }, [ifaces, series]);
  const axis = {
    ticks: { color: colors.textDim, maxTicksLimit: 6 },
    grid: { color: colors.grid },
  };
  const palette = PHYS_COLORS(colors.accent, colors.accent2);

  function buildChart(direction: "rx" | "tx", title: string) {
    const key = direction === "rx" ? "rx_mbps" : "tx_mbps";
    const datasets = physNames.map((name, i) => {
      const s = series[name];
      const color = palette[i % palette.length];
      const data = (s?.[key] || []).map((v) => finiteOrNull(v));
      return {
        label: name,
        data,
        borderColor: color,
        backgroundColor: color + "33",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
        spanGaps: true,
      };
    });
    return (
      <div className="chart-card">
        <h3>{title}</h3>
        <div className="chart-box chart-box-tall">
          {datasets.length === 0 || labels.length < 2 ? (
            <p className="muted" style={{ padding: 12 }}>
              Waiting for Prometheus history (node_exporter scrape)…
            </p>
          ) : (
            <Line
              data={{ labels, datasets }}
              options={{
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                  legend: {
                    position: "bottom",
                    labels: { color: colors.textDim, boxWidth: 12 },
                  },
                  tooltip: {
                    callbacks: {
                      label(ctx) {
                        const v = ctx.parsed.y;
                        return `${ctx.dataset.label}: ${fmtNum(v, 3)} Mbps`;
                      },
                    },
                  },
                },
                scales: {
                  x: { ...axis, grid: { display: false } },
                  y: {
                    ...axis,
                    beginAtZero: true,
                    title: { display: true, text: "Mbps", color: colors.textDim },
                  },
                },
              }}
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="charts" style={{ marginTop: 12 }}>
      {error ? <div className="error-banner">{error}</div> : null}

      <div className="chart-card">
        <h3>
          Physical interfaces (up) — {node}
          <span className="gauge-kicker">{source}</span>
        </h3>
        <div className="iface-legend">
          <span className="tag tag-phys">physical · up</span>
          {ifaces.length === 0 ? (
            <span className="muted">No up physical NIC series yet</span>
          ) : (
            ifaces.map((i) => {
              const rxOk = isFiniteNumber(i.rx_mbps);
              const txOk = isFiniteNumber(i.tx_mbps);
              return (
                <span key={i.name} className="mono iface-chip">
                  {i.name}
                  {rxOk || txOk
                    ? ` · ↓${fmtNum(i.rx_mbps, 2)} ↑${fmtNum(i.tx_mbps, 2)} Mb/s`
                    : " · —"}
                </span>
              );
            })
          )}
        </div>
      </div>

      <div className="resource-gauges">
        {buildChart("rx", "RX throughput (Mbps)")}
        {buildChart("tx", "TX throughput (Mbps)")}
      </div>
    </div>
  );
}
