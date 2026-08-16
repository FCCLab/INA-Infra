import { useMemo, useState, type ReactNode } from "react";
import PlanningPage from "./pages/PlanningPage";
import ApplicationsPage from "./pages/ApplicationsPage";
import MediumPage from "./pages/MediumPage";
import ShortPage from "./pages/ShortPage";
import DocsPage from "./pages/DocsPage";
import OperatorsPage from "./pages/OperatorsPage";
import BenchmarkPage from "./pages/BenchmarkPage";
import AppShell, { type NavTab } from "./components/ui/AppShell";
import { DialogProvider } from "./components/ui/Dialog";

type Tab =
  | "planning"
  | "applications"
  | "medium"
  | "short"
  | "operators"
  | "benchmark"
  | "docs";

const IconOverview = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
  </svg>
);

const IconApps = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="4" width="7" height="7" rx="1.5" />
    <rect x="14" y="4" width="7" height="7" rx="1.5" />
    <rect x="3" y="15" width="7" height="7" rx="1.5" />
    <path d="M17.5 15v7M14 18.5h7" strokeLinecap="round" />
  </svg>
);

const IconClock = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" strokeLinecap="round" />
  </svg>
);

const IconSignal = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <path d="M4 20v-4M10 20v-9M16 20v-14M22 20V9" />
  </svg>
);

const IconBook = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const IconOperator = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="2" y="6" width="20" height="12" rx="3" />
    <path d="M8 12h2M7 10v4M15 11h.01M18 13h.01" strokeLinecap="round" />
  </svg>
);

const IconBenchmark = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M4 19V5M4 19h16" strokeLinecap="round" />
    <path d="M8 16v-5M12 16V8M16 16v-3" strokeLinecap="round" />
  </svg>
);

const BRAND = {
  product: "NeuroRAN",
  title: "INA-Infra · NeuroRAN",
  shortName: "INA",
  logo: "/logos/NeuroRAN.svg",
  logoPng: "/logos/NeuroRAN.png",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("planning");

  const tabs: NavTab[] = useMemo(
    () => [
      { id: "planning", label: "Planning", icon: IconOverview },
      { id: "applications", label: "Applications", icon: IconApps },
      { id: "medium", label: "Medium", icon: IconClock },
      { id: "short", label: "Short", icon: IconSignal },
      { id: "operators", label: "Operators", icon: IconOperator },
      { id: "benchmark", label: "Benchmark", icon: IconBenchmark },
      { id: "docs", label: "Docs", icon: IconBook },
    ],
    [],
  );

  const crumb =
    tab === "planning"
      ? "Planning (PL)"
      : tab === "applications"
        ? "Applications"
        : tab === "medium"
          ? "Medium (PM)"
          : tab === "short"
            ? "Short (PS)"
            : tab === "operators"
              ? "Operator agents"
              : tab === "benchmark"
                ? "oai-benchmark"
                : "Docs";

  let body: ReactNode = null;
  if (tab === "planning") {
    body = <PlanningPage />;
  } else if (tab === "applications") {
    body = <ApplicationsPage />;
  } else if (tab === "medium") {
    body = <MediumPage />;
  } else if (tab === "short") {
    body = <ShortPage />;
  } else if (tab === "operators") {
    body = <OperatorsPage />;
  } else if (tab === "benchmark") {
    body = <BenchmarkPage />;
  } else {
    body = <DocsPage />;
  }

  return (
    <AppShell
      brand={BRAND}
      tabs={tabs}
      tab={tab}
      setTab={(id) => setTab(id as Tab)}
      crumb={crumb}
      siteLabel="INA · Multi-timescale slice planning"
      topbarExtra={
        <>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
            Swagger
          </a>
        </>
      }
    >
      <DialogProvider>{body}</DialogProvider>
    </AppShell>
  );
}
