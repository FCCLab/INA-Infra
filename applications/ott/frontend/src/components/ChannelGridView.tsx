import React, { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import type { OttChannel } from "../lib/api";

function GridTile({ channel }: { channel: OttChannel }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [inView, setInView] = useState(true);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        setInView(entry.isIntersecting);
      },
      { threshold: 0.05 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!inView || !channel.hls_path) return;
    const el = videoRef.current;
    if (!el) return;
    const src = channel.hls_path;
    let hls: Hls | null = null;

    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = src;
      el.play().catch(() => {});
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
    }
    return () => {
      hls?.destroy();
    };
  }, [channel.hls_path, inView]);

  return (
    <div ref={containerRef} className="grid-card">
      <video ref={videoRef} autoPlay muted playsInline />
      <div className="grid-card-overlay">
        <span style={{ fontSize: "13px", fontWeight: 700, color: "#fff" }}>
          {channel.id.toUpperCase()}: {channel.name}
        </span>
        <span style={{ fontSize: "11px", color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
          {channel.fps.toFixed(0)} FPS
        </span>
      </div>
    </div>
  );
}

export default function ChannelGridView({ channels }: { channels: OttChannel[] }) {
  return (
    <div className="grid-container">
      {channels.map((ch) => (
        <GridTile key={ch.id} channel={ch} />
      ))}
    </div>
  );
}
