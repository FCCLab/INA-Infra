import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  DEFAULT_PROFILE,
  PmLoopParams,
  PmLoopStatusOut,
  PmSolveResponse,
  Profile,
  ProfileClusterStatusOut,
  ProfileRecord,
  StreamHandlers,
} from "../api/client";
import StatusRail from "../components/StatusRail";
import FieldHelp from "../components/FieldHelp";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

const NS_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

const DEFAULT_PARAMS: PmLoopParams = {
  interval_sec: 10,
  demand_multiplier: 1,
  max_cycles: 0,
};

export default function MediumPage() {
  const [rec, setRec] = useState<ProfileRecord | null>(null);
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [params, setParams] = useState<PmLoopParams>(DEFAULT_PARAMS);
  const [busy, setBusy] = useState(false);
  const [loopRunning, setLoopRunning] = useState(false);
  const [lastResult, setLastResult] = useState<PmSolveResponse | null>(null);
  const [loopStatus, setLoopStatus] = useState<PmLoopStatusOut | null>(null);
  const [consoleText, setConsoleText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cluster, setCluster] = useState<ProfileClusterStatusOut | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [clusterBusy, setClusterBusy] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);

  const profileOk = NS_RE.test(profile.name);
  const plReady = Boolean(rec?.pl_result?.ok);

  const appendConsole = useCallback((line: string) => {
    setConsoleText((prev) => (prev ? `${prev}\n${line}` : line));
  }, []);

  const streamHandlers = useCallback((): StreamHandlers => ({
    onLog: (_stream, line) => appendConsole(line),
    onStatus: (message) => {
      if (message) appendConsole(`# ${message}`);
    },
    onError: (message) => appendConsole(`! ${message}`),
  }), [appendConsole]);

  const refreshStatus = useCallback(async () => {
    if (!profileOk) return;
    try {
      const st = await api.pmLoopStatus(profile.name);
      setLoopStatus(st);
      setLoopRunning(st.running);
      if (st.last_result) setLastResult(st.last_result);
    } catch {
      /* ignore */
    }
  }, [profile.name, profileOk]);

  const refreshCluster = useCallback(async () => {
    if (!profileOk) return;
    setClusterBusy(true);
    try {
      setCluster(await api.clusterStatus(profile.name));
      setClusterError(null);
    } catch (e) {
      setClusterError(e instanceof Error ? e.message : String(e));
    } finally {
      setClusterBusy(false);
    }
  }, [profile.name, profileOk]);

  useEffect(() => {
    (async () => {
      try {
        const list = await api.listProfiles();
        const first = list.profiles[0];
        if (first) {
          setRec(first);
          setProfile(first.profile);
        } else {
          const defs = await api.profileDefaults();
          setRec({ ...defs, updated_at: "" } as ProfileRecord);
          setProfile(defs.profile);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  useEffect(() => {
    if (!profileOk) return;
    void refreshStatus();
    void refreshCluster();
    const id = window.setInterval(() => void refreshStatus(), 5000);
    return () => window.clearInterval(id);
  }, [profileOk, refreshStatus, refreshCluster]);

  useEffect(() => {
    if (!profileOk) return;
    void refreshCluster();
    const id = window.setInterval(() => void refreshCluster(), 10000);
    return () => window.clearInterval(id);
  }, [profileOk, refreshCluster]);

  async function onRunOnce() {
    if (!plReady) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.pmSolve(profile, params);
      setLastResult(res);
      appendConsole(`# ${res.message}`);
      for (const s of res.slices) {
        appendConsole(
          `S${s.id}: demand=${s.demand.toFixed(2)} cap=${s.compute_cap.toFixed(2)} ` +
            `CU=${s.resources.a_c_cu.toFixed(2)} UPF=${s.resources.a_c_upf.toFixed(2)}`,
        );
      }
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRunLoop() {
    if (!plReady || loopRunning) return;
    setBusy(true);
    setLoopRunning(true);
    setError(null);
    setConsoleText("");
    appendConsole("# PM loop starting…");
    streamAbortRef.current = new AbortController();
    try {
      const st = await api.pmLoopStart(profile, params, streamHandlers(), {
        signal: streamAbortRef.current.signal,
      });
      setLoopStatus(st);
      if (st.last_result) setLastResult(st.last_result);
      appendConsole("# PM loop finished");
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
      setLoopRunning(false);
      streamAbortRef.current = null;
      await refreshStatus();
    }
  }

  async function onStop() {
    streamAbortRef.current?.abort();
    try {
      const res = await api.pmLoopStop(profile);
      appendConsole(`# ${res.message}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoopRunning(false);
      await refreshStatus();
    }
  }

  function setNum(key: keyof PmLoopParams, raw: string) {
    const v = raw === "" ? 0 : Number(raw);
    setParams((p) => ({ ...p, [key]: v }));
  }

  return (
    <div className="page-layout">
      <div className="page">
        <Card className="tier" glow>
          <SectionLabel kicker="medium layer">Medium (PM)</SectionLabel>
          <p className="hint" style={{ marginTop: 0 }}>
            Re-allocate compute with placement fixed from the last PL solve.
            PS loop updates demand between PM ticks.
          </p>

          {!plReady && (
            <p className="hint error">
              Solve PL on the Planning tab first — profile{" "}
              <code>{profile.name}</code> has no PL result.
            </p>
          )}
          {error && <p className="hint error">{error}</p>}

          <div className="grid-3 profile-grid" style={{ marginTop: 12 }}>
            <FieldHelp
              label="Loop interval (s)"
              help="interval_sec — Seconds between PM solves when Run loop is active."
            >
              <input
                type="number"
                step="0.1"
                min="0.1"
                value={params.interval_sec}
                onChange={(e) => setNum("interval_sec", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="Demand scale"
              help="demand_multiplier — Scale recent throughput demand from PS (fallback: slice t_bar) before each PM solve."
            >
              <input
                type="number"
                step="0.1"
                min="0"
                value={params.demand_multiplier}
                onChange={(e) => setNum("demand_multiplier", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="Max cycles"
              help="max_cycles — Stop the loop after this many PM solves. Use 0 to run until Stop."
            >
              <input
                type="number"
                step="1"
                min="0"
                value={params.max_cycles}
                onChange={(e) => setNum("max_cycles", e.target.value)}
              />
            </FieldHelp>
          </div>

          <div className="actions" style={{ marginTop: 16 }}>
            <button
              type="button"
              className="primary"
              disabled={busy || !plReady}
              onClick={() => void onRunOnce()}
            >
              {busy && !loopRunning ? "Working…" : "Run once"}
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !plReady || loopRunning}
              onClick={() => void onRunLoop()}
            >
              {loopRunning ? "Running…" : "Run loop"}
            </button>
            <button
              type="button"
              disabled={!loopRunning && !busy}
              onClick={() => void onStop()}
            >
              Stop
            </button>
          </div>

          {loopStatus && (
            <p className="hint" style={{ marginTop: 8 }}>
              PM cycle {loopStatus.cycle}
              {loopStatus.running ? " · loop active" : ""}
              {Object.keys(loopStatus.demand).length > 0 &&
                ` · demand from PS: ${Object.entries(loopStatus.demand)
                  .map(([id, d]) => `S${id}=${d.toFixed(1)}`)
                  .join(", ")}`}
            </p>
          )}
        </Card>

        {consoleText && (
          <Card className="console-panel" style={{ marginTop: 16 }}>
            <div className="panel-head">Console</div>
            <pre className="net-pre console-pre">{consoleText}</pre>
          </Card>
        )}

        {lastResult?.ok && (
          <Card style={{ marginTop: 16 }}>
            <SectionLabel kicker="result">Last PM result</SectionLabel>
            <table className="slice-table">
              <thead>
                <tr>
                  <th>Slice</th>
                  <th>
                    Demand{" "}
                    <span className="help-q" title="demand — Target throughput (Mbps) fed into MediumLayer for this PM step." role="img">?</span>
                  </th>
                  <th>
                    Compute cap{" "}
                    <span className="help-q" title="compute_cap — Achievable throughput (Mbps) from allocated CU/UPF/APP resources." role="img">?</span>
                  </th>
                  <th>
                    CU CPU{" "}
                    <span className="help-q" title="a_c_cu — CPU allocated to CU-UP." role="img">?</span>
                  </th>
                  <th>
                    UPF CPU{" "}
                    <span className="help-q" title="a_c_upf — CPU allocated to UPF." role="img">?</span>
                  </th>
                  <th>
                    APP CPU{" "}
                    <span className="help-q" title="a_c_app — CPU allocated to the slice application." role="img">?</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {lastResult.slices.map((s) => (
                  <tr key={s.id}>
                    <td>S{s.id}</td>
                    <td>{s.demand.toFixed(2)}</td>
                    <td>{s.compute_cap.toFixed(2)}</td>
                    <td>{s.resources.a_c_cu.toFixed(2)}</td>
                    <td>{s.resources.a_c_upf.toFixed(2)}</td>
                    <td>{s.resources.a_c_app.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>

      <StatusRail
        profileName={profile.name}
        profileOk={profileOk}
        isExisting={Boolean(rec?.updated_at)}
        dirtyName={false}
        savedAt={rec?.updated_at || ""}
        networkCollapsed={true}
        sliceCount={rec?.slices?.length || 0}
        maxSlices={rec?.profile.max_slices || 0}
        plSolved={plReady}
        plMessage={rec?.pl_result?.ok ? rec.pl_result.message : null}
        deployed={Boolean(rec?.deployed)}
        deployedAt={rec?.deployed_at || ""}
        cluster={cluster}
        clusterError={clusterError}
        refreshing={clusterBusy}
        onRefresh={() => void refreshCluster()}
      />
    </div>
  );
}
