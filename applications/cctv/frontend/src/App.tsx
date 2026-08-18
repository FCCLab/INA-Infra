import { useMemo, useState, type ReactNode } from "react";
import AppShell, { type NavTab } from "./components/ui/AppShell";
import WallPage from "./pages/WallPage";
import StatusPage from "./pages/StatusPage";
import DocsPage from "./pages/DocsPage";

type Tab = "wall" | "status" | "docs";

const IconWall = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
  </svg>
);

const IconStatus = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M4 19V5M4 19h16" strokeLinecap="round" />
    <path d="M8 16v-5M12 16V8M16 16v-3" strokeLinecap="round" />
  </svg>
);

const IconBook = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const BRAND = {
  product: "NeuroRAN",
  title: "CCTV Vision AI · NeuroRAN",
  shortName: "CCTV",
  logo: "/logos/NeuroRAN.png",
  logoPng: "/logos/NeuroRAN.png",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("wall");

  const tabs: NavTab[] = useMemo(
    () => [
      { id: "wall", label: "Video Wall", icon: IconWall },
      { id: "status", label: "Streams & Stats", icon: IconStatus },
      { id: "docs", label: "Docs & API", icon: IconBook },
    ],
    [],
  );

  const crumb =
    tab === "wall"
      ? "Video Wall · Live Stream Analytics"
      : tab === "status"
        ? "Stream Topology & YOLO Inference"
        : "Documentation & REST / RTSP Reference";

  const body: ReactNode =
    tab === "wall" ? <WallPage /> : tab === "status" ? <StatusPage /> : <DocsPage />;

  return (
    <AppShell
      brand={BRAND}
      tabs={tabs}
      tab={tab}
      setTab={(id) => setTab(id as Tab)}
      crumb={crumb}
      siteLabel="INA · CCTV Vision AI Server"
      topbarExtra={
        <>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer" title="Interactive FastAPI Swagger">
            Swagger API
          </a>
          <a
            className="docs-link"
            href="http://10.1.137.105:3000/d/ina-cctv-metrics/cctv-metrics?orgId=1&refresh=5s"
            target="_blank"
            rel="noreferrer"
            title="Open Grafana CCTV Metrics"
          >
            Grafana
          </a>
        </>
      }
    >
      {body}
    </AppShell>
  );
}

