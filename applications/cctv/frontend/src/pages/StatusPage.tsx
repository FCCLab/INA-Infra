import { useEffect, useState } from "react";
import Card from "../components/ui/Card";
import KpiStrip from "../components/ui/KpiStrip";
import SectionLabel from "../components/ui/SectionLabel";
import StatusDot from "../components/ui/StatusDot";
import { fetchStatus, formatCameraName, type CctvStatus } from "../lib/api";

export default function StatusPage() {
  const [st, setSt] = useState<CctvStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const next = await fetchStatus();
        if (!stop) {
          setSt(next);
          setErr(null);
        }
      } catch (e) {
        if (!stop) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedPath(text);
    setTimeout(() => setCopiedPath(null), 2000);
  };

  const clients = st?.clients || [];
  const live = clients.filter((c) => c.active).length;
  const mtxOk = st?.mediamtx && !("error" in (st.mediamtx as object));

  return (
    <>
      <KpiStrip
        items={[
          {
            label: "Active Ingest Feeds",
            value: String(live),
            kicker: `${clients.length} registered cameras`,
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M4 6h11a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z" />
              </svg>
            ),
          },
          {
            label: "Inference Engine",
            value: st?.yolo_enabled ? (st.yolo_process_per_client ? "Per-Client" : "Shared") : "Disabled",
            kicker: st?.yolo_model || "YOLOv8n",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            ),
          },
          {
            label: "Device Target",
            value: (st?.yolo_device || "cpu").toUpperCase(),
            kicker: st?.yolo_device === "cuda" ? "NVIDIA GPU acceleration" : "CPU inference",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="4" y="4" width="16" height="16" rx="2" />
                <rect x="9" y="9" width="6" height="6" />
                <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
              </svg>
            ),
          },
          {
            label: "MediaMTX Pub/Sub",
            value: mtxOk ? "HEALTHY" : "DOWN",
            kicker: "HLS :8888 · WHEP :8889",
            bad: !mtxOk,
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 8v4M12 16h.01" />
              </svg>
            ),
          },
          {
            label: "RTSP Ingest Port",
            value: `:${st?.rtsp_port ?? 8554}`,
            kicker: "RTSP RECORD direct ingest",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="2" width="20" height="8" rx="2" />
                <rect x="2" y="14" width="20" height="8" rx="2" />
                <path d="M6 6h.01M6 18h.01" />
              </svg>
            ),
          },
        ]}
      />

      {err && <div className="banner error">{err}</div>}

      <div className="row-2 tier">
        <Card>
          <SectionLabel kicker="Engine Configuration">YOLO Vision Pipeline</SectionLabel>
          <table className="slice-table" style={{ marginTop: 8 }}>
            <tbody>
              <tr>
                <td style={{ width: "40%" }}>YOLO Model</td>
                <td className="mono"><strong>{st?.yolo_model || "yolov8n.pt"}</strong></td>
              </tr>
              <tr>
                <td>Execution Device</td>
                <td className="mono">{st?.yolo_device || "cpu"}</td>
              </tr>
              <tr>
                <td>Worker Architecture</td>
                <td className="mono">{st?.yolo_process_per_client ? "Dedicated worker process per stream" : "Shared worker queue"}</td>
              </tr>
              <tr>
                <td>Default Ingest Path</td>
                <td className="mono">/{st?.stream_path || "slicea"}</td>
              </tr>
              <tr>
                <td>Frontend UI Directory</td>
                <td className="mono">{st?.frontend ? "Mounted & Active" : "Missing /ui/dist"}</td>
              </tr>
            </tbody>
          </table>
        </Card>

        <Card>
          <SectionLabel kicker="Networking & Protocols">Ports & Routing</SectionLabel>
          <table className="slice-table" style={{ marginTop: 8 }}>
            <tbody>
              <tr>
                <td style={{ width: "40%" }}>RTSP Ingest (GStreamer)</td>
                <td className="mono">:{st?.rtsp_port ?? 8554} (TCP / UDP RECORD)</td>
              </tr>
              <tr>
                <td>HTTP API & Dashboard</td>
                <td className="mono">:{st?.http_port ?? 8080}</td>
              </tr>
              <tr>
                <td>MediaMTX Internal RTSP</td>
                <td className="mono">:8555 (annotated feed publish)</td>
              </tr>
              <tr>
                <td>HLS Subscriptions</td>
                <td className="mono">:8888 (Proxied at <code>/live/*</code>)</td>
              </tr>
              <tr>
                <td>WHEP Subscriptions</td>
                <td className="mono">:8889 (Proxied at <code>/whep/*</code>)</td>
              </tr>
            </tbody>
          </table>
        </Card>
      </div>

      <Card className="tier" glow>
        <SectionLabel kicker="Real-time Camera Stream Registry">Active Streams & Telemetry</SectionLabel>
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="cctv-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Camera ID</th>
                <th>Mount Endpoint</th>
                <th>HLS Subscribe URL</th>
                <th>FPS</th>
                <th>Net Delay</th>
                <th>YOLO Delay</th>
                <th>E2E Delay</th>
                <th>Detections</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.id}>
                  <td>
                    <StatusDot
                      state={c.active ? "ok" : "warn"}
                      label={c.active ? "LIVE" : "IDLE"}
                    />
                  </td>
                  <td>
                    <strong>{formatCameraName(c.id, c.name)}</strong>
                    <div className="mono muted" style={{ fontSize: "10px" }}>{c.id}</div>
                  </td>
                  <td className="mono">
                    <code>{c.publish_path || `/${c.id}`}</code>
                  </td>
                  <td className="mono" style={{ fontSize: "11px" }}>
                    <span>{c.hls_path}</span>
                    <button
                      type="button"
                      className="copy-btn"
                      onClick={() => handleCopy(c.hls_path)}
                    >
                      {copiedPath === c.hls_path ? "Copied" : "Copy"}
                    </button>
                  </td>
                  <td className="mono">
                    <span className={`pill-metric ${c.active ? "accent" : ""}`}>
                      {c.fps.toFixed(1)} fps
                    </span>
                  </td>
                  <td className="mono">{c.net_delay_ms.toFixed(1)} ms</td>
                  <td className="mono">
                    <span className="pill-metric accent">
                      {c.yolo_delay_ms.toFixed(1)} ms
                    </span>
                  </td>
                  <td className="mono">{c.e2e_delay_ms.toFixed(1)} ms</td>
                  <td>
                    <span className="pill-metric">
                      {c.detections_count} objs
                    </span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: "6px" }}>
                      <a
                        href={c.snapshot_path}
                        target="_blank"
                        rel="noreferrer"
                        className="cam-action-btn"
                        style={{ padding: "2px 6px" }}
                      >
                        Snapshot
                      </a>
                      <a
                        href={c.hls_path}
                        target="_blank"
                        rel="noreferrer"
                        className="cam-action-btn"
                        style={{ padding: "2px 6px" }}
                      >
                        HLS
                      </a>
                    </div>
                  </td>
                </tr>
              ))}
              {clients.length === 0 && (
                <tr>
                  <td colSpan={10} style={{ textAlign: "center", padding: "28px", color: "var(--text-faint)" }}>
                    No client camera feeds currently connected. Ingest RTSP stream to port <code>:{st?.rtsp_port ?? 8554}</code>.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
