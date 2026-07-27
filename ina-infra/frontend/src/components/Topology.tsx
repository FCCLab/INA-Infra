import { SliceResultOut } from "../api/client";

/** Matplotlib tab10 — same palette as algorithm simulation_fig2 */
const TAB10 = [
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#e377c2",
  "#7f7f7f",
  "#bcbd22",
  "#17becf",
];

const ZONE_COLORS = ["#e6f2ff", "#fff0e6", "#e6ffe6"];
const ZONE_LABELS = ["Edge DC", "Regional DC", "Central DC"];

/** Within-zone x offsets (same as plots.py): CU left, UPF center, APP right */
const OFFSET = { cu: -0.15, upf: 0, app: 0.15 };

type Props = { slices: SliceResultOut[] };

function sliceColor(id: number): string {
  return TAB10[(id - 1) % TAB10.length];
}

function siteId(name: string): number {
  if (name === "Edge") return 0;
  if (name === "Regional") return 1;
  return 2;
}

/** Circle = CU-UP, Square = UPF, Triangle = APP — all share a 2×size bounding box */
function Marker({
  kind,
  x,
  y,
  color,
  size = 9,
}: {
  kind: "cu" | "upf" | "app";
  x: number;
  y: number;
  color: string;
  size?: number;
}) {
  const s = size;
  const sw = Math.max(1, size * 0.15);
  if (kind === "cu") {
    return (
      <circle
        cx={x}
        cy={y}
        r={s}
        fill={color}
        stroke="#111"
        strokeWidth={sw}
      />
    );
  }
  if (kind === "upf") {
    return (
      <rect
        x={x - s}
        y={y - s}
        width={s * 2}
        height={s * 2}
        fill={color}
        stroke="#111"
        strokeWidth={sw}
      />
    );
  }
  // Same height/width as circle & square: tip top, base corners at box bottom
  const points = `${x},${y - s} ${x - s},${y + s} ${x + s},${y + s}`;
  return (
    <polygon points={points} fill={color} stroke="#111" strokeWidth={sw} />
  );
}

function LegendIcon({ kind }: { kind: "cu" | "upf" | "app" }) {
  return (
    <svg width={18} height={18} viewBox="0 0 18 18" aria-hidden>
      <Marker kind={kind} x={9} y={9} color="#555" size={4} />
    </svg>
  );
}

export default function Topology({ slices }: Props) {
  // Slice 1 on top, then 2, 3, ... downward
  const sorted = [...slices].sort((a, b) => a.id - b.id);
  const maxId = sorted.length ? Math.max(...sorted.map((s) => s.id)) : 1;
  const minId = sorted.length ? Math.min(...sorted.map((s) => s.id)) : 1;
  /** Plot y so smaller id is higher on screen (after SVG invert). */
  const rowY = (id: number) => maxId - id + minId;

  // SVG mapping: x in [-0.5, 2.5], y in [0.5, maxId+2]
  const padL = 90;
  const padR = 24;
  const padT = 48;
  const padB = 24;
  const plotW = 720;
  const rowH = 48;
  const plotH = Math.max(280, (maxId - minId + 1) * rowH + 80);
  const W = padL + plotW + padR;
  const H = padT + plotH + padB;

  const xMin = -0.5;
  const xMax = 2.5;
  const yMin = 0.5;
  const yMax = maxId + 2;

  const sx = (x: number) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  // In mpl y increases upward; SVG y increases downward → invert
  const sy = (y: number) => padT + ((yMax - y) / (yMax - yMin)) * plotH;

  const zoneWidth = 0.6;

  return (
    <div className="topo topo-fig2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Optimal Deployment Topology"
      >
        <text
          x={W / 2}
          y={28}
          textAnchor="middle"
          className="topo-title"
        >
          Optimal Deployment Topology
        </text>

        {/* DC zones */}
        {[0, 1, 2].map((j) => {
          const cx = j;
          const x0 = sx(cx - zoneWidth / 2);
          const x1 = sx(cx + zoneWidth / 2);
          return (
            <g key={j}>
              <rect
                x={x0}
                y={sy(yMax)}
                width={x1 - x0}
                height={sy(yMin) - sy(yMax)}
                fill={ZONE_COLORS[j]}
                opacity={0.85}
              />
              <text
                x={sx(cx)}
                y={sy(maxId + 1.5)}
                textAnchor="middle"
                className="topo-zone"
              >
                {ZONE_LABELS[j]}
              </text>
            </g>
          );
        })}

        {/* Horizontal grid + slice labels (slice 1 at top) */}
        {sorted.map((s) => (
          <g key={`grid-${s.id}`}>
            <line
              x1={sx(xMin)}
              x2={sx(xMax)}
              y1={sy(rowY(s.id))}
              y2={sy(rowY(s.id))}
              stroke="#ccc"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
            <text
              x={padL - 12}
              y={sy(rowY(s.id)) + 4}
              textAnchor="end"
              className="topo-slice-label"
            >
              Slice {s.id}
            </text>
          </g>
        ))}

        <text
          x={18}
          y={padT + plotH / 2}
          textAnchor="middle"
          className="topo-axis"
          transform={`rotate(-90 18 ${padT + plotH / 2})`}
        >
          Network Slice
        </text>

        {/* Links + markers */}
        {sorted.map((s) => {
          const color = sliceColor(s.id);
          const y = rowY(s.id);
          const pts = [
            { kind: "cu" as const, x: siteId(s.placement.cu) + OFFSET.cu, y },
            { kind: "upf" as const, x: siteId(s.placement.upf) + OFFSET.upf, y },
            { kind: "app" as const, x: siteId(s.placement.app) + OFFSET.app, y },
          ];
          const ordered = [...pts].sort((a, b) => a.x - b.x);
          const path = ordered
            .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x)} ${sy(p.y)}`)
            .join(" ");
          return (
            <g key={s.id}>
              <path
                d={path}
                fill="none"
                stroke={color}
                strokeWidth={2.5}
                opacity={0.65}
              />
              {pts.map((p) => (
                <Marker
                  key={p.kind}
                  kind={p.kind}
                  x={sx(p.x)}
                  y={sy(p.y)}
                  color={color}
                />
              ))}
            </g>
          );
        })}
      </svg>

      {/* Legend outside the figure (below) so it never overlaps markers */}
      <ul className="topo-legend-bar" aria-label="Marker legend">
        <li>
          <LegendIcon kind="cu" />
          <span>CU-UP</span>
        </li>
        <li>
          <LegendIcon kind="upf" />
          <span>UPF</span>
        </li>
        <li>
          <LegendIcon kind="app" />
          <span>APP</span>
        </li>
      </ul>
    </div>
  );
}
