import { useEffect, useMemo, useState, type ReactNode } from "react";
import AppShell, { type NavTab } from "./components/ui/AppShell";
import ConsoleView from "./components/ConsoleView";
import AddStreamModal from "./components/AddStreamModal";
import DocsPage from "./pages/DocsPage";
import {
  fetchChannels,
  fetchClients,
  fetchStatus,
  playYouTubeStream,
  setClientChannel,
  startClient,
  stopClient,
  type ConnectedClient,
  type OttChannel,
} from "./lib/api";

type Tab = "console" | "docs";

const IconConsole = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="4" width="18" height="14" rx="2" />
    <path d="M8 20h8M12 18v2" strokeLinecap="round" />
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
  title: "OTT Stream Portal · NeuroRAN",
  shortName: "OTT",
  logo: "/logos/NeuroRAN.png",
  logoPng: "/logos/NeuroRAN.png",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("console");
  const [channels, setChannels] = useState<OttChannel[]>([]);
  const [clients, setClients] = useState<ConnectedClient[]>([]);
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [totalThroughput, setTotalThroughput] = useState<number>(0);

  useEffect(() => {
    let stop = false;
    let abortController: AbortController | null = null;

    const tick = async () => {
      if (stop) return;
      abortController?.abort();
      abortController = new AbortController();

      try {
        const [chs, cls, st] = await Promise.all([
          fetchChannels(abortController.signal),
          fetchClients(abortController.signal),
          fetchStatus(abortController.signal),
        ]);

        if (!stop) {
          setChannels(chs);
          setClients(cls);
          setTotalThroughput(st.total_downlink_throughput_mbps || 0);
        }
      } catch (err: unknown) {
        const name = err && typeof err === "object" && "name" in err ? String((err as { name?: string }).name) : "";
        if (name !== "AbortError") {
          /* keep last state */
        }
      }
    };

    void tick();
    const id = setInterval(() => void tick(), 1500);

    return () => {
      stop = true;
      abortController?.abort();
      clearInterval(id);
    };
  }, []);

  const handleStartClient = async (clientId: string) => {
    await startClient(clientId);
    setClients((prev) =>
      prev.map((c) => (c.id === clientId ? { ...c, state: "STREAMING" } : c)),
    );
  };

  const handleStopClient = async (clientId: string) => {
    await stopClient(clientId);
    setClients((prev) =>
      prev.map((c) => (c.id === clientId ? { ...c, state: "STOPPED" } : c)),
    );
  };

  const handleSetChannel = async (clientId: string, channelId: string) => {
    await setClientChannel(clientId, channelId);
    setClients((prev) =>
      prev.map((c) => (c.id === clientId ? { ...c, assigned_channel: channelId } : c)),
    );
  };

  const handleAddYouTubeStream = async (channelId: string, youtubeUrl: string, title?: string) => {
    await playYouTubeStream(channelId, youtubeUrl, title);
    const updated = await fetchChannels();
    setChannels(updated);
  };

  const streamingCount = clients.filter((c) => c.state === "STREAMING").length;

  const tabs: NavTab[] = useMemo(
    () => [
      { id: "console", label: "UE Console", icon: IconConsole },
      { id: "docs", label: "Docs & API", icon: IconBook },
    ],
    [],
  );

  const crumb =
    tab === "console" ? "UE Console · Downlink control" : "Documentation & REST Reference";

  let body: ReactNode = null;
  if (tab === "docs") {
    body = <DocsPage />;
  } else {
    body = (
      <>
        <div className="kpi-bar">
          <div className="kpi-item">
            <span>Active Streams:</span>
            <strong>
              {streamingCount} / {clients.length} UEs
            </strong>
          </div>
          <div className="kpi-item">
            <span>Active Channels:</span>
            <strong>{channels.length}</strong>
          </div>
          <div className="kpi-item">
            <span>Total DL Throughput:</span>
            <strong>{totalThroughput.toFixed(2)} Mbps</strong>
          </div>
        </div>
        <div className="ott-main-content">
          <ConsoleView
            clients={clients}
            channels={channels}
            onStartClient={handleStartClient}
            onStopClient={handleStopClient}
            onSetChannel={handleSetChannel}
          />
        </div>
      </>
    );
  }

  return (
    <AppShell
      brand={BRAND}
      tabs={tabs}
      tab={tab}
      setTab={(id) => setTab(id as Tab)}
      crumb={crumb}
      siteLabel="INA · OTT Stream Portal"
      topbarExtra={
        <>
          <button type="button" className="icon-btn" onClick={() => setIsAddModalOpen(true)}>
            + Add YouTube Stream
          </button>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
            Swagger
          </a>
        </>
      }
    >
      {body}
      <AddStreamModal
        channels={channels}
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onAddStream={handleAddYouTubeStream}
      />
    </AppShell>
  );
}
