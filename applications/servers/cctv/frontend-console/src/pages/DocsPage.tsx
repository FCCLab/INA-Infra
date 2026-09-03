import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

function DocTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | React.ReactNode)[][];
}) {
  return (
    <div className="table-wrap" style={{ marginTop: 10 }}>
      <table className="slice-table docs-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DocsPage() {
  return (
    <div className="page-layout docs-layout">
      <div className="page docs-page">
        <Card className="tier" glow>
          <SectionLabel kicker="Reference Architecture">CCTV Vision AI · Slice A Server</SectionLabel>
          <p className="docs-lead" style={{ marginTop: 8 }}>
            The CCTV Analyzer acts as the high-performance edge vision gateway in the NeuroRAN architecture.
            Edge camera UEs push raw H.264/H.265 video via RTSP RECORD into the GStreamer ingest plane.
            Dedicated YOLO inference workers annotate frames with bounding boxes and publish the annotated streams
            into MediaMTX for low-latency HLS and WHEP web distribution.
          </p>
        </Card>

        <Card className="tier">
          <SectionLabel kicker="Data Flow & Pipeline">Streaming Pipeline Architecture</SectionLabel>
          <div className="code-snippet-box" style={{ marginTop: 10 }}>
            <pre>{`[ Edge Camera / UE Publisher ]
              │  RTSP RECORD (:8554/slicea or :8554/ue1) [RTP NTP-64 Timestamped]
              ▼
[ GStreamer RTSP Ingest Server ] ──▶ [ Appsink Buffer & Queue ]
                                               │
                                               ▼
                                 [ Dedicated YOLO Worker Process ]
                                 (PyTorch / TensorRT / CUDA / CPU)
                                               │
                                 Annotated Video + Bounding Boxes
                                               │
                                               ▼
[ MediaMTX Gateway (:8555) ] ──▶ HLS (:8888 -> /live/*)
                               ──▶ WHEP WebRTC (:8889 -> /whep/*)
                               ──▶ Prometheus Metrics (:9102)`}</pre>
          </div>
        </Card>

        <Card className="tier">
          <SectionLabel kicker="REST API Endpoints">HTTP & Media API Reference</SectionLabel>
          <p className="docs-lead" style={{ marginTop: 8 }}>
            Interactive OpenAPI documentation is available via the <a href="/docs" target="_blank" rel="noreferrer" className="mono" style={{ color: "var(--accent)" }}>/docs</a> Swagger endpoint.
          </p>
          <DocTable
            headers={["Method", "Endpoint", "Type", "Description"]}
            rows={[
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/health</code>, "JSON", "System health, analyzer workers, MediaMTX status"],
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/status</code>, "JSON", "Server configuration, YOLO engine specs, and registered clients"],
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/clients</code>, "JSON", "List of all active/idle camera streams and subscribe URLs"],
              [<span className="docs-badge-get">GET</span>, <code>/live/{'{path}'}</code>, "HLS", "Low-latency HLS playlist proxy to MediaMTX"],
              [<span className="docs-badge-post">POST</span>, <code>/whep/{'{path}'}</code>, "WHEP", "WebRTC WHEP signaling proxy for sub-second playback"],
              [<span className="docs-badge-get">GET</span>, <code>/snapshot/{'{id}'}</code>, "JPEG", "Instant high-resolution frame capture"],
              [<span className="docs-badge-get">GET</span>, <code>/video/{'{id}'}</code>, "MJPEG", "MJPEG multipart streaming fallback"],
              [<span className="docs-badge-get">GET</span>, <code>:9102/metrics</code>, "Prometheus", "End-to-end latency, YOLO inference delay, network jitter"],
            ]}
          />
        </Card>

        <Card className="tier">
          <SectionLabel kicker="Network & Container Ports">Port Allocation</SectionLabel>
          <DocTable
            headers={["Port", "Protocol", "Component", "Function"]}
            rows={[
              [<code>8554</code>, "TCP / UDP", "GStreamer RTSP Server", "Ingest from UE publishers (RTSP RECORD)"],
              [<code>8080</code>, "HTTP / TCP", "FastAPI & React SPA", "Console web UI and REST control plane"],
              [<code>8555</code>, "TCP", "MediaMTX Internal", "Inference worker annotated RTSP publish"],
              [<code>8888</code>, "HTTP / TCP", "MediaMTX HLS", "HLS streaming engine (proxied at /live)"],
              [<code>8889</code>, "HTTP / TCP", "MediaMTX WHEP", "WebRTC WHEP gateway (proxied at /whep)"],
              [<code>9102</code>, "HTTP / TCP", "Prometheus Exporter", "Application and latency metrics exporter"],
            ]}
          />
        </Card>

        <Card className="tier">
          <SectionLabel kicker="Testing & Stream Simulation">Sample Publisher Commands</SectionLabel>
          <p className="docs-lead" style={{ marginTop: 8 }}>
            Push a test live camera feed to the server using GStreamer:
          </p>
          <div className="code-snippet-box">
            <pre>gst-launch-1.0 videotestsrc is-live=true pattern=ball ! video/x-raw,framerate=25/1,width=1280,height=720 ! timeoverlay ! x264enc tune=zerolatency bitrate=4000 ! rtph264pay pt=96 ! rtspclientsink location=rtsp://&lt;SERVER_IP&gt;:8554/slicea protocols=tcp</pre>
          </div>
          <p className="docs-lead" style={{ marginTop: 14 }}>
            Or stream an MP4 video file continuously in loop using FFmpeg:
          </p>
          <div className="code-snippet-box">
            <pre>ffmpeg -re -stream_loop -1 -i sample.mp4 -c:v copy -f rtsp -rtsp_transport tcp rtsp://&lt;SERVER_IP&gt;:8554/slicea</pre>
          </div>
        </Card>
      </div>
    </div>
  );
}
