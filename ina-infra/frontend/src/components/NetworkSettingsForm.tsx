import FieldHelp from "./FieldHelp";
import type { NetworkIn } from "../api/client";

const SITES = [
  { id: 0, name: "Edge" },
  { id: 1, name: "Regional" },
  { id: 2, name: "Central" },
] as const;

function siteVal(
  map: Record<string, number> | Record<number, number> | null | undefined,
  id: number,
  fallback = 0,
): number {
  if (!map) return fallback;
  const v = (map as Record<string | number, number>)[id] ?? (map as Record<string, number>)[String(id)];
  return v === undefined || v === null ? fallback : Number(v);
}

/** F1: CU site value; also accepts legacy "0-j" Edge-DU row keys. */
function f1Val(
  map: Record<string, number> | null | undefined,
  cuSite: number,
  fallback = 0,
): number {
  if (!map) return fallback;
  const direct = map[String(cuSite)];
  if (direct !== undefined && direct !== null) return Number(direct);
  const pair = map[`0-${cuSite}`] ?? map[`0,${cuSite}`];
  return pair === undefined || pair === null ? fallback : Number(pair);
}

function pairVal(
  map: Record<string, number> | null | undefined,
  i: number,
  j: number,
  fallback = 0,
): number {
  if (!map) return fallback;
  const k1 = `${i}-${j}`;
  const k2 = `${i},${j}`;
  const v = map[k1] ?? map[k2];
  return v === undefined || v === null ? fallback : Number(v);
}

type Props = {
  value: NetworkIn;
  onChange: (patch: Partial<NetworkIn>) => void;
};

type MatrixKey = "d_n3" | "d_n6";

function DelayMatrix({
  title,
  help,
  corner,
  matrixKey,
  value,
  onSet,
}: {
  title: string;
  help: string;
  corner: string;
  matrixKey: MatrixKey;
  value: NetworkIn;
  onSet: (key: MatrixKey, i: number, j: number, raw: string) => void;
}) {
  return (
    <>
      <h3 className="net-section-title">
        {title}{" "}
        <span className="help-q" title={help} role="img">
          ?
        </span>
      </h3>
      <div className="table-wrap">
        <table className="net-site-table">
          <thead>
            <tr>
              <th>{corner}</th>
              {SITES.map((s) => (
                <th key={s.id}>{s.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {SITES.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                {SITES.map((col) => (
                  <td key={col.id}>
                    <input
                      type="number"
                      step="1"
                      value={pairVal(value[matrixKey], row.id, col.id)}
                      onChange={(e) => onSet(matrixKey, row.id, col.id, e.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function NetworkSettingsForm({ value, onChange }: Props) {
  function setScalar(key: keyof NetworkIn, raw: string) {
    const n = Number(raw);
    onChange({ [key]: Number.isFinite(n) ? n : 0 } as Partial<NetworkIn>);
  }

  function setSite(
    key:
      | "c_n_capacity"
      | "r_n_capacity"
      | "c_a_capacity"
      | "r_a_capacity"
      | "g_a_capacity"
      | "p_c"
      | "p_r"
      | "p_g"
      | "d_f1",
    id: number,
    raw: string,
  ) {
    const n = Number(raw);
    const prev = { ...(value[key] || {}) } as Record<string, number>;
    prev[String(id)] = Number.isFinite(n) ? n : 0;
    onChange({ [key]: prev } as Partial<NetworkIn>);
  }

  function setPair(key: MatrixKey, i: number, j: number, raw: string) {
    const n = Number(raw);
    const prev = { ...(value[key] || {}) };
    prev[`${i}-${j}`] = Number.isFinite(n) ? n : 0;
    onChange({ [key]: prev });
  }

  return (
    <div className="net-form">
      <h3 className="net-section-title">Radio &amp; objective</h3>
      <div className="profile-grid">
        <FieldHelp
          label="b_total"
          help="Total radio PRBs available at the cell. All slices share this pool (sum of b_min ≤ b_total)."
        >
          <input
            type="number"
            value={value.b_total ?? ""}
            onChange={(e) => setScalar("b_total", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="w_c"
          help="Objective weight on money/resource cost. Higher → prefer cheaper sites (usually Central)."
        >
          <input
            type="number"
            step="0.1"
            value={value.w_c ?? ""}
            onChange={(e) => setScalar("w_c", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="w_p"
          help="Objective weight on SLA shortfalls. Keep large so the solver prefers meeting throughput/delay SLAs."
        >
          <input
            type="number"
            step="1"
            value={value.w_p ?? ""}
            onChange={(e) => setScalar("w_p", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="beta_demand"
          help="PM-only: weight on demand shortfall vs throughput shortfall. Unused by Planning Layer (PL)."
        >
          <input
            type="number"
            step="0.01"
            value={value.beta_demand ?? ""}
            onChange={(e) => setScalar("beta_demand", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="p_prb_ded"
          help="Cost weight for dedicated PRBs (hard isolation h_s=1)."
        >
          <input
            type="number"
            step="0.1"
            value={value.p_prb_ded ?? ""}
            onChange={(e) => setScalar("p_prb_ded", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="p_prb_prio"
          help="Cost weight for shared / priority PRBs."
        >
          <input
            type="number"
            step="0.1"
            value={value.p_prb_prio ?? ""}
            onChange={(e) => setScalar("p_prb_prio", e.target.value)}
          />
        </FieldHelp>
      </div>

      <h3 className="net-section-title">Throughput conversion</h3>
      <div className="profile-grid">
        <FieldHelp
          label="alpha_cu"
          help="CU CPU → Mbps conversion. Throughput ≤ alpha_cu × CU_CPU (bottleneck of all five)."
        >
          <input
            type="number"
            step="0.01"
            value={value.alpha_cu ?? ""}
            onChange={(e) => setScalar("alpha_cu", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="alpha_upf"
          help="UPF CPU → Mbps conversion."
        >
          <input
            type="number"
            step="0.01"
            value={value.alpha_upf ?? ""}
            onChange={(e) => setScalar("alpha_upf", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp label="gamma_c" help="APP CPU → Mbps conversion.">
          <input
            type="number"
            step="0.01"
            value={value.gamma_c ?? ""}
            onChange={(e) => setScalar("gamma_c", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp label="gamma_r" help="APP RAM → Mbps conversion.">
          <input
            type="number"
            step="0.001"
            value={value.gamma_r ?? ""}
            onChange={(e) => setScalar("gamma_r", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp label="gamma_g" help="APP GPU → Mbps conversion.">
          <input
            type="number"
            step="0.01"
            value={value.gamma_g ?? ""}
            onChange={(e) => setScalar("gamma_g", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="min_r_cu"
          help="Lower bound on CU RAM in the MILP (avoid zero-RAM solutions)."
        >
          <input
            type="number"
            step="1"
            value={value.min_r_cu ?? ""}
            onChange={(e) => setScalar("min_r_cu", e.target.value)}
          />
        </FieldHelp>
        <FieldHelp
          label="min_r_upf"
          help="Lower bound on UPF RAM in the MILP."
        >
          <input
            type="number"
            step="1"
            value={value.min_r_upf ?? ""}
            onChange={(e) => setScalar("min_r_upf", e.target.value)}
          />
        </FieldHelp>
      </div>

      <h3 className="net-section-title">
        Per-site capacity{" "}
        <span className="muted">(0=Edge, 1=Regional, 2=Central)</span>
      </h3>
      <div className="table-wrap">
        <table className="net-site-table">
          <thead>
            <tr>
              <th>
                Variable{" "}
                <span className="help-q" title="Capacities and unit costs per site. Cheaper toward Central." role="img">?</span>
              </th>
              {SITES.map((s) => (
                <th key={s.id}>{s.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(
              [
                ["c_n_capacity", "NF CPU capacity", "CPU units available for CU/UPF NFs"],
                ["r_n_capacity", "NF RAM capacity", "RAM units available for CU/UPF NFs"],
                ["c_a_capacity", "APP CPU capacity", "CPU units for application workloads"],
                ["r_a_capacity", "APP RAM capacity", "RAM units for application workloads"],
                ["g_a_capacity", "APP GPU capacity", "GPU units for application workloads"],
                ["p_c", "CPU unit cost", "Cost coefficient for CPU (cheaper toward Central)"],
                ["p_r", "RAM unit cost", "Cost coefficient for RAM"],
                ["p_g", "GPU unit cost", "Cost coefficient for GPU"],
              ] as const
            ).map(([key, label, help]) => (
              <tr key={key}>
                <td>
                  <span className="field-help-head inline">
                    <code>{key}</code>
                    <span className="help-q" title={`${label}: ${help}`} role="img">
                      ?
                    </span>
                  </span>
                  <div className="muted tiny">{label}</div>
                </td>
                {SITES.map((s) => (
                  <td key={s.id}>
                    <input
                      type="number"
                      step="any"
                      value={siteVal(value[key] as Record<string, number>, s.id)}
                      onChange={(e) => setSite(key, s.id, e.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="net-section-title">
        Link delays (ms){" "}
        <span className="muted">E2E ≈ RF + F1 + N3 + N6 (RTT / ping ms) · sites = Edge / Regional / Central</span>
      </h3>
      <div className="profile-grid">
        <FieldHelp
          label="d_rf"
          help="RF / air-interface delay UE → DU (ms). Fixed additive term in PL E2E delay (default 20)."
        >
          <input
            type="number"
            step="1"
            value={value.d_rf ?? ""}
            onChange={(e) => setScalar("d_rf", e.target.value)}
          />
        </FieldHelp>
      </div>

      <h3 className="net-section-title">
        F1: DU → CU-UP{" "}
        <span
          className="help-q"
          title="Fronthaul delay. DU is always at Edge — one row by CU-UP site."
          role="img"
        >
          ?
        </span>
      </h3>
      <div className="table-wrap">
        <table className="net-site-table">
          <thead>
            <tr>
              <th>DU \\ CU-UP</th>
              {SITES.map((s) => (
                <th key={s.id}>{s.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Edge</td>
              {SITES.map((col) => (
                <td key={col.id}>
                  <input
                    type="number"
                    step="1"
                    value={f1Val(value.d_f1, col.id)}
                    onChange={(e) => setSite("d_f1", col.id, e.target.value)}
                  />
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <DelayMatrix
        title="N3: CU-UP → UPF"
        help="Midhaul delay by (CU-UP site, UPF site). Part of PL E2E delay."
        corner="CU-UP \\ UPF"
        matrixKey="d_n3"
        value={value}
        onSet={setPair}
      />
      <DelayMatrix
        title="UPF → APP (N6)"
        help="Backhaul delay by (UPF site, APP site). Part of PL E2E delay."
        corner="UPF \\ APP"
        matrixKey="d_n6"
        value={value}
        onSet={setPair}
      />
    </div>
  );
}
