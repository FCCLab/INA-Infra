export type OttChannel = {
  id: string;
  name: string;
  category: string;
  source_type: string;
  source_url: string;
  youtube_id?: string;
  description: string;
  is_active: boolean;
  fps: number;
  width: number;
  height: number;
  bitrate_kbps: number;
  hls_path: string;
  whep_path: string;
  rtsp_path: string;
  subscribers_count: number;
  frames_sent: number;
};

export type ConnectedClient = {
  id: string;
  name: string;
  ip: string;
  state: "STREAMING" | "STOPPED" | "IDLE";
  assigned_channel: string;
  net_delay_ms: number;
  rx_fps: number;
  rx_bitrate_mbps: number;
  dropped_frames: number;
  total_frames_received: number;
  uptime_seconds: number;
  is_alive: boolean;
};

export type OttStatus = {
  ok: boolean;
  channels_count: number;
  clients_count: number;
  active_streaming_clients: number;
  total_downlink_throughput_mbps: number;
  channels: OttChannel[];
  clients: ConnectedClient[];
};

export async function fetchChannels(signal?: AbortSignal): Promise<OttChannel[]> {
  const res = await fetch("/api/v1/channels", { cache: "no-store", signal });
  if (!res.ok) throw new Error(`channels ${res.status}`);
  const data = await res.json();
  return data.channels || [];
}

export async function fetchClients(signal?: AbortSignal): Promise<ConnectedClient[]> {
  const res = await fetch("/api/v1/clients", { cache: "no-store", signal });
  if (!res.ok) throw new Error(`clients ${res.status}`);
  const data = await res.json();
  return data.clients || [];
}

export async function fetchStatus(signal?: AbortSignal): Promise<OttStatus> {
  const res = await fetch("/api/v1/status", { cache: "no-store", signal });
  if (!res.ok) throw new Error(`status ${res.status}`);
  return await res.json();
}

export async function startClient(clientId: string): Promise<boolean> {
  const res = await fetch(`/api/v1/clients/${clientId}/start`, { method: "POST" });
  return res.ok;
}

export async function stopClient(clientId: string): Promise<boolean> {
  const res = await fetch(`/api/v1/clients/${clientId}/stop`, { method: "POST" });
  return res.ok;
}

export async function setClientChannel(clientId: string, channelId: string): Promise<boolean> {
  const res = await fetch(`/api/v1/clients/${clientId}/channel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel_id: channelId }),
  });
  return res.ok;
}

export async function playYouTubeStream(channelId: string, youtubeUrl: string, title?: string): Promise<boolean> {
  const res = await fetch(`/api/v1/channels/${channelId}/play`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ youtube_url: youtubeUrl, title }),
  });
  return res.ok;
}
