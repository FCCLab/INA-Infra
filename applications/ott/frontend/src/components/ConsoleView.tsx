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
          <div className="kicker" style={{ fontSize: "11px", letterSpacing: ".12em", textTransform: "uppercase", color: "var(--accent2)", fontWeight: 600 }}>
            Slice 3 · 5G Connected UEs
          </div>
          <h2 style={{ fontSize: "18px", fontWeight: 700, color: "#fff", fontFamily: "var(--font-heading)" }}>
            Connected UEs
          </h2>
          <p style={{ fontSize: "13px", color: "var(--text-dim)" }}>
            Heartbeating UEs appear here with HTTPS console links. Start/Stop and channel changes drive
            each UE Chromium player (YouTube via 5G PDU SOCKS).
          </p>
        </div>
      </div>

      <div className="console-grid">
        {clients.map((cl) => {
          const isStreaming = cl.state === "STREAMING";
          const consoleUrl =
            cl.console_url ||
            (cl.console_ip ? `https://${cl.console_ip}/` : "https://10.1.137.240/");

          return (
            <div key={cl.id} className="console-card">
              {/* 1. Identity */}
              <div className="ue-identity">
                <div className="ue-avatar">{cl.id.toUpperCase().slice(0, 3)}</div>
                <div className="ue-details">
                  <span className="ue-title">{cl.name}</span>
                  <span className="ue-ip mono">
                    Console: {cl.console_ip || cl.ip}
                  </span>
                  {cl.console_mac && (
                    <span className="ue-ip mono" style={{ fontSize: "10px" }}>
                      MAC: {cl.console_mac}
                    </span>
                  )}
                  {cl.pdu_ip && (
                    <span className="ue-ip mono" style={{ fontSize: "10px", color: "var(--accent)" }}>
                      PDU: {cl.pdu_ip} ({cl.pdu_iface || "oaitun_ue3"})
                    </span>
                  )}
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
                <span style={{ fontSize: "11px", color: "var(--text-faint)", textTransform: "uppercase" }}>
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
                  <span className="val">{isStreaming ? `${cl.rx_fps.toFixed(0)}` : "0"}</span>
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

              {/* 5. Independent Action Buttons & Direct Link to UE Console */}
              <div className="ue-action-buttons">
                {isStreaming ? (
                  <button
                    type="button"
                    className="btn-ctrl btn-stop"
                    onClick={() => onStopClient(cl.id)}
                    title="Stop video downlink reception on this UE"
                  >
                    ⏹ Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-ctrl btn-start"
                    onClick={() => onStartClient(cl.id)}
                    title="Start video downlink reception on this UE"
                  >
                    ▶ Start
                  </button>
                )}

                <a
                  href={consoleUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-console-link"
                  title={`Open dedicated UE Console for ${cl.name}`}
                >
                  <span>UE Console ↗</span>
                </a>
              </div>
            </div>
          );
        })}

        {clients.length === 0 && (
          <div style={{ padding: "40px", textAlign: "center", color: "var(--text-dim)" }}>
            No connected UEs yet. Deploy slice-3 UE pods; they register via heartbeat and list their console URL here.
          </div>
        )}
      </div>
    </div>
  );
}
