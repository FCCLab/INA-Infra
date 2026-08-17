import { useState } from "react";
import {
  type PlSolveResponse,
  type Profile,
  type SliceApplicationConfig,
  type SliceAppType,
  type SliceIn,
  api,
} from "../api/client";
import { defaultsForAppType } from "../lib/applicationDefaults";
import Card from "./ui/Card";
import SectionLabel from "./ui/SectionLabel";
import FieldHelp from "./FieldHelp";

const IconConfig = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const APP_TYPES: { id: SliceAppType; label: string; tag: string }[] = [
  { id: "cctv", label: "CCTV Vision Server", tag: "YOLO + RTSP Analyzer" },
  { id: "physical_ai", label: "Physical AI Server (Cosmos3)", tag: "vLLM on A40 / GH200" },
  { id: "ott", label: "OTT Video Server", tag: "RTSP / HLS Server" },
  { id: "iot", label: "IoT Broker Server", tag: "Mosquitto Broker" },
  { id: "custom", label: "Custom Server", tag: "Generic Service" },
  { id: "none", label: "None (No Server)", tag: "Disabled" },
];

function getClusterTagClass(cluster?: string | null): string {
  if (!cluster) return "tag-cluster-auto";
  const c = cluster.toLowerCase();
  if (c.includes("edge")) return "tag-cluster-edge";
  if (c.includes("regional")) return "tag-cluster-regional";
  if (c.includes("central")) return "tag-cluster-central";
  return "tag-cluster-auto";
}

type Props = {
  profile: Profile;
  slices: SliceIn[];
  applications: Record<string, SliceApplicationConfig>;
  plResult?: PlSolveResponse | null;
  onChangeApplications: (apps: Record<string, SliceApplicationConfig>) => void;
  disabled?: boolean;
  embedded?: boolean;
};

export default function ApplicationServerSettingsBox({
  profile,
  slices,
  applications,
  plResult,
  onChangeApplications,
  disabled = false,
  embedded = false,
}: Props) {
  const [showWorkloads, setShowWorkloads] = useState(true);
  const [collapsedSlices, setCollapsedSlices] = useState<Record<number, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  const toggleExpand = (sid: number) => {
    setCollapsedSlices((prev) => ({ ...prev, [sid]: !prev[sid] }));
  };

  const getAppConfig = (sid: number): SliceApplicationConfig => {
    if (applications[String(sid)]) {
      return applications[String(sid)];
    }
    return {
      slice_id: sid,
      name: `Slice ${sid} Server`,
      app_type: "none",
      enabled: false,
      server_image: "",
      client_image: "",
      server_port: 8080,
      metrics_port: 9102,
      target_cluster: "auto",
      params: {},
    };
  };

  const updateApp = (sid: number, patch: Partial<SliceApplicationConfig>) => {
    const current = getAppConfig(sid);
    const updated = { ...current, ...patch };
    const next = { ...applications, [String(sid)]: updated };
    onChangeApplications(next);
  };

  const updateParam = (sid: number, key: string, value: any) => {
    const current = getAppConfig(sid);
    const nextParams = { ...(current.params || {}), [key]: value };
    updateApp(sid, { params: nextParams });
  };

  const handleAppTypeChange = (sid: number, newType: SliceAppType) => {
    const current = getAppConfig(sid);
    const defaults = defaultsForAppType(newType, sid);
    const kept: Record<string, unknown> = {};
    const cur = current.params || {};
    if (cur.client_count != null) kept.client_count = cur.client_count;
    if (cur.client_metrics_port != null) kept.client_metrics_port = cur.client_metrics_port;
    updateApp(sid, {
      ...defaults,
      app_type: newType,
      enabled: newType !== "none",
      params: {
        ...(defaults.params || {}),
        ...kept,
      },
    });
  };

  const handleSave = async () => {
    if (!profile.name) return;
    setSaving(true);
    setSaveStatus(null);
    try {
      await api.saveProfileApplications(profile.name, applications);
      setSaveStatus("Saved server configs");
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (e) {
      setSaveStatus("Save failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  };

  const getPlPlacement = (sid: number): string | null => {
    if (!plResult?.deploy_map) return null;
    const place = plResult.deploy_map[String(sid)];
    if (!place) return null;
    const siteId = place.app_id;
    if (siteId === 0) return "edge";
    if (siteId === 1) return "regional";
    if (siteId === 2) return "central";
    return null;
  };

  const activeCount = slices.filter((s) => {
    const cfg = getAppConfig(s.id);
    return cfg.enabled && cfg.app_type !== "none";
  }).length;

  const body = (
    <>
      <div className="panel-head">
        <SectionLabel kicker={`${activeCount}/${slices.length} servers · GitOps`}>
          Application Servers
        </SectionLabel>
        <div className="actions" style={{ flexWrap: "wrap", gap: 8 }}>
          {saveStatus && (
            <span style={{ fontSize: 12, color: saveStatus.includes("failed") ? "#ef4444" : "#22c55e", fontWeight: 600 }}>
              {saveStatus}
            </span>
          )}
          <button
            type="button"
            disabled={disabled || saving}
            onClick={handleSave}
            title="Save application server configuration to profile"
          >
            {saving ? "Saving…" : "Save Server Configs"}
          </button>
          <button type="button" onClick={() => setShowWorkloads((v) => !v)}>
            {showWorkloads ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      {showWorkloads ? (
        <>
          <p className="hint">
            Slice config: application servers mapped to each slice (CCTV YOLO, Cosmos3 VLM, OTT RTSP, IoT MQTT). Client UE settings stay on the Applications page. <strong>Servers are placed by PL and deployed via GitOps when clicking Deploy.</strong>
          </p>

          <div className="app-card-list">
            {slices.map((s) => {
              const cfg = getAppConfig(s.id);
              const isExpanded = !collapsedSlices[s.id];
              const p = cfg.params || {};
              const plSite = getPlPlacement(s.id);
              const resolvedCluster =
                cfg.app_type === "physical_ai" &&
                (!cfg.target_cluster || cfg.target_cluster === "auto")
                  ? "edge"
                  : cfg.target_cluster && cfg.target_cluster !== "auto"
                    ? cfg.target_cluster
                    : plSite || "Auto (PL)";

              return (
                <div key={s.id} className="app-slice-card">
                  <div className="app-slice-head">
                    <div className="app-slice-ident">
                      <span className="app-slice-badge">
                        Slice {s.id} {s.slice_type ? `· ${s.slice_type}` : ""}
                      </span>
                      <select
                        className="app-type-select"
                        value={cfg.app_type}
                        disabled={disabled}
                        onChange={(e) => handleAppTypeChange(s.id, e.target.value as SliceAppType)}
                      >
                        {APP_TYPES.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.label} ({t.tag})
                          </option>
                        ))}
                      </select>
                      <span
                        className={`tag ${getClusterTagClass(resolvedCluster)}`}
                        style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 700 }}
                        title={plSite ? `PL solver placement: ${plSite}` : "Target cluster placement"}
                      >
                        Server Cluster: {resolvedCluster}
                      </span>
                      <span
                        className="tag"
                        style={{ fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap", flexShrink: 0, fontWeight: 600, background: "rgba(59, 130, 246, 0.12)", color: "var(--accent, #60a5fa)", border: "1px solid rgba(59, 130, 246, 0.25)", borderRadius: 6 }}
                        title={`Multus static IP allocated on subnet prefix ${profile.subnet || "10.1.140.0/24"}`}
                      >
                        N6 Data IP: 10.1.137.{160 + s.id}
                      </span>
                    </div>

                    <div className="app-slice-actions">
                      <button
                        type="button"
                        className={isExpanded ? "is-selected" : ""}
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 12px", fontSize: 12, borderRadius: 20 }}
                        onClick={() => toggleExpand(s.id)}
                        title={isExpanded ? "Collapse configuration" : "Open server configuration"}
                      >
                        {IconConfig}
                        {isExpanded ? "Close Config" : "Server Config"}
                      </button>
                    </div>
                  </div>

                  {isExpanded && cfg.app_type !== "none" && (
                    <div className="app-config-grid">
                      <FieldHelp
                        label="Server app name"
                        help="Descriptive display name of the application server workload."
                      >
                        <input
                          value={cfg.name}
                          disabled={disabled}
                          onChange={(e) => updateApp(s.id, { name: e.target.value })}
                        />
                      </FieldHelp>

                      <FieldHelp
                        label="Placement cluster"
                        help="Cluster where the server container is deployed. Physical AI (slice 2) defaults to edge gpu-a40; other types use Auto (PL)."
                      >
                        <select
                          value={cfg.target_cluster || "auto"}
                          disabled={disabled}
                          onChange={(e) => updateApp(s.id, { target_cluster: e.target.value })}
                        >
                          <option value="auto">
                            Auto {plSite ? `(PL selected: ${plSite})` : "(PL solver placement)"}
                          </option>
                          <option value="edge">Edge cluster</option>
                          <option value="regional">Regional cluster</option>
                          <option value="central">Central cluster</option>
                        </select>
                      </FieldHelp>

                      <FieldHelp
                        className="field-help-wide"
                        label="Server image"
                        help="Container registry image for server/analyzer/broker."
                      >
                        <input
                          value={cfg.server_image}
                          disabled={disabled}
                          onChange={(e) => updateApp(s.id, { server_image: e.target.value })}
                        />
                      </FieldHelp>

                      <FieldHelp
                        label="Service port"
                        help="Main service TCP/UDP port exposed by the application server."
                      >
                        <input
                          type="number"
                          value={cfg.server_port || ""}
                          disabled={disabled}
                          onChange={(e) => updateApp(s.id, { server_port: Number(e.target.value) || 0 })}
                        />
                      </FieldHelp>

                      <FieldHelp
                        label="Metrics port"
                        help="Prometheus scrape port exposed by the server."
                      >
                        <input
                          type="number"
                          value={cfg.metrics_port || ""}
                          disabled={disabled}
                          onChange={(e) => updateApp(s.id, { metrics_port: Number(e.target.value) || 0 })}
                        />
                      </FieldHelp>

                      {cfg.app_type === "cctv" && (
                        <>
                          <FieldHelp
                            label="YOLO model"
                            help="YOLO detection model checkpoint file."
                          >
                            <select
                              value={p.yolo_model || "yolov8n.pt"}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "yolo_model", e.target.value)}
                            >
                              <option value="yolov8n.pt">yolov8n.pt (Nano - Fast)</option>
                              <option value="yolov8s.pt">yolov8s.pt (Small)</option>
                              <option value="yolov8m.pt">yolov8m.pt (Medium)</option>
                            </select>
                          </FieldHelp>

                          <FieldHelp
                            label="YOLO device"
                            help="Hardware accelerator device for YOLO inference."
                          >
                            <select
                              value={p.yolo_device || "cpu"}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "yolo_device", e.target.value)}
                            >
                              <option value="cpu">CPU</option>
                              <option value="cuda:0">CUDA GPU (cuda:0)</option>
                            </select>
                          </FieldHelp>

                          <FieldHelp
                            label="Frame skip"
                            help="Process 1 out of N frames for real-time throughput matching."
                          >
                            <input
                              type="number"
                              min={1}
                              max={10}
                              value={p.frame_skip ?? 1}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "frame_skip", Number(e.target.value) || 1)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="RTSP server port"
                            help="RTSP listener port on N6 Data Network."
                          >
                            <input
                              type="number"
                              value={p.rtsp_port || 8554}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "rtsp_port", Number(e.target.value) || 8554)}
                            />
                          </FieldHelp>
                        </>
                      )}

                      {cfg.app_type === "physical_ai" && (
                        <>
                          <FieldHelp
                            className="field-help-wide"
                            label="Model ID"
                            help="HuggingFace / vLLM model checkpoint."
                          >
                            <input
                              value={p.model || "nvidia/Cosmos3-Nano"}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "model", e.target.value)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="Max model length"
                            help="Context window length tokens."
                          >
                            <input
                              type="number"
                              value={p.max_model_len || 2048}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "max_model_len", Number(e.target.value) || 2048)}
                            />
                          </FieldHelp>

                          <FieldHelp
                            label="GPU memory ratio"
                            help="vLLM GPU memory utilization limit (0.1 - 1.0)."
                          >
                            <input
                              type="number"
                              step="0.05"
                              min="0.1"
                              max="1.0"
                              value={p.gpu_memory_utilization || 0.9}
                              disabled={disabled}
                              onChange={(e) => updateParam(s.id, "gpu_memory_utilization", Number(e.target.value) || 0.9)}
                            />
                          </FieldHelp>
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <p className="hint">Collapsed — Show to configure application servers for GitOps deployment.</p>
      )}
    </>
  );

  if (embedded) {
    return <div className="slice-config-section">{body}</div>;
  }
  return <Card className="tier">{body}</Card>;
}
