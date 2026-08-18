import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  DEFAULT_SLICES,
  type ProfileRecord,
  type SliceApplicationConfig,
  type SliceIn,
  type UeClientStatusOut,
} from "../api/client";
import ApplicationSettingsBox from "../components/ApplicationSettingsBox";
import { AppConsoleButtons } from "../components/AppConsoleLinks";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";
import KpiStrip from "../components/ui/KpiStrip";
import { APPLICATION_CONSOLE_LIST } from "../lib/applicationConsoles";

export default function ApplicationsPage() {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [selectedProfileName, setSelectedProfileName] = useState<string>("ina-infra");
  const [profileRecord, setProfileRecord] = useState<ProfileRecord | null>(null);
  const [slices, setSlices] = useState<SliceIn[]>(DEFAULT_SLICES);
  const [applications, setApplications] = useState<Record<string, SliceApplicationConfig>>({});
  const [ueStatus, setUeStatus] = useState<UeClientStatusOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // Console Drawer
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleTitle, setConsoleTitle] = useState("Application Console");
  const [consoleText, setConsoleText] = useState("");
  const [consoleAutoScroll, setConsoleAutoScroll] = useState(true);
  const consoleRef = useRef<HTMLPreElement>(null);

  const appendConsole = useCallback((line: string) => {
    setConsoleText((prev) => (prev ? `${prev}\n${line}` : line));
  }, []);

  const resetConsole = useCallback((title: string) => {
    setConsoleTitle(title);
    setConsoleText("");
    setConsoleOpen(true);
  }, []);

  useEffect(() => {
    if (!consoleAutoScroll || !consoleRef.current) return;
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [consoleText, consoleAutoScroll]);

  // Load profiles list
  const refreshProfiles = useCallback(async () => {
    try {
      const res = await api.listProfiles();
      const names = res.names || [];
      setProfiles(names);
      if (!names.includes(selectedProfileName) && names.length > 0) {
        setSelectedProfileName(names[0]);
      }
    } catch {
      /* ignore */
    }
  }, [selectedProfileName]);

  // Load selected profile
  const loadProfile = useCallback(async (name: string) => {
    if (!name) return;
    setLoading(true);
    setError(null);
    try {
      const rec = await api.getProfile(name);
      setProfileRecord(rec);
      setSlices(rec.slices || DEFAULT_SLICES);
      setApplications(rec.applications || {});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const onUeStatusChange = useCallback((st: UeClientStatusOut | null) => {
    setUeStatus(st);
  }, []);

  useEffect(() => {
    void refreshProfiles();
  }, [refreshProfiles]);

  useEffect(() => {
    if (selectedProfileName) {
      void loadProfile(selectedProfileName);
    }
  }, [selectedProfileName, loadProfile]);

  const activeCount = slices.filter((s) => {
    const cfg = applications[String(s.id)];
    return cfg?.enabled && cfg?.app_type !== "none";
  }).length;

  const deployedCount = slices.filter((s) => {
    const live = ueStatus?.slices?.[String(s.id)];
    if (live) {
      return live.client_ready > 0 || live.overall === "partial" || live.overall === "degraded";
    }
    const cfg = applications[String(s.id)];
    return cfg?.deployed;
  }).length;

  const liveReadyTotal = slices.reduce(
    (n, s) => n + (ueStatus?.slices?.[String(s.id)]?.client_ready || 0),
    0,
  );
  const liveExpectedTotal = slices.reduce(
    (n, s) => n + (ueStatus?.slices?.[String(s.id)]?.expected || 0),
    0,
  );

  const kpiItems = [
    {
      label: "Active Profile",
      kicker: `${profiles.length} registered`,
      value: selectedProfileName || "None",
    },
    {
      label: "Configured Slices",
      kicker: `Max ${profileRecord?.profile.max_slices || 4}`,
      value: String(slices.length),
    },
    {
      label: "Active Clients",
      kicker: "UE application clients",
      value: String(activeCount),
      tone: (activeCount > 0 ? "ok" : "muted") as "ok" | "muted",
    },
    {
      label: "Deployed UEs",
      kicker: "Live edge oai-ue-*",
      value: liveExpectedTotal
        ? `${liveReadyTotal}/${liveExpectedTotal}`
        : `${deployedCount}/${slices.length}`,
      tone: (deployedCount > 0 ? "ok" : "muted") as "ok" | "muted",
    },
  ];

  return (
    <div className="page applications-page">
      <KpiStrip items={kpiItems} />

      <Card className="tier">
        <div className="panel-head">
          <SectionLabel kicker="Namespace target">
            Profile & workload scope
          </SectionLabel>
          <div className="actions">
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span className="muted" style={{ fontWeight: 500 }}>Profile:</span>
              <select
                value={selectedProfileName}
                onChange={(e) => setSelectedProfileName(e.target.value)}
                disabled={loading}
              >
                {profiles.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void loadProfile(selectedProfileName)}
              disabled={loading}
              title="Reload profile workloads and status"
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
            <button
              type="button"
              onClick={() => setConsoleOpen((v) => !v)}
              className={consoleOpen ? "is-selected" : ""}
              title="Toggle execution logs terminal drawer"
            >
              {consoleOpen ? "Hide Console" : "Show Console"}
            </button>
          </div>
        </div>
        {error && <div className="banner error">{error}</div>}
        {status && <div className="banner ok-inline">{status}</div>}
      </Card>

      <Card className="tier">
        <SectionLabel kicker="Per application">Consoles</SectionLabel>
        <p className="hint" style={{ marginTop: 0 }}>
          Each application has a control console and a Grafana dashboard (metrics).
        </p>
        <div className="app-console-grid">
          {APPLICATION_CONSOLE_LIST.map((app) => (
            <div key={app.id} className="app-console-cell">
              <div className="app-console-cell-title">
                Slice {app.sliceId} · {app.name}
              </div>
              <div className="app-console-cell-actions">
                <AppConsoleButtons appType={app.id} />
              </div>
            </div>
          ))}
        </div>
      </Card>

      {profileRecord && (
        <ApplicationSettingsBox
          profile={profileRecord.profile}
          slices={slices}
          applications={applications}
          onChangeApplications={(updated) => {
            setApplications(updated);
            if (profileRecord) {
              setProfileRecord({ ...profileRecord, applications: updated });
            }
          }}
          onUeStatusChange={onUeStatusChange}
          onDeployStart={(title) => {
            resetConsole(title);
            appendConsole(`[${new Date().toLocaleTimeString()}] Starting: ${title}`);
          }}
          onDeployLog={(_stream, line) => {
            appendConsole(line);
          }}
          onDeployStatus={(msg) => {
            setStatus(msg);
            appendConsole(`ℹ ${msg}`);
          }}
          onDeployDone={(msg) => {
            setStatus(msg);
            appendConsole(`✔ ${msg}`);
            void loadProfile(selectedProfileName);
          }}
          onDeployError={(err) => {
            setError(err);
            appendConsole(`✖ Error: ${err}`);
          }}
        />
      )}

      {/* Live Workloads Summary Table */}
      <Card className="tier" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <SectionLabel kicker={`${slices.length} slices`}>
            Client UE status
          </SectionLabel>
        </div>
        <div className="table-wrap">
          <table className="dtable">
            <thead>
              <tr>
                <th>Slice</th>
                <th>Application</th>
                <th>Client UEs</th>
                <th>Client image</th>
                <th>Client metrics</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {slices.map((s) => {
                const cfg = applications[String(s.id)];
                const appType = cfg?.app_type || "none";
                const live = ueStatus?.slices?.[String(s.id)];
                const p = cfg?.params || {};
                const clientCount = Number(p.client_count || live?.expected || 1);
                const pill =
                  live?.overall === "ready"
                    ? "status-pill ok"
                    : live?.overall === "partial" ||
                        live?.overall === "degraded" ||
                        live?.overall === "ran_only"
                      ? "status-pill warn"
                      : cfg?.deployed
                        ? "status-pill warn"
                        : "status-pill muted";
                const label = live?.summary
                  ? live.summary
                  : cfg?.deployed
                    ? "Saved as deployed"
                    : "Not on cluster";

                return (
                  <tr key={s.id}>
                    <td>
                      <span className="app-slice-badge" style={{ fontSize: 11, padding: "2px 8px" }}>
                        S{s.id} {s.slice_type ? `(${s.slice_type})` : ""}
                      </span>
                    </td>
                    <td>
                      <strong>{cfg?.name || `Slice ${s.id}`}</strong>
                      {appType !== "none" && <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>({appType})</span>}
                    </td>
                    <td className="mono">{appType === "none" ? "—" : clientCount}</td>
                    <td>
                      <code style={{ fontSize: 11 }}>{cfg?.client_image || "—"}</code>
                    </td>
                    <td className="mono">:{p.client_metrics_port || 9101}</td>
                    <td>
                      <span className={pill} style={{ fontSize: 10 }} title={live?.deployments?.map((d) => `${d.name} ${d.ready_text}`).join(", ")}>
                        {label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Slide-out Console Drawer */}
      <div
        className={`console-drawer ${consoleOpen ? "open" : ""}`}
        style={{
          display: consoleOpen ? "block" : "none",
          marginTop: 16,
        }}
      >
        <Card className="console-panel" glow>
          <div className="panel-head">
            <SectionLabel>{consoleTitle}</SectionLabel>
            <div className="actions">
              <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
                <input
                  type="checkbox"
                  checked={consoleAutoScroll}
                  onChange={(e) => setConsoleAutoScroll(e.target.checked)}
                />
                Auto-scroll
              </label>
              <button
                type="button"
                style={{ padding: "2px 8px", fontSize: 11 }}
                onClick={() => setConsoleText("")}
              >
                Clear
              </button>
              <button
                type="button"
                style={{ padding: "2px 8px", fontSize: 11 }}
                onClick={() => void navigator.clipboard?.writeText(consoleText)}
              >
                Copy
              </button>
              <button
                type="button"
                style={{ padding: "2px 8px", fontSize: 11 }}
                onClick={() => setConsoleOpen(false)}
              >
                Close
              </button>
            </div>
          </div>
          <pre
            ref={consoleRef}
            className="console-body"
            style={{
              maxHeight: 340,
              overflowY: "auto",
              padding: 14,
              background: "var(--bg-card)",
              borderRadius: 8,
              fontSize: 12,
              fontFamily: "monospace",
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {consoleText || (
              <span className="muted">
                No execution logs yet. Click Deploy All Slices or Deploy on an application to stream live logs.
              </span>
            )}
          </pre>
        </Card>
      </div>
    </div>
  );
}
