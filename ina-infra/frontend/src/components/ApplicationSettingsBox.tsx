import { useEffect, useRef, useState } from "react";
import {
  type Profile,
  type SliceApplicationConfig,
  type SliceIn,
  type StreamHandlers,
  type UeClientStatusOut,
  type UeSliceStatus,
  api,
} from "../api/client";
import Card from "./ui/Card";
import SectionLabel from "./ui/SectionLabel";
import FieldHelp from "./FieldHelp";
import { useDialog } from "./ui/Dialog";
import { defaultClientImage } from "../lib/applicationDefaults";
import {
  CCTV_CLIPS,
  defaultCctvClipIds,
  clipById,
} from "../lib/cctvVideos";

function BtnProgress({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <span className="btn-progress" aria-hidden>
      <span className="btn-progress-spin" />
    </span>
  );
}

const IconDeploy = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" />
    <path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z" />
  </svg>
);

const IconUndeploy = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
);

const IconConfig = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

function livePillClass(overall?: string, fallbackDeployed?: boolean, lastError?: string | null) {
  if (overall === "ready") return "status-pill ok";
  if (overall === "partial" || overall === "degraded" || overall === "ran_only") {
    return "status-pill warn";
  }
  if (lastError) return "status-pill err";
  if (fallbackDeployed) return "status-pill warn";
  return "status-pill muted";
}

function liveLabel(live?: UeSliceStatus, cfg?: SliceApplicationConfig) {
  if (live?.summary) return live.summary;
  if (cfg?.deployed) return "Saved as deployed";
  if (cfg?.last_error) return "Error";
  return "Not on cluster";
}

type Props = {
  profile: Profile;
  slices: SliceIn[];
  applications: Record<string, SliceApplicationConfig>;
  onChangeApplications: (apps: Record<string, SliceApplicationConfig>) => void;
  onDeployStart?: (title: string) => void;
  onDeployLog?: (stream: string, line: string) => void;
  onDeployStatus?: (msg: string) => void;
  onDeployDone?: (msg: string) => void;
  onDeployError?: (err: string) => void;
  onUeStatusChange?: (status: UeClientStatusOut | null) => void;
  disabled?: boolean;
};

export default function ApplicationSettingsBox({
  profile,
  slices,
  applications,
  onChangeApplications,
  onDeployStart,
  onDeployLog,
  onDeployStatus,
  onDeployDone,
  onDeployError,
  onUeStatusChange,
  disabled = false,
}: Props) {
  const dialog = useDialog();
  const [showWorkloads, setShowWorkloads] = useState(true);
  const [collapsedSlices, setCollapsedSlices] = useState<Record<number, boolean>>({});
  const [runningAction, setRunningAction] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [ueStatus, setUeStatus] = useState<UeClientStatusOut | null>(null);
  const dirtyRef = useRef(false);

  const toggleExpand = (sid: number) => {
    setCollapsedSlices((prev) => ({ ...prev, [sid]: !prev[sid] }));
  };

  const getAppConfig = (sid: number): SliceApplicationConfig => {
    if (applications[String(sid)]) {
      return applications[String(sid)];
    }
    return {
      slice_id: sid,
      name: `Slice ${sid} Client`,
      app_type: "none",
      enabled: false,
      server_image: "",
      client_image: "",
      server_port: 8080,
      metrics_port: 9101,
      target_cluster: "auto",
      params: {
        client_count: 1,
        video_clip_ids: defaultCctvClipIds(1),
        fps: 25,
        bitrate_kbps: 4000,
        rtsp_protocol: "tcp",
      },
    };
  };

  const updateApp = (sid: number, patch: Partial<SliceApplicationConfig>) => {
    const current = getAppConfig(sid);
    const updated = { ...current, ...patch };
    const next = { ...applications, [String(sid)]: updated };
    dirtyRef.current = true;
    onChangeApplications(next);
  };

  const updateParam = (sid: number, key: string, value: any) => {
    const current = getAppConfig(sid);
    const nextParams = { ...(current.params || {}), [key]: value };
    updateApp(sid, { params: nextParams });
  };

  const snapshotApps = () => {
    const next: Record<string, SliceApplicationConfig> = { ...applications };
    for (const s of slices) {
      next[String(s.id)] = getAppConfig(s.id);
    }
    return next;
  };

  const persistApps = async (apps: Record<string, SliceApplicationConfig>) => {
    if (!profile.name) return null;
    const rec = await api.saveProfileApplications(profile.name, apps);
    dirtyRef.current = false;
    if (rec.applications) {
      onChangeApplications(rec.applications);
    }
    return rec;
  };

  useEffect(() => {
    if (!profile.name) {
      setUeStatus(null);
      onUeStatusChange?.(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const st = await api.ueClientStatus(profile.name);
        if (cancelled) return;
        setUeStatus(st);
        onUeStatusChange?.(st);
      } catch {
        if (!cancelled) {
          setUeStatus(null);
          onUeStatusChange?.(null);
        }
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [profile.name, onUeStatusChange]);

  useEffect(() => {
    if (!dirtyRef.current || !profile.name) return;
    const t = window.setTimeout(() => {
      const apps = snapshotApps();
      void api
        .saveProfileApplications(profile.name, apps)
        .then((rec) => {
          dirtyRef.current = false;
          if (rec.applications) onChangeApplications(rec.applications);
          setSaveStatus("Saved for reload");
          window.setTimeout(() => setSaveStatus(null), 2500);
        })
        .catch((e) => {
          setSaveStatus("Save failed: " + (e instanceof Error ? e.message : String(e)));
        });
    }, 800);
    return () => window.clearTimeout(t);
    // snapshotApps reads latest applications/slices via closure
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applications, profile.name, slices]);

  const handleSaveApplications = async () => {
    if (!profile.name) return;
    setSaving(true);
    setSaveStatus(null);
    try {
      await persistApps(snapshotApps());
      setSaveStatus("Saved client configs");
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (e) {
      setSaveStatus("Save failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  };

  const handleDeploy = async (sliceId?: number) => {
    const isAll = sliceId === undefined;
    const targetName = isAll ? "All Client UEs" : `Slice ${sliceId} Client UE(s)`;

    const ok = await dialog.confirm({
      title: isAll ? "Deploy All Client UEs?" : `Deploy ${targetName}?`,
      message: `Deploy ${targetName} directly to Edge cluster over 5G radio in namespace “${profile.name}”.`,
      confirmLabel: isAll ? "Deploy All Client UEs" : "Deploy Client UEs",
    });
    if (!ok) return;

    const actionKey = isAll ? "deploy-all" : `deploy-${sliceId}`;
    setRunningAction(actionKey);
    onDeployStart?.(`Deploying ${targetName}`);

    const streamH: StreamHandlers = {
      onLog: (stream, line) => onDeployLog?.(stream, line),
      onStatus: (msg) => onDeployStatus?.(msg),
      onError: (err) => onDeployError?.(err),
    };

    try {
      const apps = snapshotApps();
      onChangeApplications(apps);
      await persistApps(apps);
      const cfg = sliceId !== undefined ? apps[String(sliceId)] : null;
      const res = await api.deployAppStream(
        profile.name,
        {
          slice_id: sliceId ?? null,
          config: cfg,
          applications: apps,
          profile,
        },
        streamH,
      );
      if (res.profile?.applications) {
        onChangeApplications(res.profile.applications);
      }
      try {
        const st = await api.ueClientStatus(profile.name);
        setUeStatus(st);
        onUeStatusChange?.(st);
      } catch {
        /* ignore */
      }
      onDeployDone?.(res.message || "Client deployment complete");
    } catch (err) {
      onDeployError?.(err instanceof Error ? err.message : String(err));
    } finally {
      setRunningAction(null);
    }
  };

  const handleUndeploy = async (sliceId?: number) => {
    const isAll = sliceId === undefined;
    const targetName = isAll ? "All Client UEs" : `Slice ${sliceId} Client UE(s)`;

    const ok = await dialog.confirm({
      title: isAll ? "Undeploy All Client UEs?" : `Undeploy ${targetName}?`,
      message: `Remove ${targetName} from Edge cluster in namespace “${profile.name}”.`,
      confirmLabel: isAll ? "Undeploy All" : "Undeploy",
      danger: true,
    });
    if (!ok) return;

    const actionKey = isAll ? "undeploy-all" : `undeploy-${sliceId}`;
    setRunningAction(actionKey);
    onDeployStart?.(`Undeploying ${targetName}`);

    const streamH: StreamHandlers = {
      onLog: (stream, line) => onDeployLog?.(stream, line),
      onStatus: (msg) => onDeployStatus?.(msg),
      onError: (err) => onDeployError?.(err),
    };

    try {
      const res = await api.undeployAppStream(
        profile.name,
        { slice_id: sliceId ?? null, profile },
        streamH,
      );
      if (res.profile?.applications) {
        onChangeApplications(res.profile.applications);
      }
      try {
        const st = await api.ueClientStatus(profile.name);
        setUeStatus(st);
        onUeStatusChange?.(st);
      } catch {
        /* ignore */
      }
      onDeployDone?.(res.message || "Client undeployment complete");
    } catch (err) {
      onDeployError?.(err instanceof Error ? err.message : String(err));
    } finally {
      setRunningAction(null);
    }
  };

  const activeCount = slices.filter((s) => {
    const cfg = getAppConfig(s.id);
    return cfg.enabled && cfg.app_type !== "none";
  }).length;

  const deployedCount = slices.filter((s) => {
    const live = ueStatus?.slices?.[String(s.id)];
    if (live) {
      return live.client_ready > 0 || live.overall === "partial" || live.overall === "degraded";
    }
    return !!getAppConfig(s.id).deployed;
  }).length;

  const liveReadyTotal = slices.reduce(
    (n, s) => n + (ueStatus?.slices?.[String(s.id)]?.client_ready || 0),
    0,
  );
  const liveExpectedTotal = slices.reduce(
    (n, s) => n + (ueStatus?.slices?.[String(s.id)]?.expected || 0),
    0,
  );

  return (
    <Card className="tier">
      <div className="panel-head">
        <SectionLabel kicker={showWorkloads ? `${activeCount}/${slices.length} active · ${liveExpectedTotal ? `${liveReadyTotal}/${liveExpectedTotal} UEs ready` : `${deployedCount} deployed`}` : "collapsed"}>
          Application Clients & UEs (Direct K8s API)
        </SectionLabel>
        <div className="actions" style={{ flexWrap: "wrap", gap: 8 }}>
          {saveStatus && (
            <span style={{ fontSize: 12, color: saveStatus.includes("failed") ? "#ef4444" : "#22c55e", fontWeight: 600 }}>
              {saveStatus}
            </span>
          )}
          <button
            type="button"
            disabled={disabled || !!runningAction || saving}
            onClick={handleSaveApplications}
            title="Save client configurations to profile DB"
          >
            {saving ? "Saving…" : "Save Client Configs"}
          </button>
          <button
            type="button"
            className="primary"
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            disabled={disabled || !!runningAction || activeCount === 0}
            onClick={() => void handleDeploy()}
            title="Deploy all active client UEs directly to edge cluster over 5G"
          >
            <BtnProgress active={runningAction === "deploy-all"} />
            {runningAction !== "deploy-all" && IconDeploy}
            {runningAction === "deploy-all" ? "Deploying all UEs…" : "Deploy All Client UEs"}
          </button>
          <button
            type="button"
            className="danger"
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            disabled={disabled || !!runningAction || deployedCount === 0}
            onClick={() => void handleUndeploy()}
            title="Undeploy all client UEs from edge cluster"
          >
            <BtnProgress active={runningAction === "undeploy-all"} />
            {runningAction !== "undeploy-all" && IconUndeploy}
            {runningAction === "undeploy-all" ? "Undeploying all…" : "Undeploy All UEs"}
          </button>
          <button type="button" onClick={() => setShowWorkloads((v) => !v)}>
            {showWorkloads ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      {showWorkloads ? (
        <>
          <p className="hint">
            Configure and manage 5G User Equipment (UE) application clients (CCTV camera streamers, Cosmos3 VLM prompt clients, OTT players, IoT telemetry publishers). Server type, image, and placement are profile config on Planning. <strong>Client configs auto-save for reload. Status is live from the edge cluster (not GitOps).</strong>
          </p>

          <div className="app-card-list">
            {slices.map((s) => {
              const cfg = getAppConfig(s.id);
              const isExpanded = !collapsedSlices[s.id];
              const isDeploying = runningAction === `deploy-${s.id}`;
              const isUndeploying = runningAction === `undeploy-${s.id}`;
              const p = cfg.params || {};
              const clientCount = p.client_count || 1;
              const live = ueStatus?.slices?.[String(s.id)];
              const canUndeploy =
                !!cfg.deployed ||
                (live?.client_ready || 0) > 0 ||
                live?.overall === "partial" ||
                live?.overall === "degraded";

              return (
                <div
                  key={s.id}
                  className={`app-slice-card ${live?.overall === "ready" || cfg.deployed ? "is-deployed" : ""}`}
                >
                  <div className="app-slice-head">
                    <div className="app-slice-ident">
                      <span className="app-slice-badge">
                        Slice {s.id} {s.slice_type ? `· ${s.slice_type}` : ""}
                      </span>
                      <span
                        className="tag"
                        style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 700, background: "rgba(168, 85, 247, 0.12)", color: "#c084fc", border: "1px solid rgba(168, 85, 247, 0.3)", borderRadius: 6 }}
                      >
                        App: {cfg.app_type.toUpperCase()}
                      </span>
                      <span
                        className="tag"
                        style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 700, background: "rgba(34, 197, 94, 0.12)", color: "#4ade80", border: "1px solid rgba(34, 197, 94, 0.3)", borderRadius: 6 }}
                      >
                        Active UEs: {clientCount} {clientCount > 1 ? "Cameras" : "Camera"}
                      </span>
                      {cfg.app_type === "cctv" && (
                        <a
                          href="http://10.1.137.121:8080/"
                          target="_blank"
                          rel="noreferrer"
                          className="tag"
                          style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 700, background: "rgba(59, 130, 246, 0.15)", color: "#60a5fa", border: "1px solid rgba(59, 130, 246, 0.4)", borderRadius: 6, textDecoration: "none", cursor: "pointer" }}
                          title="Open live adaptive CCTV Surveillance Video Wall on 10.1.137.121:8080"
                        >
                          📹 Video Wall ↗
                        </a>
                      )}
                      {cfg.app_type === "cctv" && (
                        <a
                          href="http://10.1.137.105:3000/d/ffvbyfvl0i29sd/cctv?orgId=1&refresh=2s"
                          target="_blank"
                          rel="noreferrer"
                          className="tag"
                          style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 600, background: "rgba(249, 115, 22, 0.12)", color: "#fb923c", border: "1px solid rgba(249, 115, 22, 0.25)", borderRadius: 6, textDecoration: "none", cursor: "pointer" }}
                          title="View live CCTV dashboard on Grafana (10.1.137.105:3000)"
                        >
                          Grafana: CCTV ↗
                        </a>
                      )}
                      <span
                        className={livePillClass(live?.overall, cfg.deployed, cfg.last_error)}
                        style={{ fontSize: 11, whiteSpace: "nowrap", flexShrink: 0 }}
                        title={
                          live?.deployments
                            ?.map((d) => `${d.name} ${d.ready_text} ${d.status}${d.client_sidecar ? " · client" : ""}`)
                            .join("\n") || undefined
                        }
                      >
                        {liveLabel(live, cfg)}
                      </span>
                    </div>

                    <div className="app-slice-actions">
                      <button
                        type="button"
                        className="primary"
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 14px", fontSize: 12, borderRadius: 20 }}
                        disabled={disabled || !!runningAction || cfg.app_type === "none"}
                        onClick={() => void handleDeploy(s.id)}
                        title={`Deploy slice ${s.id} UE client(s) directly to Edge cluster`}
                      >
                        <BtnProgress active={isDeploying} />
                        {!isDeploying && IconDeploy}
                        {isDeploying ? "Deploying…" : "Deploy UEs"}
                      </button>
                      <button
                        type="button"
                        className="danger"
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 14px", fontSize: 12, borderRadius: 20 }}
                        disabled={disabled || !!runningAction || !canUndeploy}
                        onClick={() => void handleUndeploy(s.id)}
                        title={`Undeploy slice ${s.id} UE client(s)`}
                      >
                        <BtnProgress active={isUndeploying} />
                        {!isUndeploying && IconUndeploy}
                        {isUndeploying ? "Undeploying…" : "Undeploy"}
                      </button>
                      <button
                        type="button"
                        className={isExpanded ? "is-selected" : ""}
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 12px", fontSize: 12, borderRadius: 20 }}
                        onClick={() => toggleExpand(s.id)}
                        title={isExpanded ? "Collapse client configuration" : "Open client configuration"}
                      >
                        {IconConfig}
                        {isExpanded ? "Close Config" : "Client Config"}
                      </button>
                    </div>
                  </div>

                  {cfg.last_error && (
                    <div className="banner error" style={{ fontSize: 11.5, padding: "6px 10px" }}>
                      {cfg.last_error}
                    </div>
                  )}

                  {live && live.deployments.length > 0 && (
                    <div
                      className="muted"
                      style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "0 12px 8px" }}
                    >
                      {live.deployments.map((d) => (
                        <span
                          key={d.name}
                          className={d.ok && d.client_sidecar ? "status-pill ok" : d.exists ? "status-pill warn" : "status-pill muted"}
                          style={{ fontSize: 10 }}
                          title={d.name}
                        >
                          {d.name} {d.ready_text} {d.status}
                          {d.client_sidecar ? "" : d.exists ? " · RAN" : ""}
                        </span>
                      ))}
                    </div>
                  )}

                  {isExpanded && cfg.app_type !== "none" && (
                    <div className="app-config-grid">
                          <FieldHelp
                        label="Client UEs / Cameras"
                        help="Number of concurrent UE client containers streaming video/telemetry over 5G. Each CCTV camera uses a different sample clip."
                      >
                        <input
                          type="number"
                          min={1}
                          max={10}
                          value={p.client_count ?? 1}
                          disabled={disabled || !!runningAction}
                          onChange={(e) => {
                            const n = Math.max(1, Number(e.target.value) || 1);
                            const current = getAppConfig(s.id);
                            const prev = Array.isArray(current.params?.video_clip_ids)
                              ? (current.params.video_clip_ids as string[])
                              : [];
                            const nextIds = defaultCctvClipIds(n).map(
                              (id, i) => prev[i] || id,
                            );
                            updateApp(s.id, {
                              params: {
                                ...(current.params || {}),
                                client_count: n,
                                video_clip_ids: nextIds,
                              },
                            });
                          }}
                        />
                      </FieldHelp>

                      <FieldHelp
                        className="field-help-wide"
                        label="Client image"
                        help="Container registry image for the UE client publisher sidecar."
                      >
                        <input
                          value={cfg.client_image || defaultClientImage(cfg.app_type)}
                          disabled={disabled || !!runningAction}
                          onChange={(e) => updateApp(s.id, { client_image: e.target.value })}
                        />
                      </FieldHelp>

                      {cfg.app_type === "cctv" && (
                        <>
                          <FieldHelp
                            className="field-help-wide"
                            label="Camera videos"
                            help="Each UE downloads a different Intel sample clip into /data at start. Change the assignment per camera."
                          >
                            <div className="cctv-clip-list">
                              {Array.from(
                                { length: Math.max(1, Number(p.client_count) || 1) },
                                (_, i) => {
                                  const ids = Array.isArray(p.video_clip_ids)
                                    ? (p.video_clip_ids as string[])
                                    : defaultCctvClipIds(Number(p.client_count) || 1);
                                  const selected = ids[i] || defaultCctvClipIds(i + 1)[i];
                                  return (
                                    <label key={`cam-${s.id}-${i}`} className="cctv-clip-row">
                                      <span>Cam {i + 1}</span>
                                      <select
                                        value={selected}
                                        disabled={disabled || !!runningAction}
                                        onChange={(e) => {
                                          const count = Math.max(
                                            1,
                                            Number(p.client_count) || 1,
                                          );
                                          const next = defaultCctvClipIds(count).map(
                                            (id, j) => ids[j] || id,
                                          );
                                          next[i] = e.target.value;
                                          updateParam(s.id, "video_clip_ids", next);
                                        }}
                                      >
                                        {CCTV_CLIPS.map((c) => (
                                          <option key={c.id} value={c.id}>
                                            {c.label}
                                          </option>
                                        ))}
                                      </select>
                                      <span className="muted">{clipById(selected).file}</span>
                                    </label>
                                  );
                                },
                              )}
                            </div>
                          </FieldHelp>

                          <FieldHelp
                            label="Target FPS"
                            help="Frame rate emitted by GStreamer publisher over 5G."
                          >
                            <input
                              type="number"
                              min={1}
                              max={60}
                              value={p.fps ?? 25}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "fps", Number(e.target.value) || 25)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="Target Bitrate (kbps)"
                            help="Video encoding bitrate transmitted over 5G."
                          >
                            <input
                              type="number"
                              min={100}
                              max={50000}
                              value={p.bitrate_kbps ?? 4000}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "bitrate_kbps", Number(e.target.value) || 4000)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="RTSP protocol"
                            help="Transport protocol for streaming over 5G radio."
                          >
                            <select
                              value={p.rtsp_protocol || "tcp"}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "rtsp_protocol", e.target.value)}
                            >
                              <option value="tcp">TCP (Reliable)</option>
                              <option value="udp">UDP (Low Latency)</option>
                            </select>
                          </FieldHelp>

                          <FieldHelp
                            label="Base stream path"
                            help="RTSP path prefix (e.g. cctv/ue1)."
                          >
                            <input
                              value={p.stream_path || `cctv/ue${s.id}`}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "stream_path", e.target.value)}
                            />
                          </FieldHelp>
                        </>
                      )}

                      {cfg.app_type === "physical_ai" && (
                        <>
                          <FieldHelp
                            label="Prompt interval (s)"
                            help="Inference request interval from client UE."
                          >
                            <input
                              type="number"
                              value={p.prompt_interval_s ?? 2}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "prompt_interval_s", Number(e.target.value) || 2)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="Max output tokens"
                            help="Response token budget per prompt."
                          >
                            <input
                              type="number"
                              value={p.max_tokens ?? 128}
                              disabled={disabled || !!runningAction}
                              onChange={(e) => updateParam(s.id, "max_tokens", Number(e.target.value) || 128)}
                            />
                          </FieldHelp>
                        </>
                      )}

                      <FieldHelp
                        label="Client metrics port"
                        help="Port for application client Prometheus exporter."
                      >
                        <input
                          type="number"
                          value={p.client_metrics_port || 9101}
                          disabled={disabled || !!runningAction}
                          onChange={(e) => updateParam(s.id, "client_metrics_port", Number(e.target.value) || 9101)}
                        />
                      </FieldHelp>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <p className="hint">Collapsed — Show to manage and scale client UEs.</p>
      )}
    </Card>
  );
}
