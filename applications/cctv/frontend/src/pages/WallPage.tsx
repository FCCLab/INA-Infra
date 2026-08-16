import { useEffect, useMemo, useRef, useState } from "react";
import Hls from "hls.js";
import Card from "../components/ui/Card";
import KpiStrip from "../components/ui/KpiStrip";
import SectionLabel from "../components/ui/SectionLabel";
import StatusDot from "../components/ui/StatusDot";
import { fetchClients, type CctvClient } from "../lib/api";

function CameraTile({ cam }: { cam: CctvClient }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [hlsOk, setHlsOk] = useState(false);
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
    const el = videoRef.current;
    if (!el || !cam.hls_path) return;
    const src = cam.hls_path;
    let hls: Hls | null = null;

    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = src;
      setHlsOk(true);
    } else if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        liveSyncDurationCount: 2,
        maxLiveSyncPlaybackRate: 1.5,
      });
      hls.loadSource(src);
      hls.attachMedia(el);
      hls.on(Hls.Events.MANIFEST_PARSED, () => setHlsOk(true));
      hls.on(Hls.Events.ERROR, () => setHlsOk(false));
    }
    return () => {
      hls?.destroy();
    };
  }, [cam.hls_path]);

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

  return (
    <div className={`cam-card ${cam.active ? "is-live" : ""}`}>
      <div className="cam-card-topbar">
        <div className="cam-meta-left">
          <StatusDot state={cam.active ? "ok" : "warn"} />
          <span className="cam-title">{cam.name || cam.id}</span>
          <span className="cam-id-pill">{cam.id}</span>
        </div>
        <div className="cam-meta-right">
          <span className={`cam-badge ${cam.active ? "cam-badge-live" : "cam-badge-idle"}`}>
            {cam.active ? `${cam.fps.toFixed(1)} FPS` : "OFFLINE"}
          </span>
          <span className="cam-badge cam-badge-idle">
            {hlsOk ? "HLS Live" : "MJPEG"}
          </span>
        </div>
      </div>

      <div className="cam-stage">
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          style={{ display: hlsOk ? "block" : "none" }}
        />
        {!hlsOk && cam.has_frame && (
          <img src={cam.mjpeg_path} alt={cam.name} />
        )}
        {!cam.active && !cam.has_frame && !hlsOk && (
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
              <span className="cam-hud-tag">
                YOLO {cam.yolo_delay_ms.toFixed(1)}ms
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="cam-hud">
        <div className="cam-hud-stat">
          <span className="cam-hud-stat-label">Inference</span>
          <span className="cam-hud-stat-val accent">{cam.yolo_delay_ms.toFixed(1)} <small>ms</small></span>
        </div>
        <div className="cam-hud-stat">
          <span className="cam-hud-stat-label">E2E Delay</span>
          <span className="cam-hud-stat-val">{cam.e2e_delay_ms.toFixed(1)} <small>ms</small></span>
        </div>
        <div className="cam-hud-stat">
          <span className="cam-hud-stat-label">Net Delay</span>
          <span className="cam-hud-stat-val">{cam.net_delay_ms.toFixed(1)} <small>ms</small></span>
        </div>
        <div className="cam-hud-stat">
          <span className="cam-hud-stat-label">Objects</span>
          <span className="cam-hud-stat-val accent">{cam.detections_count}</span>
        </div>
      </div>

      <div className="cam-foot-actions">
        <a
          href={cam.snapshot_path}
          target="_blank"
          rel="noreferrer"
          className="cam-action-btn"
          title="Download full resolution JPEG snapshot"
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <path d="M21 15l-5-5L5 21" />
          </svg>
          Snapshot
        </a>
        <a
          href={cam.hls_path}
          target="_blank"
          rel="noreferrer"
          className="cam-action-btn"
          title="Open MediaMTX HLS playlist"
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
          </svg>
          HLS Stream
        </a>
      </div>
    </div>
  );
}

export default function WallPage() {
  const [clients, setClients] = useState<CctvClient[]>([]);
  const [cols, setCols] = useState(0);

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

  const live = clients.filter((c) => c.active || c.has_frame);
  const activeCount = clients.filter((c) => c.active).length;
  const totalDetections = clients.reduce((sum, c) => sum + (c.active ? c.detections_count : 0), 0);
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

  const autoCols = live.length <= 1 ? 1 : live.length <= 4 ? 2 : 3;
  const grid = cols || autoCols;
  const shown = useMemo(() => (live.length ? live : clients), [live, clients]);

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
          {
            label: "Avg YOLO Latency",
            value: avgYolo,
            unit: "ms",
            kicker: "Inference time / frame",
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
            kicker: "Capture → Infer → Subscribe",
            icon: (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 6v6l4 2" />
              </svg>
            ),
          },
          {
            label: "Ingest & PubSub",
            value: ":8554",
            unit: "RTSP",
            kicker: "MediaMTX HLS / WHEP",
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

      <Card className="tier" glow>
        <div className="cctv-wall-header">
          <SectionLabel kicker={`${shown.length} camera feed${shown.length === 1 ? "" : "s"} · MediaMTX HLS subscribe`}>
            Multi-Camera Video Wall
          </SectionLabel>
          <div className="actions">
            <button
              type="button"
              className={cols === 0 ? "is-selected" : ""}
              onClick={() => setCols(0)}
              title="Automatic responsive column grid"
            >
              Auto
            </button>
            <button
              type="button"
              className={cols === 1 ? "is-selected" : ""}
              onClick={() => setCols(1)}
              title="1 Column single view"
            >
              1
            </button>
            <button
              type="button"
              className={cols === 2 ? "is-selected" : ""}
              onClick={() => setCols(2)}
              title="2 Columns side-by-side"
            >
              2
            </button>
            <button
              type="button"
              className={cols === 3 ? "is-selected" : ""}
              onClick={() => setCols(3)}
              title="3 Columns grid"
            >
              3
            </button>
            <button
              type="button"
              className={cols === 4 ? "is-selected" : ""}
              onClick={() => setCols(4)}
              title="4 Columns compact grid"
            >
              4
            </button>
          </div>
        </div>

        <div className="cam-wall" data-cols={grid}>
          {shown.length === 0 ? (
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
            shown.map((cam) => <CameraTile key={cam.id} cam={cam} />)
          )}
        </div>
      </Card>
    </>
  );
}
