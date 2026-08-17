import { useEffect, useMemo, useRef, useState } from "react";
import Hls from "hls.js";
import Card from "../components/ui/Card";
import KpiStrip from "../components/ui/KpiStrip";
import StatusDot from "../components/ui/StatusDot";
import { fetchClients, formatCameraName, type CctvClient } from "../lib/api";

function CameraTile({ cam }: { cam: CctvClient }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [streamMode, setStreamMode] = useState<"mjpeg" | "hls">("hls");
  const [hlsPlaying, setHlsPlaying] = useState(false);
  const [timeStr, setTimeStr] = useState("");

  useEffect(() => {
    const updateClock = () => {
      const d = new Date();
      setTimeStr(d.toLocaleTimeString("en-GB", { hour12: false }));
    };
    updateClock();
    const id = setInterval(updateClock, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (streamMode !== "hls") {
      setHlsPlaying(false);
      return;
    }
    const el = videoRef.current;
    if (!el || !cam.hls_path) return;
    const src = cam.hls_path;
    let hls: Hls | null = null;

    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = src;
      el.play().catch(() => {});
      setHlsPlaying(true);
    } else if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        maxLiveSyncPlaybackRate: 1.5,
      });
      hls.loadSource(src);
      hls.attachMedia(el);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        el.play().catch(() => {});
      });
      hls.on(Hls.Events.FRAG_BUFFERED, () => {
        setHlsPlaying(true);
        el.play().catch(() => {});
      });
      hls.on(Hls.Events.ERROR, () => {
        setHlsPlaying(false);
      });
    }
    return () => {
      hls?.destroy();
    };
  }, [cam.hls_path, streamMode]);

  const detSummary = useMemo(() => {
    if (!cam.detected_objects || cam.detected_objects.length === 0) {
      return cam.detections_count > 0 ? [`${cam.detections_count} objects`] : [];
    }
    const counts: Record<string, number> = {};
    for (const obj of cam.detected_objects) {
      counts[obj] = (counts[obj] || 0) + 1;
    }
    return Object.entries(counts).map(([name, count]) => `${name} × ${count}`);
  }, [cam.detected_objects, cam.detections_count]);

  const isHlsActive = streamMode === "hls" && hlsPlaying;

  return (
    <div className={`cam-card ${cam.active ? "is-live" : ""}`}>
      <div className="cam-card-topbar">
        <div className="cam-meta-left">
          <StatusDot state={cam.active ? "ok" : "warn"} />
          <span className="cam-title">{formatCameraName(cam.id, cam.name)}</span>
          <span className="cam-id-pill">{cam.id}</span>
        </div>
        <div className="cam-meta-right">
          <span className={`cam-badge ${cam.active ? "cam-badge-live" : "cam-badge-idle"}`}>
            {cam.active ? `${cam.fps.toFixed(1)} FPS` : "OFFLINE"}
          </span>
          <button
            type="button"
            className={`cam-badge ${streamMode === "hls" ? "cam-badge-live" : "cam-badge-idle"}`}
            style={{ cursor: "pointer", border: "none" }}
            onClick={() => setStreamMode((prev) => (prev === "hls" ? "mjpeg" : "hls"))}
            title="Click to toggle between MediaMTX Pub/Sub (HLS) and Direct MJPEG"
          >
            {streamMode === "hls" ? "MediaMTX (HLS)" : "MJPEG (Direct)"}
          </button>
        </div>
      </div>

      <div className="cam-stage">
        {/* HLS Video Player */}
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          style={{ display: isHlsActive ? "block" : "none" }}
        />

        {/* Instant MJPEG / Snapshot Stream */}
        {!isHlsActive && (
          <img
            src={cam.mjpeg_path}
            alt={cam.name || cam.id}
            style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
            onError={(e) => {
              const target = e.currentTarget;
              target.src = `${cam.snapshot_path}?t=${Date.now()}`;
            }}
          />
        )}
        {!cam.active && !cam.has_frame && !isHlsActive && (
          <div className="cam-offline">
            <svg className="cam-offline-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M4 6h11a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z" />
            </svg>
            <span>Waiting for RTSP publisher on :8554/{cam.id}…</span>
          </div>
        )}

        <div className="cam-hud-overlay">
          <div className="cam-hud-top">
            {timeStr && <span className="cam-hud-tag">{timeStr}</span>}
            {cam.active && (
              <>
                <span className="cam-hud-tag" title="Network Transit Delay">
                  Net {cam.net_delay_ms.toFixed(1)}ms
                </span>
                <span className="cam-hud-tag" title="YOLO Inference Latency">
                  Infer {cam.yolo_delay_ms.toFixed(1)}ms
                </span>
                <span className="cam-hud-tag" title="Total End-to-End Latency">
                  E2E {cam.e2e_delay_ms.toFixed(1)}ms
                </span>
              </>
            )}
          </div>
          <div className="cam-hud-bottom">
            <div className="cam-detections-list">
              {detSummary.map((det, idx) => (
                <span key={idx} className="cam-det-chip">
                  {det}
                </span>
              ))}
            </div>
            {cam.active && (
              <span className="cam-hud-tag" style={{ background: "rgba(22, 230, 160, 0.25)", borderColor: "var(--accent)" }}>
                Obj: {cam.detections_count}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="cam-card-foot">
        <div className="cam-foot-stats">
          <span className="cam-mini-stat" title="Network Transit Delay">
            <span className="lbl">Net</span> <strong>{cam.net_delay_ms.toFixed(1)}ms</strong>
          </span>
          <span className="cam-mini-stat" title="YOLO Inference Latency">
            <span className="lbl">Infer</span> <strong>{cam.yolo_delay_ms.toFixed(1)}ms</strong>
          </span>
          <span className="cam-mini-stat" title="Total End-to-End Delay">
            <span className="lbl">E2E</span> <strong>{cam.e2e_delay_ms.toFixed(1)}ms</strong>
          </span>
          <span className="cam-mini-stat" title="Detected Objects Count">
            <span className="lbl">Obj</span> <strong>{cam.detections_count}</strong>
          </span>
        </div>

        <div className="cam-foot-links">
          <a
            href={cam.snapshot_path}
            target="_blank"
            rel="noreferrer"
            className="cam-mini-btn"
            title="Download full resolution JPEG snapshot"
          >
            Snap
          </a>
          <a
            href={cam.hls_path}
            target="_blank"
            rel="noreferrer"
            className="cam-mini-btn"
            title="Open MediaMTX HLS playlist"
          >
            HLS
          </a>
        </div>
      </div>
    </div>
  );
}

type LayoutPattern = "auto" | "1" | "2x2" | "3x3" | "4x4";
type SortOption = "default" | "active" | "detections" | "name";

export default function WallPage() {
  const [clients, setClients] = useState<CctvClient[]>([]);
  const [layout, setLayout] = useState<LayoutPattern>("auto");
  const [selectedCamId, setSelectedCamId] = useState<string>("all");
  const [activeOnly, setActiveOnly] = useState<boolean>(true);
  const [sortBy, setSortBy] = useState<SortOption>("default");

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const next = await fetchClients();
        if (!stop) setClients(next);
      } catch {
        /* keep last */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  const liveFeeds = useMemo(() => clients.filter((c) => c.active || c.has_frame), [clients]);
  const activeCount = clients.filter((c) => c.active).length;
  const totalDetections = clients.reduce((sum, c) => sum + (c.active ? c.detections_count : 0), 0);

  const avgNet = useMemo(() => {
    const activeFeeds = clients.filter((c) => c.active && c.net_delay_ms > 0);
    if (!activeFeeds.length) return "0.0";
    const sum = activeFeeds.reduce((acc, c) => acc + c.net_delay_ms, 0);
    return (sum / activeFeeds.length).toFixed(1);
  }, [clients]);

  const avgYolo = useMemo(() => {
    const activeFeeds = clients.filter((c) => c.active && c.yolo_delay_ms > 0);
    if (!activeFeeds.length) return "0.0";
    const sum = activeFeeds.reduce((acc, c) => acc + c.yolo_delay_ms, 0);
    return (sum / activeFeeds.length).toFixed(1);
  }, [clients]);

  const avgE2e = useMemo(() => {
    const activeFeeds = clients.filter((c) => c.active && c.e2e_delay_ms > 0);
    if (!activeFeeds.length) return "0.0";
    const sum = activeFeeds.reduce((acc, c) => acc + c.e2e_delay_ms, 0);
    return (sum / activeFeeds.length).toFixed(1);
  }, [clients]);

  // Filter and sort cameras
  const displayedCameras = useMemo(() => {
    let list = activeOnly ? (liveFeeds.length ? liveFeeds : clients) : clients;
    if (layout === "1" && selectedCamId !== "all") {
      const match = list.find((c) => c.id === selectedCamId);
      if (match) list = [match];
    }
    const cloned = [...list];
    if (sortBy === "active") {
      cloned.sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0));
    } else if (sortBy === "detections") {
      cloned.sort((a, b) => b.detections_count - a.detections_count);
    } else if (sortBy === "name") {
      cloned.sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
    }
    return cloned;
  }, [clients, liveFeeds, activeOnly, layout, selectedCamId, sortBy]);

  // Compute effective grid cols attribute
  const effectiveGridCols = useMemo(() => {
    if (layout === "1") return 1;
    if (layout === "2x2") return 2;
    if (layout === "3x3") return 3;
    if (layout === "4x4") return 4;
    return displayedCameras.length <= 1 ? 1 : displayedCameras.length <= 4 ? 2 : 3;
  }, [layout, displayedCameras.length]);

  return (
    <>
      <KpiStrip
        items={[
          {
            label: "Live Video Streams",
            value: String(activeCount),
            kicker: `${clients.length} registered feeds`,
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M4 6h11a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z" />
              </svg>
            ),
          },
          {
            label: "Avg Net Delay",
            value: avgNet,
            unit: "ms",
            kicker: "Network transit delay",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
              </svg>
            ),
          },
          {
            label: "Avg Inference Delay",
            value: avgYolo,
            unit: "ms",
            kicker: "YOLO detection / frame",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            ),
          },
          {
            label: "Avg E2E Delay",
            value: avgE2e,
            unit: "ms",
            kicker: "Total capture → inference → subscribe",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 6v6l4 2" />
              </svg>
            ),
          },
          {
            label: "Total Detections",
            value: String(totalDetections),
            kicker: "Real-time YOLO bounding boxes",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="9" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            ),
          },
        ]}
      />

      <Card className="tier" glow style={{ padding: "6px 10px", flex: 1, minHeight: 0, height: "100%", maxHeight: "100%", display: "flex", flexDirection: "column", boxSizing: "border-box", overflow: "hidden", marginBottom: 0 }}>
        <div className="cctv-wall-header">
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "15px", fontWeight: 700, color: "var(--text)" }}>
              Video Wall
            </span>
            <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "12px", background: "rgba(255, 255, 255, 0.07)", color: "var(--muted)", fontWeight: 500 }}>
              {displayedCameras.length} Feed{displayedCameras.length === 1 ? "" : "s"} · {layout.toUpperCase()}
            </span>
          </div>

          <div className="actions">
            {/* Pattern Chooser: Auto, 1, 2x2, 3x3, 4x4 */}
            <div style={{ display: "inline-flex", background: "rgba(255, 255, 255, 0.05)", borderRadius: "8px", padding: "2px", border: "1px solid var(--border)", flexShrink: 0 }}>
              {(["auto", "1", "2x2", "3x3", "4x4"] as LayoutPattern[]).map((p) => (
                <button
                  key={p}
                  type="button"
                  className={layout === p ? "is-selected" : ""}
                  style={{
                    padding: "3px 9px",
                    height: "24px",
                    fontSize: "11.5px",
                    fontWeight: layout === p ? 700 : 500,
                    borderRadius: "6px",
                    border: "none",
                    background: layout === p ? "var(--accent)" : "transparent",
                    color: layout === p ? "#000" : "var(--text)",
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                    display: "inline-flex",
                    alignItems: "center",
                  }}
                  onClick={() => setLayout(p)}
                  title={`Select ${p.toUpperCase()} Grid Pattern`}
                >
                  {p === "auto" ? "Auto" : p}
                </button>
              ))}
            </div>

            {/* Camera selector when in single view */}
            {layout === "1" && clients.length > 1 && (
              <select
                value={selectedCamId}
                onChange={(e) => setSelectedCamId(e.target.value)}
                style={{ flexShrink: 0 }}
                title="Choose camera to display in Single view"
              >
                <option value="all">First Available</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || c.id} {c.active ? "(Live)" : "(Offline)"}
                  </option>
                ))}
              </select>
            )}

            {/* Reorder / Sort Options */}
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortOption)}
              style={{ flexShrink: 0 }}
              title="Reorder camera feeds"
            >
              <option value="default">Sort: Ingest</option>
              <option value="active">Sort: Active</option>
              <option value="detections">Sort: Detections</option>
              <option value="name">Sort: Name</option>
            </select>

            {/* Filter Inactive Feeds */}
            <button
              type="button"
              className={activeOnly ? "is-selected" : ""}
              onClick={() => setActiveOnly((prev) => !prev)}
              style={{
                height: "28px",
                padding: "2px 10px",
                fontSize: "12px",
                borderRadius: "6px",
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                flexShrink: 0,
              }}
              title="Toggle offline camera feeds"
            >
              {activeOnly ? "✓ Active Only" : "Show All"}
            </button>
          </div>
        </div>

        <div
          className="cam-wall"
          data-cols={effectiveGridCols}
          data-layout={layout}
        >
          {displayedCameras.length === 0 ? (
            <div className="empty-wall-guide">
              <div className="empty-wall-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <path d="M12 16v-4M12 8h.01" />
                </svg>
                No Active Camera Ingest Feeds
              </div>
              <p className="empty-wall-lead">
                The CCTV analyzer server is ready and listening for RTSP RECORD streams on port <code>8554</code>.
                Deploy an Edge UE publisher or push a test video stream using GStreamer:
              </p>
              <div className="code-snippet-box">
                <pre>gst-launch-1.0 videotestsrc is-live=true ! video/x-raw,framerate=25/1,width=1280,height=720 ! x264enc tune=zerolatency bitrate=4000 ! rtph264pay pt=96 ! rtspclientsink location=rtsp://127.0.0.1:8554/slicea protocols=tcp</pre>
              </div>
            </div>
          ) : (
            displayedCameras.map((cam) => <CameraTile key={cam.id} cam={cam} />)
          )}
        </div>
      </Card>
    </>
  );
}
