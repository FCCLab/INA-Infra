import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ClusterDeployStatus,
  EDGE_RF_NODES,
  EdgeNodeOut,
  ProfileClusterStatusOut,
  type StreamHandlers,
} from "../api/client";
import FieldHelp from "../components/FieldHelp";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";
import { useDialog } from "../components/ui/Dialog";

const BENCH_NS = "oai-benchmark";
const BENCH_CLUSTERS = ["central", "edge"] as const;
const DEFAULT_RAN_NODE = "usrp";

type RunAction = "deploy" | "undeploy" | null;
type RunStep = "idle" | "render" | "push" | "cleanup" | "done";

function BtnProgress({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <span className="btn-progress" aria-hidden>
      <span className="btn-progress-spin" />
    </span>
  );
}

function toneClass(tone?: "ok" | "warn" | "err" | "muted") {
  if (tone === "ok") return "status-pill ok";
  if (tone === "warn") return "status-pill warn";
  if (tone === "err") return "status-pill err";
  return "status-pill muted";
}

function overallTone(
  overall: string | undefined,
): "ok" | "warn" | "err" | "muted" {
  if (overall === "ready" || overall === "synced") return "ok";
  if (overall === "degraded" || overall === "error" || overall === "missing")
    return "err";
  if (overall === "partial" || overall === "empty" || overall === "syncing")
    return "warn";
  return "muted";
}

function stepLabel(action: RunAction, step: RunStep): string {
  if (!action || step === "idle") return "Idle — Deploy or Undeploy to stream logs";
  if (step === "done") return action === "deploy" ? "Deploy finished" : "Undeploy finished";
  if (step === "render") return "Rendering manifests…";
  if (step === "push") return "Pushing to Gitea…";
  if (step === "cleanup") return "Cleaning cluster namespaces…";
  return "Running…";
}

function ClusterBlock({ st }: { st: ClusterDeployStatus }) {
  const title = st.cluster.charAt(0).toUpperCase() + st.cluster.slice(1);
  const cs = st.config_sync;
  return (
    <div className="status-cluster">
      <div className="status-cluster-head">
        <strong>{title}</strong>
        <span className={toneClass(overallTone(st.overall))}>{st.summary}</span>
      </div>
      <ul className="status-rows">
        <li>
          <span className="status-label">Namespace</span>
          <span className={toneClass(st.namespace_exists ? "ok" : "err")}>
            {st.namespace_exists
              ? st.namespace_phase || "Active"
              : "Missing"}
          </span>
        </li>
        <li>
          <span className="status-label">Config Sync</span>
          <span className={toneClass(overallTone(cs?.overall))}>
            {cs?.summary || cs?.overall || "—"}
          </span>
        </li>
      </ul>
      {st.error && <p className="hint error">{st.error}</p>}
      {st.deployments.length === 0 ? (
        <p className="hint">
          {st.namespace_exists ? "No Deployments" : "—"}
        </p>
      ) : (
        <div className="table-wrap status-deploy-table">
          <table>
            <thead>
              <tr>
                <th>Deployment</th>
                <th>Ready</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {st.deployments.map((d) => (
                <tr key={`${st.cluster}-${d.name}`}>
                  <td>
                    <code>{d.name}</code>
                  </td>
                  <td>{d.ready_text}</td>
                  <td>
                    <span
                      className={toneClass(
                        d.ok ? "ok" : d.exists ? "warn" : "err",
                      )}
                    >
                      {d.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function BenchmarkPage() {
  const dialog = useDialog();
  const [running, setRunning] = useState<RunAction>(null);
  const [runStep, setRunStep] = useState<RunStep>("idle");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [ranNode, setRanNode] = useState(DEFAULT_RAN_NODE);
  const [edgeNodes, setEdgeNodes] = useState<EdgeNodeOut[]>([]);
  const [edgeNodesError, setEdgeNodesError] = useState<string | null>(null);

  const [consoleTitle, setConsoleTitle] = useState("Console");
  const [consoleText, setConsoleText] = useState("");
  const [consoleMessage, setConsoleMessage] = useState("");
  const [consoleOk, setConsoleOk] = useState<boolean | null>(null);
  const [consoleFiles, setConsoleFiles] = useState<string[]>([]);
  const [consoleFilesLabel, setConsoleFilesLabel] = useState("Files");
  const [consoleAutoScroll, setConsoleAutoScroll] = useState(true);
  const consoleRef = useRef<HTMLPreElement | null>(null);
  const consolePanelRef = useRef<HTMLDivElement | null>(null);

  const [cluster, setCluster] = useState<ProfileClusterStatusOut | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [clusterBusy, setClusterBusy] = useState(false);

  const busy = running !== null;

  const appendConsole = useCallback((line: string) => {
    setConsoleText((prev) => (prev ? `${prev}\n${line}` : line));
  }, []);

  const resetConsole = (title: string) => {
    setConsoleTitle(title);
    setConsoleText("");
    setConsoleMessage("");
    setConsoleOk(null);
    setConsoleFiles([]);
    setConsoleAutoScroll(true);
    // Bring the progress console into view when a run starts.
    requestAnimationFrame(() => {
      consolePanelRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    });
  };

  const streamHandlers = (action: RunAction): StreamHandlers => ({
    onLog: (stream, line) => {
      appendConsole(stream === "stderr" ? `[err] ${line}` : line);
    },
    onStatus: (message) => {
      if (!message) return;
      appendConsole(`# ${message}`);
      const m = message.toLowerCase();
      if (
        m.includes("rendering") ||
        m.includes("wrote ") ||
        m.includes("working tree") ||
        m.startsWith("pending ") ||
        m.startsWith("removed ")
      ) {
        setRunStep(action === "undeploy" ? "cleanup" : "render");
        if (
          m.includes("clearing") ||
          m.includes("undeploying") ||
          m.startsWith("removed ")
        ) {
          setRunStep("cleanup");
        }
        if (m.includes("rendering") || m.includes("wrote ")) {
          setRunStep("render");
        }
      }
      if (m.startsWith("$ ") || m.includes("pushing")) {
        setRunStep("push");
      }
      if (
        m.includes("force-clean") ||
        m.includes("forcing cluster") ||
        m.includes("cleaning")
      ) {
        setRunStep("cleanup");
      }
    },
    onError: (message) => {
      if (message) appendConsole(`! ${message}`);
    },
  });

  useEffect(() => {
    if (!consoleAutoScroll || !consoleRef.current) return;
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [consoleText, consoleAutoScroll]);

  const onConsoleScroll = () => {
    const el = consoleRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setConsoleAutoScroll(distance < 40);
  };

  const refreshCluster = useCallback(async () => {
    setClusterBusy(true);
    try {
      const st = await api.clusterStatus(BENCH_NS);
      setCluster(st);
      setClusterError(null);
    } catch (e) {
      setClusterError(e instanceof Error ? e.message : String(e));
    } finally {
      setClusterBusy(false);
    }
  }, []);

  const refreshEdgeNodes = useCallback(async () => {
    try {
      const out = await api.edgeNodes();
      setEdgeNodes(out.nodes || []);
      setEdgeNodesError(out.error || null);
    } catch (e) {
      setEdgeNodesError(e instanceof Error ? e.message : String(e));
      setEdgeNodes([]);
    }
  }, []);

  const edgeRfOptions = useMemo(() => {
    const names = edgeNodes.map((n) => n.name);
    const merged = names.length > 0 ? [...names] : [...EDGE_RF_NODES];
    if (ranNode && !merged.includes(ranNode)) merged.push(ranNode);
    // Prefer usrp at the top when present.
    merged.sort((a, b) => {
      if (a === DEFAULT_RAN_NODE) return -1;
      if (b === DEFAULT_RAN_NODE) return 1;
      return a.localeCompare(b);
    });
    return merged;
  }, [edgeNodes, ranNode]);

  const edgeOptionLabel = useCallback(
    (name: string) => {
      const n = edgeNodes.find((x) => x.name === name);
      if (!n) return name;
      const bits = [
        name,
        n.ready ? "Ready" : "NotReady",
        n.multus_master || null,
      ].filter(Boolean);
      return bits.join(" · ");
    },
    [edgeNodes],
  );

  useEffect(() => {
    void refreshCluster();
    void refreshEdgeNodes();
    const id = window.setInterval(() => void refreshCluster(), 10000);
    return () => window.clearInterval(id);
  }, [refreshCluster, refreshEdgeNodes]);

  async function onDeploy() {
    if (
      !(await dialog.confirm({
        title: `Deploy ${BENCH_NS}?`,
        message:
          "Renders the dedicated benchmark stack (5GC CP on central, " +
          `RAN+UPF on edge; DU + UE on node “${ranNode}”) and pushes to Gitea.`,
        confirmLabel: "Deploy",
      }))
    ) {
      return;
    }
    setRunning("deploy");
    setRunStep("render");
    setError(null);
    setStatus(null);
    resetConsole("Deploy oai-benchmark");
    appendConsole(`# Starting deploy… DU=${ranNode} UE=${ranNode}`);
    try {
      const res = await api.benchmarkDeployStream(
        {
          commit_message: "ina-benchmark: deploy oai-benchmark",
          dry_run: false,
          clusters: [...BENCH_CLUSTERS],
          du_node: ranNode,
          ue_node: ranNode,
        },
        streamHandlers("deploy"),
      );
      setConsoleOk(res.ok);
      setConsoleMessage(res.message || "");
      setConsoleFiles(res.written_files || []);
      setConsoleFilesLabel("Written files");
      setRunStep("done");
      if (res.ok) {
        setStatus(res.message || "Deploy complete");
        appendConsole("# Deploy complete");
        void refreshCluster();
      } else {
        setError(res.message);
        appendConsole(`! ${res.message || "Deploy failed"}`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setConsoleOk(false);
      setConsoleMessage(msg);
      setError(msg);
      setRunStep("done");
      appendConsole(`! ${msg}`);
    } finally {
      setRunning(null);
    }
  }

  async function onUndeploy() {
    if (
      !(await dialog.confirm({
        title: `Undeploy ${BENCH_NS}?`,
        message:
          `Removes namespaces/${BENCH_NS}/ from GitOps repos, pushes to Gitea, ` +
          "and force-cleans the namespace on central and edge.",
        confirmLabel: "Undeploy",
        danger: true,
      }))
    ) {
      return;
    }
    setRunning("undeploy");
    setRunStep("cleanup");
    setError(null);
    setStatus(null);
    resetConsole("Undeploy oai-benchmark");
    appendConsole("# Starting undeploy…");
    try {
      const res = await api.benchmarkUndeployStream(
        {
          commit_message: "ina-benchmark: undeploy oai-benchmark",
          dry_run: false,
          clusters: [...BENCH_CLUSTERS],
        },
        streamHandlers("undeploy"),
      );
      setConsoleOk(res.ok);
      setConsoleMessage(res.message || "");
      setConsoleFiles(res.removed_paths || []);
      setConsoleFilesLabel("Removed paths");
      setRunStep("done");
      if (res.ok) {
        setStatus(res.message || "Undeploy complete");
        appendConsole("# Undeploy complete");
        void refreshCluster();
      } else {
        setError(res.message);
        appendConsole(`! ${res.message || "Undeploy failed"}`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setConsoleOk(false);
      setConsoleMessage(msg);
      setError(msg);
      setRunStep("done");
      appendConsole(`! ${msg}`);
    } finally {
      setRunning(null);
    }
  }

  const benchClusters = (cluster?.clusters || []).filter((c) =>
    (BENCH_CLUSTERS as readonly string[]).includes(c.cluster),
  );

  return (
    <div className="page-layout">
      <div className="page">
        <Card className="tier" glow>
          <SectionLabel kicker="gitops">Benchmark</SectionLabel>
          <p className="hint" style={{ marginTop: 0 }}>
            Dedicated non-slice OAI stack in <code>{BENCH_NS}</code>: 5GC CP on{" "}
            <strong>central</strong>, RAN (CU-CP/CU-UP/DU via{" "}
            <code>oai-ran-controller</code>) + UPF + nrUE on{" "}
            <strong>edge</strong>. DU and UE are pinned to the selected RAN
            node (default <code>usrp</code>). Live compute control stays on the
            Operators tab.
          </p>
          <div className="profile-grid" style={{ marginTop: 12 }}>
            <FieldHelp
              className="field-help-wide"
              label="RAN node (DU + UE)"
              help="Edge worker for OAI DU + nrUE (rfsim). usrp → Multus enp4s0f0; VMs → enp7s0."
            >
              <select
                className="ran-node-select"
                value={ranNode}
                disabled={busy}
                onChange={(e) => setRanNode(e.target.value)}
                onFocus={() => {
                  void refreshEdgeNodes();
                }}
              >
                {edgeRfOptions.map((n) => (
                  <option key={n} value={n}>
                    {edgeOptionLabel(n)}
                  </option>
                ))}
              </select>
            </FieldHelp>
          </div>
          {edgeNodesError && (
            <p className="hint error">Edge nodes: {edgeNodesError}</p>
          )}
          {error && <p className="hint error">{error}</p>}
          {status && !error && <p className="hint ok-inline">{status}</p>}
          <div className="actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className={"primary" + (running === "deploy" ? " is-running" : "")}
              disabled={busy}
              onClick={() => void onDeploy()}
              title="Render operator+NFDeployments GitOps + push; pin RAN after create-once"
            >
              <BtnProgress active={running === "deploy"} />
              Deploy
            </button>
            <button
              type="button"
              className={
                "danger" + (running === "undeploy" ? " is-running" : "")
              }
              disabled={busy}
              onClick={() => void onUndeploy()}
              title="Clear GitOps + push + force-delete namespaces"
            >
              <BtnProgress active={running === "undeploy"} />
              Undeploy
            </button>
          </div>
        </Card>

        <div ref={consolePanelRef}>
          <Card className="tier console-panel">
            <div className="panel-head">
              <SectionLabel kicker="stream">{consoleTitle}</SectionLabel>
              <div className="actions">
                {!consoleAutoScroll && consoleText.length > 0 && (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => setConsoleAutoScroll(true)}
                    title="Jump to latest output and resume auto-scroll"
                  >
                    To bottom
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setConsoleText("");
                    setConsoleMessage("");
                    setConsoleOk(null);
                    setConsoleFiles([]);
                    setConsoleTitle("Console");
                    setRunStep("idle");
                    setConsoleAutoScroll(true);
                  }}
                >
                  Clear
                </button>
              </div>
            </div>

            <p className="hint" style={{ marginTop: 0, marginBottom: 8 }}>
              Progress:{" "}
              <span
                className={toneClass(
                  busy
                    ? "warn"
                    : consoleOk === true
                      ? "ok"
                      : consoleOk === false
                        ? "err"
                        : "muted",
                )}
              >
                {stepLabel(running, busy ? runStep : consoleOk != null ? "done" : runStep)}
              </span>
            </p>

            {consoleMessage && (
              <pre
                className={
                  "console-summary" +
                  (consoleOk == null ? "" : consoleOk ? " ok" : " error")
                }
              >
                {consoleMessage}
              </pre>
            )}
            <pre
              className="net-pre console-pre benchmark-console-pre"
              ref={consoleRef}
              onScroll={onConsoleScroll}
            >
              {consoleText ||
                (busy
                  ? "Waiting for output…"
                  : "Deploy or Undeploy to stream render / push / cleanup logs here.")}
            </pre>
            {consoleFiles.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary className="hint">
                  {consoleFilesLabel} ({consoleFiles.length})
                </summary>
                <ul className="hint" style={{ marginTop: 6 }}>
                  {consoleFiles.map((f) => (
                    <li key={f}>
                      <code>{f}</code>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Card>
        </div>
      </div>

      <aside className="status-rail">
        <Card className="tier">
          <div className="panel-head">
            <SectionLabel kicker="live">Status</SectionLabel>
            <div className="actions">
              <button
                type="button"
                disabled={clusterBusy}
                onClick={() => void refreshCluster()}
              >
                Refresh
              </button>
            </div>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            Namespace <code>{BENCH_NS}</code> · central + edge
          </p>
          {clusterError && <p className="hint error">{clusterError}</p>}
          {cluster && (
            <p className="hint">
              Overall:{" "}
              <span className={toneClass(overallTone(cluster.overall))}>
                {cluster.summary}
              </span>
            </p>
          )}
          {benchClusters.map((st) => (
            <ClusterBlock key={st.cluster} st={st} />
          ))}
          {!cluster && !clusterError && (
            <p className="hint">Loading cluster status…</p>
          )}
        </Card>
      </aside>
    </div>
  );
}
