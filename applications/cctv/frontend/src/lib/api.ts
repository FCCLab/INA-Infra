export type CctvClient = {
  id: string;
  name: string;
  active: boolean;
  fps: number;
  net_delay_ms: number;
  yolo_delay_ms: number;
  e2e_delay_ms: number;
  detections_count: number;
  detected_objects: string[];
  has_frame: boolean;
  mtx_path: string;
  mtx_publishing: boolean;
  publish_path: string;
  hls_path: string;
  whep_path: string;
  mjpeg_path: string;
  snapshot_path: string;
};

export type CctvStatus = {
  app: string;
  yolo_enabled: boolean;
  yolo_process_per_client: boolean;
  yolo_model: string;
  yolo_device: string;
  rtsp_port: number;
  http_port: number;
  stream_path: string;
  frontend: boolean;
  mediamtx: Record<string, unknown>;
  clients: CctvClient[];
};

export async function fetchClients(): Promise<CctvClient[]> {
  const res = await fetch("/api/v1/clients", { cache: "no-store" });
  if (!res.ok) throw new Error(`clients ${res.status}`);
  const body = (await res.json()) as { clients?: CctvClient[] };
  return Array.isArray(body.clients) ? body.clients : [];
}

export async function fetchStatus(): Promise<CctvStatus> {
  const res = await fetch("/api/v1/status", { cache: "no-store" });
  if (!res.ok) throw new Error(`status ${res.status}`);
  return (await res.json()) as CctvStatus;
}
