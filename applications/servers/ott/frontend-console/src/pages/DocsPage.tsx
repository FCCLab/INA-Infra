import type { ReactNode } from "react";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

function DocTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | ReactNode)[][];
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
          <SectionLabel kicker="Reference Architecture">OTT Stream Portal · Slice 3</SectionLabel>
          <p className="docs-lead" style={{ marginTop: 8 }}>
            Edge UEs pull media over the 5G PDU session (Chromium → SOCKS → air). This portal
            assigns channels and starts / stops UE players.
          </p>
        </Card>

        <Card className="tier">
          <SectionLabel kicker="REST API Endpoints">HTTP &amp; Media API Reference</SectionLabel>
          <p className="docs-lead" style={{ marginTop: 8 }}>
            Interactive OpenAPI is at{" "}
            <a href="/docs" target="_blank" rel="noreferrer" className="mono" style={{ color: "var(--accent)" }}>
              /docs
            </a>
            .
          </p>
          <DocTable
            headers={["Method", "Endpoint", "Type", "Description"]}
            rows={[
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/health</code>, "JSON", "Process health"],
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/status</code>, "JSON", "Videos, connected UEs, downlink Mbps"],
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/videos</code>, "JSON", "Video catalog (YouTube IDs + optional MediaMTX URLs)"],
              [
                <span className="docs-badge-get">GET</span>,
                <code>/api/v1/videos/{"{id}"}/mediamtx</code>,
                "JSON",
                "Legacy MediaMTX links (when OTT_PLAY_MODE=mediamtx)",
              ],
              [<span className="docs-badge-get">GET</span>, <code>/api/v1/clients</code>, "JSON", "Connected UEs with HTTPS console links"],
              [
                <span className="docs-badge-post">POST</span>,
                <code>/api/v1/clients/{"{id}"}/select</code>,
                "JSON",
                "Assign channel → YouTube play metadata for UE Chromium",
              ],
              [
                <span className="docs-badge-post">POST</span>,
                <code>/api/v1/clients/{"{id}"}/start</code>,
                "JSON",
                "Portal Start → UE Chromium navigates / plays",
              ],
              [
                <span className="docs-badge-post">POST</span>,
                <code>/api/v1/clients/{"{id}"}/stop</code>,
                "JSON",
                "Portal Stop → UE Chromium blanked",
              ],
              [
                <span className="docs-badge-post">POST</span>,
                <code>/api/v1/clients/heartbeat</code>,
                "JSON",
                "UE registers / telemetry; returns state + assigned channel",
              ],
            ]}
          />
        </Card>

        <Card className="tier">
          <SectionLabel kicker="Network &amp; Ports">Port Allocation</SectionLabel>
          <DocTable
            headers={["Port", "Protocol", "Component", "Function"]}
            rows={[
              [<code>80</code>, "HTTP", "Nginx + React portal", "This console"],
              [<code>8080</code>, "HTTP", "FastAPI", "REST control plane"],
              [<code>443</code>, "HTTPS", "UE console + Selkies", "Per-UE Multus console"],
              [<code>8554</code>, "RTSP", "MediaMTX (optional)", "Legacy ingest"],
              [<code>8888</code>, "HTTP", "MediaMTX HLS (optional)", "Legacy HLS"],
              [<code>9103</code>, "HTTP", "Prometheus", "OTT metrics"],
            ]}
          />
        </Card>
      </div>
    </div>
  );
}
