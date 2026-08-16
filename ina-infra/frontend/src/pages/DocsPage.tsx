import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

function DocTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | number)[][];
}) {
  return (
    <div className="table-wrap">
      <table className="slice-table docs-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Formula({ children }: { children: string }) {
  return <pre className="net-pre docs-formula">{children}</pre>;
}

export default function DocsPage() {
  return (
    <div className="page-layout docs-layout">
      <div className="page docs-page">
        <Card className="tier" glow>
          <SectionLabel kicker="reference">PL · PM · PS</SectionLabel>
          <p className="hint" style={{ marginTop: 0 }}>
            Three Gurobi MILP layers at different timescales. Solvers live in{" "}
            <code>algorithm/new_implementation/ina/</code>. Network substrate is
            editable on the Planning tab (Network settings).
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">Architecture</h2>
          <DocTable
            headers={["Layer", "Tab", "When", "What changes"]}
            rows={[
              ["PL", "Planning", "Manual Solve PL", "Placement + initial compute + b_min"],
              ["PM", "Medium", "Run once / loop", "Compute only (sites fixed)"],
              ["PS", "Short", "Run once / loop", "PRBs only"],
            ]}
          />
          <Formula>{`PL  →  deploy_map, resources, b_min
PS  →  b_min, b_ded, b_max, demand
PM  →  a_c_*, a_r_*, a_g_*  (reads demand from PS)`}</Formula>
          <p className="hint">
            PM and PS are <strong>independent</strong> background loops. Both need a
            successful PL result. PS writes <code>demand</code>; PM reads it on the
            next tick (fallback: <code>t_bar</code>).
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">Network substrate</h2>
          <p className="docs-lead">
            Sites <code>0 / 1 / 2</code> = Edge / Regional / Central. Each slice
            picks one site for CU-UP (<code>x</code>), UPF (<code>y</code>), APP (
            <code>z</code>).
          </p>
          <h3 className="net-section-title">Default capacities</h3>
          <DocTable
            headers={["Site", "NF CPU", "NF RAM", "APP CPU", "APP RAM", "APP GPU"]}
            rows={[
              ["Edge", 55, 64, 41, 2600, 22],
              ["Regional", 52, 64, 25, 1400, 12],
              ["Central", 61, 64, 90, 5625, 45],
            ]}
          />
          <h3 className="net-section-title">Unit costs (per site)</h3>
          <p className="hint">
            Edge is expensive; Central is cheap — placement trades cost vs delay.
          </p>
          <DocTable
            headers={["Site", "p_c (CPU)", "p_r (RAM)", "p_g (GPU)"]}
            rows={[
              ["Edge", 2.5, 0.5, 2.5],
              ["Regional", 0.25, 0.05, 0.5],
              ["Central", 0.001, 0.002, 0.1],
            ]}
          />
          <h3 className="net-section-title">Global weights &amp; radio</h3>
          <DocTable
            headers={["Code", "Default", "Layers", "Meaning"]}
            rows={[
              ["w_c", 1.0, "PL, PM, PS", "Weight on resource / PRB cost"],
              ["w_p", 1000, "PL, PM, PS", "Weight on SLA shortfall (keep large)"],
              ["beta_demand", 0.1, "PM", "Extra weight on demand vs t_bar shortfall"],
              ["p_prb_ded", 0.5, "PL, PS", "Dedicated PRB cost"],
              ["p_prb_prio", 0.1, "PL, PS", "Shared PRB cost (b_min − b_ded)"],
              ["b_total", 273, "PL, PS", "Total PRBs in cell"],
            ]}
          />
          <h3 className="net-section-title">Throughput coupling</h3>
          <p className="hint">
            Achievable throughput is the <strong>bottleneck</strong> of five conversions
            (<code>compute_cap</code> in API):
          </p>
          <Formula>{`T ≤ alpha_cu  × a_c_cu     (default 1.02 Mbps / CPU)
T ≤ alpha_upf × a_c_upf    (default 0.81)
T ≤ gamma_c   × a_c_app    (default 0.5)
T ≤ gamma_r   × a_r_app    (default 0.008)
T ≤ gamma_g   × a_g_app    (default 1.0)`}</Formula>
          <h3 className="net-section-title">Delay (PL only)</h3>
          <Formula>{`d_plan = d_rf + d_f1[CU] + d_n3[CU,UPF] + d_n6[UPF,APP]`}</Formula>
          <p className="hint">
            Default <code>d_rf = 20</code> ms (UE→DU). N6 cross-site penalties favour
            co-located UPF and APP.
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">Default slice SLAs</h2>
          <DocTable
            headers={["id", "Type", "t_bar", "d_bar", "h_s", "eta_t0", "Demo placement"]}
            rows={[
              ["1", "CCTV", "10 Mbps", "150 ms", "0", "2.0", "CU@Edge; UPF+APP@Regional"],
              ["2", "Physical AI", "20", "20", "1", "2.0", "All@Edge"],
              ["3", "OTT", "40", "50", "0", "2.5", "CU@Regional; UPF+APP@Central"],
              ["4", "IoT", "5", "150", "0", "1.5", "All@Central"],
            ]}
          />
          <DocTable
            headers={["Field", "Code", "PL", "PM", "PS"]}
            rows={[
              ["Throughput SLA", "t_bar", "✓", "✓", "✓"],
              ["Delay SLA", "d_bar", "✓", "—", "—"],
              ["Hard isolation", "h_s", "✓", "—", "✓"],
              ["Planning η", "eta_t0", "✓", "—", "—"],
              ["Runtime η", "eta", "—", "—", "✓"],
              ["Demand", "demand", "—", "reads", "writes"],
            ]}
          />
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">PL — PlanningLayer</h2>
          <p className="docs-lead">
            Joint placement + initial sizing. Non-convex (placement × resource cost).
          </p>
          <h3 className="net-section-title">Objective</h3>
          <Formula>{`minimize  w_c × (infra_cost + prb_cost)
        + w_p × Σ_s (xi_d + xi_prb + xi_com)

infra = Σ CPU/RAM/GPU at chosen site × p_c, p_r, p_g
prb   = Σ_s (p_prb_ded·b_ded + p_prb_prio·(b_min − b_ded))`}</Formula>
          <h3 className="net-section-title">Shortfalls (soft SLAs)</h3>
          <DocTable
            headers={["Variable", "Meaning"]}
            rows={[
              ["xi_d", "max(0, d_plan − d_bar) — delay"],
              ["xi_prb", "max(0, t_bar − eta_t0·b_min) — radio at plan time"],
              ["xi_com", "max(0, t_bar − t_plan) — compute"],
            ]}
          />
          <h3 className="net-section-title">Constraints (summary)</h3>
          <ul className="docs-ul">
            <li>Exactly one site per CU / UPF / APP</li>
            <li>Per-site NF and APP capacity limits</li>
            <li>Throughput coupling + minimum sizing for <code>t_bar</code></li>
            <li><code>b_ded ≤ b_min</code>; <code>b_ded ≥ b_min·h_s</code></li>
            <li><code>Σ b_min ≤ b_total</code></li>
            <li>Delay via linearized N3/N6 placement products</li>
          </ul>
          <p className="hint">
            API: <code>POST /api/v1/pl/solve</code>, <code>/pl/apply</code>
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">PM — MediumLayer</h2>
          <p className="docs-lead">
            Resize compute at fixed PL sites. Does not move NFs or allocate PRBs.
          </p>
          <h3 className="net-section-title">Objective</h3>
          <Formula>{`minimize  w_c × infra_cost
        + w_p × Σ_s (xi_sla + beta_demand × xi_dem)

xi_sla ≥ t_bar − t_curr
xi_dem ≥ demand − t_curr`}</Formula>
          <h3 className="net-section-title">Loop parameters (UI)</h3>
          <DocTable
            headers={["Label", "Code", "Default"]}
            rows={[
              ["Loop interval (s)", "interval_sec", 10],
              ["Demand scale", "demand_multiplier", 1.0],
              ["Max cycles", "max_cycles", 0],
            ]}
          />
          <p className="hint">
            API: <code>/pm/solve</code>, <code>/pm/loop/start</code>,{" "}
            <code>/pm/loop/stop</code>
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">PS — ShortLayer</h2>
          <p className="docs-lead">
            Reserve PRBs from runtime channel efficiency η (MCS → Mbps/PRB).
          </p>
          <h3 className="net-section-title">Objective</h3>
          <Formula>{`minimize  w_c × Σ_s (p_prb_ded·b_ded + p_prb_prio·(b_min − b_ded))
        + w_p × Σ_s xi_prb

xi_prb ≥ t_bar − eta·b_min`}</Formula>
          <h3 className="net-section-title">Post-solve sharing</h3>
          <Formula>{`extra = (b_total − Σ b_min) / n_slices
b_max = b_min + extra
radio_mbps = b_max × eta
demand   = radio_mbps  →  fed to PM`}</Formula>
          <h3 className="net-section-title">Loop parameters (UI)</h3>
          <DocTable
            headers={["Label", "Code", "Default"]}
            rows={[
              ["Loop interval (s)", "interval_sec", 1],
              ["MCS min / max", "mcs_min, mcs_max", "5 / 28"],
              ["Fixed MCS", "mcs_fixed", "random"],
              ["Max cycles", "max_cycles", 0],
              ["Random seed", "seed", 2025],
            ]}
          />
          <p className="hint">
            API: <code>/ps/solve</code>, <code>/ps/loop/start</code>,{" "}
            <code>/ps/loop/stop</code>
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">Workflow &amp; hooks</h2>
          <ol className="docs-ol">
            <li>
              <strong>Planning</strong> — slices + network → <strong>Solve PL</strong> →
              optional GitOps deploy.
            </li>
            <li>
              <strong>Short</strong> — PS simulates radio; updates PRBs and{" "}
              <code>demand</code>.
            </li>
            <li>
              <strong>Medium</strong> — PM resizes compute for current demand.
            </li>
          </ol>
          <p className="hint">
            <code>loop_application.py</code> receives PM/PS results (log-only default).
            Full markdown: <code>ina-infra/docs/pl-pm-ps.md</code>.
          </p>
        </Card>

        <Card className="tier">
          <h2 className="docs-h2">Applications · CCTV</h2>
          <p className="docs-lead">
            Slice 1 vision streaming. Server (YOLO + MediaMTX + dashboard) is GitOps on
            regional; UE clients are on-demand on edge. Lab notes:{" "}
            <code>docs/cctv.md</code>. Wall:{" "}
            <a href="http://10.1.137.121:8080/" target="_blank" rel="noreferrer">
              10.1.137.121:8080
            </a>
            . Swagger:{" "}
            <a href="http://10.1.137.121:8080/docs" target="_blank" rel="noreferrer">
              /docs
            </a>
            .
          </p>
        </Card>
      </div>
    </div>
  );
}
