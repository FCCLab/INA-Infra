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

export function normalizeCanonicalId(id: string): string {
  const s = (id || "").toLowerCase().trim();
  const m = s.match(/cam[_-]?(\d+)/);
  if (m) return `slicea_cam${m[1]}`;
  if (s === "slicea" || s === "default" || s === "cam") return "slicea_cam1";
  const m2 = s.match(/(\d+)/);
  if (m2) return `slicea_cam${m2[1]}`;
  return id || "slicea_cam1";
}

export function formatCameraName(id: string, name?: string): string {
  const s = (id || "").toLowerCase().trim();
  const m = s.match(/cam[_-]?(\d+)/);
  if (m) return `Camera ${m[1]}`;
  if (s === "slicea" || s === "default" || s === "cam") return "Camera 1";
  const m2 = s.match(/(\d+)/);
  if (m2) return `Camera ${m2[1]}`;
  return name || id;
}

function sortClients(list: CctvClient[]): CctvClient[] {
  return [...list].sort((a, b) => {
    const na = formatCameraName(a.id, a.name);
    const nb = formatCameraName(b.id, b.name);
    return na.localeCompare(nb, undefined, { numeric: true, sensitivity: "base" });
  });
}

export async function fetchClients(): Promise<CctvClient[]> {
  const res = await fetch("/api/v1/clients", { cache: "no-store" });
  if (!res.ok) throw new Error(`clients ${res.status}`);
  const body = (await res.json()) as { clients?: CctvClient[] };
  const list = Array.isArray(body.clients) ? body.clients : [];
  return sortClients(list);
}

export async function fetchStatus(): Promise<CctvStatus> {
  const res = await fetch("/api/v1/status", { cache: "no-store" });
  if (!res.ok) throw new Error(`status ${res.status}`);
  const data = (await res.json()) as CctvStatus;
  if (Array.isArray(data.clients)) {
    data.clients = sortClients(data.clients);
  }
  return data;
}
