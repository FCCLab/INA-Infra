import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  DEFAULT_PROFILE,
  Profile,
  ProfileClusterStatusOut,
  ProfileRecord,
  PsLoopParams,
  PsLoopStatusOut,
  PsSolveResponse,
  StreamHandlers,
} from "../api/client";
import StatusRail from "../components/StatusRail";
import FieldHelp from "../components/FieldHelp";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

const NS_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

const DEFAULT_PARAMS: PsLoopParams = {
  interval_sec: 1,
  mcs_min: 5,
  mcs_max: 28,
  mcs_fixed: null,
  max_cycles: 0,
  seed: 2025,
};

export default function ShortPage() {
  const [rec, setRec] = useState<ProfileRecord | null>(null);
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [params, setParams] = useState<PsLoopParams>(DEFAULT_PARAMS);
  const [busy, setBusy] = useState(false);
  const [loopRunning, setLoopRunning] = useState(false);
  const [lastResult, setLastResult] = useState<PsSolveResponse | null>(null);
  const [loopStatus, setLoopStatus] = useState<PsLoopStatusOut | null>(null);
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
      const st = await api.psLoopStatus(profile.name);
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
      const res = await api.psSolve(profile, params);
      setLastResult(res);
      appendConsole(`# ${res.message}`);
      for (const s of res.slices) {
        appendConsole(
          `S${s.id}: eta=${s.eta.toFixed(4)} b_min=${s.b_min} b_ded=${s.b_ded} ` +
            `b_max=${s.b_max.toFixed(1)} radio≈${s.radio_mbps.toFixed(2)} Mbps`,
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
    appendConsole("# PS loop starting…");
    streamAbortRef.current = new AbortController();
    try {
      const st = await api.psLoopStart(profile, params, streamHandlers(), {
        signal: streamAbortRef.current.signal,
      });
      setLoopStatus(st);
      if (st.last_result) setLastResult(st.last_result);
      appendConsole("# PS loop finished");
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
      const res = await api.psLoopStop(profile);
      appendConsole(`# ${res.message}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoopRunning(false);
      await refreshStatus();
    }
  }

  function setNum(key: keyof PsLoopParams, raw: string) {
    if (key === "mcs_fixed") {
      setParams((p) => ({
        ...p,
        mcs_fixed: raw === "" ? null : Number(raw),
      }));
      return;
    }
    const v = raw === "" ? 0 : Number(raw);
    setParams((p) => ({ ...p, [key]: v }));
  }

  return (
    <div className="page-layout">
      <div className="page">
        <Card className="tier" glow>
          <SectionLabel kicker="short layer">Short (PS)</SectionLabel>
          <p className="hint" style={{ marginTop: 0 }}>
            Allocate PRBs from current channel efficiency (η). Updates shared
            demand for the PM loop.
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
              help="interval_sec — Seconds between PS solves when Run loop is active."
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
              label="MCS min"
              help="mcs_min — Lower bound for random Modulation and Coding Scheme when sampling channel efficiency η."
            >
              <input
                type="number"
                min="1"
                max="28"
                value={params.mcs_min}
                onChange={(e) => setNum("mcs_min", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="MCS max"
              help="mcs_max — Upper bound for random MCS; PS draws uniformly in [mcs_min, mcs_max]."
            >
              <input
                type="number"
                min="1"
                max="28"
                value={params.mcs_max}
                onChange={(e) => setNum("mcs_max", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="Fixed MCS"
              help="mcs_fixed — If set, always use this MCS instead of random sampling. Leave empty for variable radio."
            >
              <input
                type="number"
                min="1"
                max="28"
                placeholder="random"
                value={params.mcs_fixed ?? ""}
                onChange={(e) => setNum("mcs_fixed", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="Max cycles"
              help="max_cycles — Stop the loop after this many PS solves. Use 0 to run until Stop."
            >
              <input
                type="number"
                min="0"
                value={params.max_cycles}
                onChange={(e) => setNum("max_cycles", e.target.value)}
              />
            </FieldHelp>
            <FieldHelp
              label="Random seed"
              help="seed — Seed for MCS random draws so PS loop runs are reproducible."
            >
              <input
                type="number"
                value={params.seed}
                onChange={(e) => setNum("seed", e.target.value)}
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
              PS cycle {loopStatus.cycle}
              {loopStatus.running ? " · loop active" : ""}
              {Object.keys(loopStatus.demand).length > 0 &&
                ` · demand→PM: ${Object.entries(loopStatus.demand)
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
            <SectionLabel kicker="result">Last PS result</SectionLabel>
            <p className="hint">
              Shared extra PRBs: {lastResult.extra.toFixed(1)} / slice{" "}
              <span className="help-q" title="extra — Leftover PRBs shared equally after PS reservation (b_max = b_min + extra)." role="img">?</span>
            </p>
            <table className="slice-table">
              <thead>
                <tr>
                  <th>Slice</th>
                  <th>
                    η{" "}
                    <span className="help-q" title="eta — Current channel efficiency (Mbps per PRB) from MCS." role="img">?</span>
                  </th>
                  <th>
                    Reserved PRBs{" "}
                    <span className="help-q" title="b_min — Reserved (guaranteed) PRBs for the slice." role="img">?</span>
                  </th>
                  <th>
                    Dedicated PRBs{" "}
                    <span className="help-q" title="b_ded — Dedicated PRBs (≤ b_min; equals b_min when h_s=1)." role="img">?</span>
                  </th>
                  <th>
                    PRB ceiling{" "}
                    <span className="help-q" title="b_max — Usable PRB ceiling this step (b_min + extra)." role="img">?</span>
                  </th>
                  <th>
                    Radio Mbps{" "}
                    <span className="help-q" title="radio_mbps — Estimated radio throughput: b_max × eta." role="img">?</span>
                  </th>
                  <th>
                    Demand→PM{" "}
                    <span className="help-q" title="demand — Radio potential written to shared loop state for the PM layer." role="img">?</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {lastResult.slices.map((s) => (
                  <tr key={s.id}>
                    <td>S{s.id}</td>
                    <td>{s.eta.toFixed(4)}</td>
                    <td>{s.b_min}</td>
                    <td>{s.b_ded}</td>
                    <td>{s.b_max.toFixed(1)}</td>
                    <td>{s.radio_mbps.toFixed(2)}</td>
                    <td>{s.demand.toFixed(2)}</td>
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
