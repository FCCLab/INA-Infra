import { useMemo, useState, type ReactNode } from "react";
import PlanningPage from "./pages/PlanningPage";
import PlaceholderPage from "./pages/PlaceholderPage";
import AppShell, { type NavTab } from "./components/ui/AppShell";
import { DialogProvider } from "./components/ui/Dialog";

type Tab = "planning" | "medium" | "short";

const IconOverview = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
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
      { id: "medium", label: "Medium", icon: IconClock },
      { id: "short", label: "Short", icon: IconSignal },
    ],
    [],
  );

  const crumb =
    tab === "planning"
      ? "Planning (PL)"
      : tab === "medium"
        ? "Medium (PM)"
        : "Short (PS)";

  let body: ReactNode = null;
  if (tab === "planning") {
    body = <PlanningPage />;
  } else if (tab === "medium") {
    body = (
      <PlaceholderPage
        title="Medium (PM)"
        subtitle="Medium-timescale planning controls will appear here. Status for the active profile stays live on the right."
      />
    );
  } else {
    body = (
      <PlaceholderPage
        title="Short (PS)"
        subtitle="Short-timescale planning controls will appear here. Status for the active profile stays live on the right."
      />
    );
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
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
          Swagger
        </a>
      }
    >
      {/* Inside .app so dialogs inherit theme tokens (opaque --bg1, etc.) */}
      <DialogProvider>{body}</DialogProvider>
    </AppShell>
  );
}
