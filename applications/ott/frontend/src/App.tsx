import React, { useEffect, useState } from "react";
import Navbar from "./components/Navbar";
import ConsoleView from "./components/ConsoleView";
import TheaterView from "./components/TheaterView";
import ChannelGridView from "./components/ChannelGridView";
import AddStreamModal from "./components/AddStreamModal";
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

export default function App() {
  const [currentTab, setCurrentTab] = useState<"console" | "theater" | "grid">("console");
  const [channels, setChannels] = useState<OttChannel[]>([]);
  const [clients, setClients] = useState<ConnectedClient[]>([]);
  const [selectedChannelId, setSelectedChannelId] = useState<string>("channel_1");
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [totalThroughput, setTotalThroughput] = useState<number>(0);

  // Polling loop with AbortController
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
      } catch (err: any) {
        if (err?.name !== "AbortError") {
          // Keep last state
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
      prev.map((c) => (c.id === clientId ? { ...c, state: "STREAMING" } : c))
    );
  };

  const handleStopClient = async (clientId: string) => {
    await stopClient(clientId);
    setClients((prev) =>
      prev.map((c) => (c.id === clientId ? { ...c, state: "STOPPED" } : c))
    );
  };

  const handleSetChannel = async (clientId: string, channelId: string) => {
    await setClientChannel(clientId, channelId);
    setClients((prev) =>
      prev.map((c) => (c.id === clientId ? { ...c, assigned_channel: channelId } : c))
    );
  };

  const handleAddYouTubeStream = async (channelId: string, youtubeUrl: string, title?: string) => {
    await playYouTubeStream(channelId, youtubeUrl, title);
    const updated = await fetchChannels();
    setChannels(updated);
  };

  const streamingCount = clients.filter((c) => c.state === "STREAMING").length;

  return (
    <div className="ott-app">
      <Navbar
        currentTab={currentTab}
        setCurrentTab={setCurrentTab}
        onOpenAddStream={() => setIsAddModalOpen(true)}
        streamingCount={streamingCount}
        totalClients={clients.length}
        totalThroughput={totalThroughput}
      />

      <div className="kpi-bar">
        <div className="kpi-item">
          <span>Active Streams:</span>
          <strong>{streamingCount} / {clients.length} UEs</strong>
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

      <main className="ott-main-content">
        {currentTab === "console" && (
          <ConsoleView
            clients={clients}
            channels={channels}
            onStartClient={handleStartClient}
            onStopClient={handleStopClient}
            onSetChannel={handleSetChannel}
          />
        )}

        {currentTab === "theater" && (
          <TheaterView
            channels={channels}
            selectedChannelId={selectedChannelId}
            onSelectChannel={setSelectedChannelId}
          />
        )}

        {currentTab === "grid" && <ChannelGridView channels={channels} />}
      </main>

      <AddStreamModal
        channels={channels}
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onAddStream={handleAddYouTubeStream}
      />
    </div>
  );
}
