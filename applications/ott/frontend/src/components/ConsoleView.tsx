import React from "react";
import type { ConnectedClient, OttChannel } from "../lib/api";

interface ConsoleViewProps {
  clients: ConnectedClient[];
  channels: OttChannel[];
  onStartClient: (clientId: string) => void;
  onStopClient: (clientId: string) => void;
  onSetChannel: (clientId: string, channelId: string) => void;
}

export default function ConsoleView({
  clients,
  channels,
  onStartClient,
  onStopClient,
  onSetChannel,
}: ConsoleViewProps) {
  return (
    <div className="console-view">
      <div style={{ marginBottom: "20px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "18px", fontWeight: 700, color: "#fff" }}>5G UE Reception Console</h2>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
            Independently manage and stream multi-channel YouTube/HD video downlink to each connected UE.
          </p>
        </div>
      </div>

      <div className="console-grid">
        {clients.map((cl) => {
          const isStreaming = cl.state === "STREAMING";
          const currentCh = channels.find((ch) => ch.id === cl.assigned_channel);

          return (
            <div key={cl.id} className="console-card">
              {/* 1. Identity */}
              <div className="ue-identity">
                <div className="ue-avatar">{cl.id.toUpperCase().slice(0, 3)}</div>
                <div className="ue-details">
                  <span className="ue-title">{cl.name}</span>
                  <span className="ue-ip">{cl.ip}</span>
                </div>
              </div>

              {/* 2. Status Badge */}
              <div>
                <span className={`status-badge ${isStreaming ? "streaming" : "stopped"}`}>
                  <span className="status-pulse" />
                  {cl.state}
                </span>
              </div>

              {/* 3. Channel Selector */}
              <div className="channel-selector-wrap">
                <span style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase" }}>
                  Assigned Channel
                </span>
                <select
                  className="channel-select"
                  value={cl.assigned_channel}
                  onChange={(e) => onSetChannel(cl.id, e.target.value)}
                >
                  {channels.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {ch.id.toUpperCase()}: {ch.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* 4. Live 5G Downlink Telemetry */}
              <div className="telemetry-strip">
                <div className="telemetry-chip" title="Downlink Network Transit Latency">
                  <span className="lbl">DL Latency</span>
                  <span className="val">{isStreaming ? `${cl.net_delay_ms.toFixed(1)} ms` : "—"}</span>
                </div>
                <div className="telemetry-chip" title="Received Frames Per Second">
                  <span className="lbl">RX FPS</span>
                  <span className="val">{isStreaming ? `${cl.rx_fps.toFixed(1)}` : "0.0"}</span>
                </div>
                <div className="telemetry-chip" title="Downlink Throughput">
                  <span className="lbl">Bitrate</span>
                  <span className="val">{isStreaming ? `${cl.rx_bitrate_mbps.toFixed(2)} M` : "0.00 M"}</span>
                </div>
                <div className="telemetry-chip" title="Dropped Frames">
                  <span className="lbl">Drops</span>
                  <span className="val">{cl.dropped_frames}</span>
                </div>
              </div>

              {/* 5. Independent Action Buttons */}
              <div className="ue-action-buttons">
                {isStreaming ? (
                  <button
                    type="button"
                    className="btn-ctrl btn-stop"
                    onClick={() => onStopClient(cl.id)}
                    title="Stop video downlink reception on this UE"
                  >
                    ⏹ Stop Stream
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-ctrl btn-start"
                    onClick={() => onStartClient(cl.id)}
                    title="Start video downlink reception on this UE"
                  >
                    ▶ Start Stream
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {clients.length === 0 && (
          <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)" }}>
            Waiting for 5G UE clients to connect...
          </div>
        )}
      </div>
    </div>
  );
}
