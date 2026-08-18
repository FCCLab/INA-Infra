import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ClusterSummary } from "../api/client";
import ClusterDetailPanel from "../components/ClusterDetailPanel";
import ClusterTopology from "../components/ClusterTopology";
import KpiStrip from "../components/KpiStrip";
import AppShell, { type NavTab } from "../components/ui/AppShell";

const POLL_MS = 10_000;

const BRAND = {
  product: "NeuroRAN",
  title: "INA-Infra Dashboard",
  shortName: "INA",
  logo: "/logos/NeuroRAN.png",
  logoPng: "/logos/NeuroRAN.png",
};

function TopologyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8 7.5 10.5 16M16 7.5 13.5 16M8.5 6h7" strokeLinecap="round" />
    </svg>
  );
}

export default function DashboardPage() {
  const [clusters, setClusters] = useState<ClusterSummary[]>([]);
  const [selected, setSelected] = useState<string | null>("mgmt");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const [tab, setTab] = useState("topology");

  const tabs: NavTab[] = useMemo(
    () => [{ id: "topology", label: "Topology", icon: <TopologyIcon /> }],
    [],
  );

  const refresh = useCallback(async () => {
    try {
      const res = await api.clusters();
      setClusters(res.clusters);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString());
      setRefreshToken((n) => n + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return (
    <AppShell
      tabs={tabs}
      tab={tab}
      setTab={setTab}
      crumb="INA-Infra Dashboard"
      siteLabel=""
      brand={BRAND}
      topbarExtra={
        <>
          <span className="muted" style={{ fontSize: 12 }}>
            {updatedAt ? `Updated ${updatedAt}` : "Loading…"}
          </span>
          <button type="button" className="icon-btn" onClick={() => void refresh()}>
            Refresh
          </button>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
            Swagger
          </a>
        </>
      }
    >
      <div className="page">
        <KpiStrip clusters={clusters} />
        {error ? <div className="error-banner">{error}</div> : null}

        <div className="dash-main">
          <section className="card panel-card card-glow">
            <div className="panel-head">
              <div className="section-label">
                <span className="section-bar" />
                <span className="section-text">Multi-cluster topology</span>
              </div>
            </div>
            <ClusterTopology
              selectedCluster={selected}
              selectedNode={selectedNode}
              onSelectCluster={(name) => {
                setSelected(name);
                setSelectedNode(null);
              }}
              onSelectNode={(cluster, nodeName) => {
                setSelected(cluster);
                setSelectedNode(nodeName);
              }}
              refreshToken={refreshToken}
            />
          </section>

          <section className="card panel-card">
            <div className="panel-head">
              <div className="section-label">
                <span className="section-bar" />
                <span className="section-text">
                  {selected
                    ? selectedNode
                      ? `Detail · ${selected} / ${selectedNode}`
                      : `Detail · ${selected}`
                    : "Detail"}
                </span>
                <span className="section-kicker">
                  {selectedNode ? "node metrics" : "cluster"}
                </span>
              </div>
            </div>
            <ClusterDetailPanel
              cluster={selected}
              selectedNode={selectedNode}
              onSelectNode={setSelectedNode}
              refreshToken={refreshToken}
              configSync={
                clusters.find((c) => c.name === selected)?.config_sync ?? null
              }
            />
          </section>
        </div>
      </div>
    </AppShell>
  );
}
