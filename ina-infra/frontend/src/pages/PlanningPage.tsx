import { useEffect, useMemo, useState } from "react";
import {
  api,
  NetworkOut,
  PlApplyResponse,
  PlSolveResponse,
  SliceIn,
} from "../api/client";
import Topology from "../components/Topology";

const emptySlice = (id: number): SliceIn => ({
  id,
  t_bar: 40,
  d_bar: 100,
  h_s: 0,
  eta_t0: 2.5,
  slice_type: "custom",
});

export default function PlanningPage() {
  const [slices, setSlices] = useState<SliceIn[]>([]);
  const [network, setNetwork] = useState<NetworkOut | null>(null);
  const [showNet, setShowNet] = useState(false);
  const [result, setResult] = useState<PlSolveResponse | null>(null);
  const [applyLog, setApplyLog] = useState<PlApplyResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commitMsg, setCommitMsg] = useState("ina-pl: apply planning intent");

  useEffect(() => {
    (async () => {
      try {
        const [defs, net] = await Promise.all([api.defaults(), api.network()]);
        setSlices(defs);
        setNetwork(net);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const nextId = useMemo(
    () => (slices.length ? Math.max(...slices.map((s) => s.id)) + 1 : 1),
    [slices],
  );

  function updateSlice(idx: number, patch: Partial<SliceIn>) {
    setSlices((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
    setResult(null);
    setApplyLog(null);
  }

  function removeSlice(idx: number) {
    setSlices((prev) => prev.filter((_, i) => i !== idx));
    setResult(null);
  }

  async function onSolve() {
    setBusy(true);
    setError(null);
    setApplyLog(null);
    try {
      const res = await api.solve(slices);
      setResult(res);
      if (!res.ok) setError(res.message || "Solve failed");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  async function onApply(dryRun: boolean) {
    if (!result?.ok) return;
    if (
      !dryRun &&
      !window.confirm(
        "Push planning intent ConfigMaps to lab Gitea (central/regional/edge)?\n\n" +
          "This applies ina-planning namespace via Config Sync — it does not relocate OAI NFs yet.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.apply({
        result,
        slices,
        commit_message: commitMsg,
        dry_run: dryRun,
      });
      setApplyLog(res);
      if (!res.ok) setError(res.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-head">
          <h2>Slice SLAs</h2>
          <div className="actions">
            <button type="button" onClick={() => setSlices((p) => [...p, emptySlice(nextId)])}>
              Add slice
            </button>
            <button type="button" className="primary" disabled={busy || !slices.length} onClick={onSolve}>
              {busy ? "Working…" : "Solve PL"}
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Type</th>
                <th>t_bar (Mbps)</th>
                <th>d_bar (ms)</th>
                <th>h_s</th>
                <th>eta_t0</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {slices.map((s, i) => (
                <tr key={`${s.id}-${i}`}>
                  <td>
                    <input
                      type="number"
                      value={s.id}
                      onChange={(e) => updateSlice(i, { id: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      value={s.slice_type}
                      onChange={(e) => updateSlice(i, { slice_type: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="1"
                      value={s.t_bar}
                      onChange={(e) => updateSlice(i, { t_bar: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="1"
                      value={s.d_bar}
                      onChange={(e) => updateSlice(i, { d_bar: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <select
                      value={s.h_s}
                      onChange={(e) => updateSlice(i, { h_s: Number(e.target.value) })}
                    >
                      <option value={0}>0 shared</option>
                      <option value={1}>1 dedicated</option>
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      value={s.eta_t0}
                      onChange={(e) => updateSlice(i, { eta_t0: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <button type="button" className="danger" onClick={() => removeSlice(i)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Network settings</h2>
          <button type="button" onClick={() => setShowNet((v) => !v)}>
            {showNet ? "Hide" : "Show"}
          </button>
        </div>
        {showNet && network && (
          <pre className="net-pre">{network.settings_text}</pre>
        )}
      </section>

      {error && <div className="banner error">{error}</div>}

      {result?.ok && (
        <section className="panel">
          <div className="panel-head">
            <h2>PL result</h2>
            <span className="muted">{result.message}</span>
          </div>
          <Topology slices={result.slices} />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Slice</th>
                  <th>CU</th>
                  <th>UPF</th>
                  <th>APP</th>
                  <th>b_min</th>
                  <th>CU CPU</th>
                  <th>UPF CPU</th>
                  <th>APP CPU</th>
                </tr>
              </thead>
              <tbody>
                {result.slices.map((s) => (
                  <tr key={s.id}>
                    <td>
                      S{s.id} {s.slice_type && <span className="muted">({s.slice_type})</span>}
                    </td>
                    <td>{s.placement.cu}</td>
                    <td>{s.placement.upf}</td>
                    <td>{s.placement.app}</td>
                    <td>{s.resources.b_min ?? "—"}</td>
                    <td>{s.resources.a_c_cu.toFixed(2)}</td>
                    <td>{s.resources.a_c_upf.toFixed(2)}</td>
                    <td>{s.resources.a_c_app.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="apply-box">
            <label>
              Commit message
              <input
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div className="actions" style={{ marginTop: 12 }}>
              <button type="button" disabled={busy} onClick={() => onApply(true)}>
                Dry-run write
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => onApply(false)}
              >
                Push to Gitea
              </button>
            </div>
            <p className="hint">
              Writes <code>namespaces/ina-planning/</code> ConfigMaps into GitOps repos, then
              pushes to lab Gitea for Config Sync. Does not relocate OAI NFs yet.
            </p>
          </div>
        </section>
      )}

      {applyLog && (
        <section className="panel">
          <h2>Apply log</h2>
          <p className={applyLog.ok ? "ok" : "error"}>
            {applyLog.dry_run ? "[dry-run] " : ""}
            {applyLog.message}
          </p>
          {applyLog.written_files.length > 0 && (
            <ul className="file-list">
              {applyLog.written_files.map((f) => (
                <li key={f}>
                  <code>{f}</code>
                </li>
              ))}
            </ul>
          )}
          {(applyLog.push_stdout || applyLog.push_stderr) && (
            <pre className="net-pre">
              {applyLog.push_stdout}
              {applyLog.push_stderr}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
