import React, { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import type { OttChannel } from "../lib/api";

interface TheaterViewProps {
  channels: OttChannel[];
  selectedChannelId: string;
  onSelectChannel: (channelId: string) => void;
}

export default function TheaterView({ channels, selectedChannelId, onSelectChannel }: TheaterViewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [timeStr, setTimeStr] = useState("");

  const activeChannel = channels.find((c) => c.id === selectedChannelId) || channels[0];

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
    if (!el || !activeChannel?.hls_path) return;
    const src = activeChannel.hls_path;
    let hls: Hls | null = null;

    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = src;
      el.play().catch(() => {});
      setIsPlaying(true);
    } else if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        maxLiveSyncPlaybackRate: 1.5,
        manifestLoadingTimeOut: 4000,
        enableWorker: true,
      });
      hls.loadSource(src);
      hls.attachMedia(el);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        el.play().catch(() => {});
      });
      hls.on(Hls.Events.FRAG_BUFFERED, () => {
        setIsPlaying(true);
        el.play().catch(() => {});
      });
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              hls?.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              hls?.recoverMediaError();
              break;
            default:
              setIsPlaying(false);
              hls?.destroy();
              break;
          }
        }
      });
    }
    return () => {
      hls?.destroy();
    };
  }, [activeChannel?.hls_path]);

  return (
    <div className="theater-container">
      {/* 1. Main Video Stage */}
      <div className="theater-stage">
        <video ref={videoRef} className="theater-video" autoPlay muted playsInline controls />

        <div className="theater-hud">
          <div className="hud-pill">
            <span style={{ color: "var(--accent-cyan)", fontWeight: 700 }}>
              {activeChannel?.id.toUpperCase()}
            </span>
            <span>{activeChannel?.name}</span>
          </div>

          <div style={{ display: "flex", gap: "8px" }}>
            <div className="hud-pill">
              <span>{activeChannel?.bitrate_kbps} kbps</span>
            </div>
            <div className="hud-pill">
              <span>{timeStr}</span>
            </div>
          </div>
        </div>
      </div>

      {/* 2. Channel Switcher Sidebar */}
      <div className="theater-sidebar">
        <div className="sidebar-title">Channels ({channels.length})</div>
        {channels.map((ch) => {
          const isSelected = ch.id === selectedChannelId;
          return (
            <div
              key={ch.id}
              className={`channel-card-mini ${isSelected ? "active" : ""}`}
              onClick={() => onSelectChannel(ch.id)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="ch-name">{ch.name}</span>
                <span style={{ fontSize: "10px", background: "rgba(255,255,255,0.1)", padding: "2px 6px", borderRadius: "4px" }}>
                  {ch.source_type.toUpperCase()}
                </span>
              </div>
              <span className="ch-cat">{ch.category}</span>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px" }}>
                {ch.description || ch.source_url}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
