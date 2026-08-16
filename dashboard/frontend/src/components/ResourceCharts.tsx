import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
} from "chart.js";
import { Bar, Doughnut } from "react-chartjs-2";
import type { Metrics, NodeInfo } from "../api/client";
import { fmtNum, finiteOrZero } from "../lib/format";
import { bytesToGi, parseCpuCores, parseMemBytes } from "../lib/k8sUnits";
import { findByNodeName } from "../lib/nodeNames";
import { readThemeColors, type ThemeColors } from "../lib/theme";

ChartJS.register(ArcElement, BarElement, CategoryScale, LinearScale, Tooltip, Legend);

type Props = {
  metrics: Metrics | null;
  /** When set, show that node's Prometheus usage vs allocatable. */
  focusNode?: string | null;
  nodes?: NodeInfo[];
};

function pctOf(used: number, total: number): number {
  const u = finiteOrZero(used);
  const t = finiteOrZero(total);
  if (t <= 0) return 0;
  const p = (u / t) * 100;
  if (!Number.isFinite(p)) return 0;
  return Math.min(100, Math.max(0, p));
}

function usageColor(pct: number, c: ThemeColors): string {
  if (pct >= 90) return c.bad;
  if (pct >= 70) return c.orange;
  return c.accent;
}

function ResourceGauge({
  title,
  kicker,
  used,
  total,
  unit,
  colors,
}: {
  title: string;
  kicker: string;
  used: number;
  total: number;
  unit: string;
  colors: ThemeColors;
}) {
  const u = finiteOrZero(used);
  const t = finiteOrZero(total);
  const pct = pctOf(u, t);
  const fill = usageColor(pct, colors);
  const size = 200;
  const stroke = 26;
  const cx = size / 2;
  const cy = size / 2;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const usedLabel = `${fmtNum(u, 2)} / ${fmtNum(t, 2)} ${unit}`;

  return (
    <div className="chart-card gauge-card">
      <h3>
        {title}
        <span className="gauge-kicker">{kicker}</span>
      </h3>
      <div className="gauge-box" title={`Used ${usedLabel} (${Math.round(pct)}%)`}>
        <svg
          className="gauge-svg"
          viewBox={`0 0 ${size} ${size}`}
          width="100%"
          height="100%"
          role="img"
          aria-label={`${title} ${Math.round(pct)} percent`}
        >
          {/* Track */}
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={colors.track}
            strokeWidth={stroke}
          />
          {/* Progress: start at 12 o'clock, fill counter-clockwise */}
          <g transform={`translate(${cx} ${cy}) scale(-1 1) translate(${-cx} ${-cy})`}>
            <circle
              cx={cx}
              cy={cy}
              r={r}
              fill="none"
              stroke={fill}
              strokeWidth={stroke}
              strokeLinecap="round"
              strokeDasharray={`${dash} ${circ}`}
              transform={`rotate(-90 ${cx} ${cy})`}
            />
          </g>
          <text
            x={cx}
            y={cy - 6}
            textAnchor="middle"
            dominantBaseline="middle"
            className="gauge-svg-pct"
            fill={fill}
          >
            {Math.round(pct)}
            <tspan className="gauge-svg-pct-unit" fill={colors.textDim} dx="2">
              %
            </tspan>
          </text>
          <text
            x={cx}
            y={cy + 22}
            textAnchor="middle"
            dominantBaseline="middle"
            className="gauge-svg-sub"
            fill={colors.textDim}
          >
            {usedLabel}
          </text>
        </svg>
      </div>
    </div>
  );
}

export default function ResourceCharts({ metrics, focusNode, nodes = [] }: Props) {
  if (!metrics) {
    return <p className="muted">Select a cluster to load charts.</p>;
  }

  const c = readThemeColors();
  const axis = {
    ticks: { color: c.textDim },
    grid: { color: c.grid },
  };

  const res = metrics.resources;
  const sourceLabel =
    res?.source === "prometheus"
      ? "prometheus"
      : res?.source === "metrics-server"
        ? "metrics-server"
        : res?.source === "requests-vs-allocatable"
          ? "requests vs allocatable"
          : res?.source || "n/a";

  if (focusNode) {
    const nodeUsage = findByNodeName(res?.nodes, focusNode);
    const nodeInfo = findByNodeName(nodes, focusNode);
    const cpuUsage = nodeUsage?.cpu_cores ?? 0;
    const memUsage = bytesToGi(nodeUsage?.memory_bytes);
    const cpuAlloc = parseCpuCores(nodeInfo?.allocatable.cpu || nodeInfo?.capacity.cpu);
    const memAlloc = bytesToGi(
      parseMemBytes(nodeInfo?.allocatable.memory || nodeInfo?.capacity.memory),
    );
    const sampled =
      nodeUsage == null ? false : nodeUsage.sampled !== false;
    const gpuNode = findByNodeName(res?.gpus?.nodes, focusNode);
    const gpu0 = gpuNode?.gpus?.[0];
    const hasGpu = Boolean(gpu0) || (nodeInfo?.gpu_count || 0) > 0;

    return (
      <div className="charts">
        {metrics.error ? <div className="error-banner">{metrics.error}</div> : null}
        {!nodeUsage && res?.source === "prometheus" ? (
          <div className="error-banner">
            No Prometheus / node_exporter sample for {focusNode}.
          </div>
        ) : null}
        {nodeUsage && !sampled ? (
          <div className="error-banner">
            Collecting node_exporter samples for {focusNode}…
          </div>
        ) : null}
        {!nodeUsage && res?.source && res.source !== "prometheus" ? (
          <div className="error-banner">
            Per-node usage needs Prometheus (cluster is on {res.source}).
          </div>
        ) : null}

        <div className="resource-gauges">
          <ResourceGauge
            title="CPU"
            kicker={`${focusNode} · ${sourceLabel}`}
            used={cpuUsage}
            total={cpuAlloc}
            unit="cores"
            colors={c}
          />
          <ResourceGauge
            title="Memory"
            kicker={`${focusNode} · ${sourceLabel}`}
            used={memUsage}
            total={memAlloc}
            unit="GiB"
            colors={c}
          />
        </div>

        {hasGpu ? (
          <div className="resource-gauges">
            <ResourceGauge
              title="GPU"
              kicker={
                gpu0
                  ? `${gpu0.model} · ${res?.gpus?.source || "prometheus"}`
                  : `${focusNode} · no sample`
              }
              used={finiteOrZero(gpu0?.util_pct)}
              total={100}
              unit="%"
              colors={c}
            />
            <ResourceGauge
              title="vRAM"
              kicker={
                gpu0
                  ? `${fmtNum(gpu0.memory_used_mib / 1024, 1)} / ${fmtNum(gpu0.memory_total_mib / 1024, 1)} GiB`
                  : "—"
              }
              used={finiteOrZero(gpu0?.memory_used_mib) / 1024}
              total={finiteOrZero(gpu0?.memory_total_mib) / 1024 || 1}
              unit="GiB"
              colors={c}
            />
          </div>
        ) : null}

        {gpuNode?.error ? (
          <div className="error-banner">GPU: {gpuNode.error}</div>
        ) : null}

        <div className="status-line" style={{ marginTop: 2 }}>
          <span className={`status-dot ${nodeInfo?.ready ? "dot-ok" : "dot-bad"}`} />
          <span className="mono">
            {focusNode} · {nodeInfo ? (nodeInfo.ready ? "Ready" : "NotReady") : "—"}
            {nodeInfo?.roles?.length ? ` · ${nodeInfo.roles.join(", ")}` : ""}
            {gpu0 ? ` · ${gpu0.model}` : ""}
          </span>
        </div>
      </div>
    );
  }

  const cpuUsage = res?.cpu_usage_cores ?? 0;
  const cpuAlloc = res?.cpu_allocatable_cores ?? 0;
  const memUsage = bytesToGi(res?.memory_usage_bytes);
  const memAlloc = bytesToGi(res?.memory_allocatable_bytes);

  const perNode = res?.nodes || [];
  const nodeNames = perNode.map((n) => n.name);
  const nodeCpu = {
    labels: nodeNames,
    datasets: [
      {
        label: "CPU cores",
        data: perNode.map((n) => finiteOrZero(n.cpu_cores)),
        backgroundColor: c.accent,
        borderRadius: 6,
        borderWidth: 0,
      },
    ],
  };
  const nodeMem = {
    labels: nodeNames,
    datasets: [
      {
        label: "Memory GiB",
        data: perNode.map((n) => finiteOrZero(bytesToGi(n.memory_bytes))),
        backgroundColor: c.accent2,
        borderRadius: 6,
        borderWidth: 0,
      },
    ],
  };

  const phases = metrics.pod_phases || {};
  const phaseLabels = Object.keys(phases);
  const phaseValues = phaseLabels.map((k) => phases[k]);
  const podChart = {
    labels: phaseLabels.length ? phaseLabels : ["none"],
    datasets: [
      {
        data: phaseValues.length ? phaseValues : [1],
        backgroundColor: [c.accent, c.accent2, c.orange, c.bad, c.textDim, "#7C5CFF"],
        borderWidth: 0,
      },
    ],
  };

  const wl = metrics.workloads || {};
  const workloadChart = {
    labels: ["Desired", "Ready", "Unhealthy"],
    datasets: [
      {
        label: "Replicas",
        data: [wl.desired || 0, wl.ready || 0, wl.unhealthy || 0],
        backgroundColor: [c.accent2, c.accent, c.bad],
        borderWidth: 0,
        borderRadius: 6,
      },
    ],
  };

  return (
    <div className="charts">
      {metrics.error ? <div className="error-banner">{metrics.error}</div> : null}

      <div className="resource-gauges">
        <ResourceGauge
          title="CPU"
          kicker={`cluster · ${sourceLabel}`}
          used={cpuUsage}
          total={cpuAlloc}
          unit="cores"
          colors={c}
        />
        <ResourceGauge
          title="Memory"
          kicker={`cluster · ${sourceLabel}`}
          used={memUsage}
          total={memAlloc}
          unit="GiB"
          colors={c}
        />
      </div>

      {perNode.length > 0 ? (
        <div className="resource-gauges">
          <div className="chart-card">
            <h3>CPU by node</h3>
            <div className="chart-box chart-box-tall">
              <Bar
                data={nodeCpu}
                options={{
                  indexAxis: "y",
                  maintainAspectRatio: false,
                  plugins: { legend: { display: false } },
                  scales: {
                    x: { ...axis, beginAtZero: true },
                    y: { ...axis, grid: { display: false } },
                  },
                }}
              />
            </div>
          </div>
          <div className="chart-card">
            <h3>Memory by node</h3>
            <div className="chart-box chart-box-tall">
              <Bar
                data={nodeMem}
                options={{
                  indexAxis: "y",
                  maintainAspectRatio: false,
                  plugins: { legend: { display: false } },
                  scales: {
                    x: { ...axis, beginAtZero: true },
                    y: { ...axis, grid: { display: false } },
                  },
                }}
              />
            </div>
          </div>
        </div>
      ) : null}

      <div className="charts-secondary">
        <div className="chart-card">
          <h3>Pod phases</h3>
          <div className="chart-box chart-box-sm">
            <Doughnut
              data={podChart}
              options={{
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom", labels: { color: c.textDim } } },
              }}
            />
          </div>
        </div>
        <div className="chart-card">
          <h3>Workload readiness</h3>
          <div className="chart-box chart-box-sm">
            <Bar
              data={workloadChart}
              options={{
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                  x: axis,
                  y: { ...axis, beginAtZero: true },
                },
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
