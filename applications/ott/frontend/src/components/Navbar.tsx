import React from "react";

interface NavbarProps {
  currentTab: "console" | "theater" | "grid";
  setCurrentTab: (tab: "console" | "theater" | "grid") => void;
  onOpenAddStream: () => void;
  streamingCount: number;
  totalClients: number;
  totalThroughput: number;
}

export default function Navbar({
  currentTab,
  setCurrentTab,
  onOpenAddStream,
  streamingCount,
  totalClients,
  totalThroughput,
}: NavbarProps) {
  return (
    <header className="ott-navbar">
      <div className="nav-brand">
        <span className="brand-badge">SLICE 3 (OTT)</span>
        <div className="brand-title">
          <span>VisionStream Portal</span>
        </div>
      </div>

      <nav className="nav-tabs">
        <button
          type="button"
          className={`nav-tab-btn ${currentTab === "console" ? "active" : ""}`}
          onClick={() => setCurrentTab("console")}
        >
          <span>🎮 UE Console</span>
        </button>
        <button
          type="button"
          className={`nav-tab-btn ${currentTab === "theater" ? "active" : ""}`}
          onClick={() => setCurrentTab("theater")}
        >
          <span>📺 Theater Player</span>
        </button>
        <button
          type="button"
          className={`nav-tab-btn ${currentTab === "grid" ? "active" : ""}`}
          onClick={() => setCurrentTab("grid")}
        >
          <span>🎛️ Video Wall</span>
        </button>
      </nav>

      <div className="nav-actions">
        <button type="button" className="btn-add-stream" onClick={onOpenAddStream}>
          <span>+ Add YouTube Stream</span>
        </button>
      </div>
    </header>
  );
}
