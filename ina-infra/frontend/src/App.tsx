import { useState } from "react";
import PlanningPage from "./pages/PlanningPage";

type Tab = "planning" | "medium" | "short";

export default function App() {
  const [tab, setTab] = useState<Tab>("planning");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">INA</span>
          <div>
            <h1>INA-Infra</h1>
            <p>Multi-timescale slice planning · Planning Layer</p>
          </div>
        </div>
        <nav className="tabs">
          <button
            className={tab === "planning" ? "active" : ""}
            onClick={() => setTab("planning")}
          >
            Planning (PL)
          </button>
          <button className="disabled" disabled title="Coming later">
            Medium (PM)
          </button>
          <button className="disabled" disabled title="Coming later">
            Short (PS)
          </button>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
            Swagger
          </a>
        </nav>
      </header>
      <main>
        {tab === "planning" && <PlanningPage />}
      </main>
    </div>
  );
}
