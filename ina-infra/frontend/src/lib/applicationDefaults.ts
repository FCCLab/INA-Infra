import type { SliceAppType, SliceApplicationConfig } from "../api/client";

type AppTypeDefaults = {
  name: string;
  server_image: string;
  client_image: string;
  server_port: number;
  metrics_port: number;
  server_params: Record<string, unknown>;
  client_params: Record<string, unknown>;
};

const REG = "10.1.132.30:5000";

const TYPE_DEFAULTS: Record<Exclude<SliceAppType, "none" | "custom">, AppTypeDefaults> = {
  cctv: {
    name: "CCTV Vision Streaming",
    server_image: `${REG}/application-cctv:nws-v0.9-amd64`,
    client_image: `${REG}/cctv-ue-console:nws-v0.1-amd64`,
    server_port: 8554,
    metrics_port: 9102,
    server_params: {
      yolo_model: "yolov8n.pt",
      yolo_device: "cpu",
      frame_skip: 1,
      rtsp_port: 8554,
      http_port: 8080,
    },
    client_params: {
      client_count: 1,
      video_clip_ids: ["classroom"],
      fps: 25,
      bitrate_kbps: 4000,
      rtsp_protocol: "tcp",
    },
  },
  physical_ai: {
    name: "Physical AI (Cosmos3 VLM)",
    server_image: `${REG}/cosmo3-vllm:nws-v0.7`,
    client_image: `${REG}/cosmo3-ue-console:nws-v0.18-amd64`,
    server_port: 8000,
    metrics_port: 8002,
    server_params: {
      model: "nvidia/Cosmos3-Nano",
      tensor_parallel_size: 1,
      max_model_len: 4096,
      gpu_arch: "auto",
      gpu_memory_utilization: 0.75,
    },
    client_params: {
      client_count: 1,
      prompt_interval_s: 2,
      max_tokens: 128,
    },
  },
  ott: {
    name: "OTT HD Video Streaming",
    server_image: `${REG}/application-ott:nws-v0.10-amd64`,
    client_image: `${REG}/ott-ue-console:nws-v0.33-amd64`,
    server_port: 8554,
    metrics_port: 9103,
    server_params: {
      stream_protocol: "rtsp",
      resolution: "4k",
    },
    client_params: {
      client_count: 1,
      bitrate_kbps: 6000,
      stream_path: "live/hd",
      play_quality: "4k",
    },
  },
  iot: {
    name: "Background IoT (MQTT)",
    server_image: `${REG}/sliced-edge:nws-v0.9-amd64`,
    client_image: `${REG}/iot-ue-console:nws-v0.10-amd64`,
    server_port: 1883,
    metrics_port: 9105,
    server_params: {
      num_devices: 5,
      mqtt_qos: 0,
    },
    client_params: {
      client_count: 1,
    },
  },
};

export function defaultsForAppType(
  appType: SliceAppType,
  sliceId: number,
): Partial<SliceApplicationConfig> {
  if (appType === "none") {
    return {
      name: `Slice ${sliceId}`,
      enabled: false,
      server_image: "",
      client_image: "",
      server_port: 8080,
      metrics_port: 9100 + sliceId,
      params: { client_count: 1 },
    };
  }
  if (appType === "custom") {
    return {
      name: `Slice ${sliceId} Custom`,
      enabled: true,
      server_image: "",
      client_image: "",
      server_port: 8080,
      metrics_port: 9100 + sliceId,
      params: { client_count: 1 },
    };
  }
  const d = TYPE_DEFAULTS[appType];
  return {
    name: d.name,
    enabled: true,
    server_image: d.server_image,
    client_image: d.client_image,
    server_port: d.server_port,
    metrics_port: d.metrics_port,
    ...(appType === "physical_ai" ? { target_cluster: "edge" as const } : {}),
    params: {
      ...d.server_params,
      ...d.client_params,
      ...(appType === "cctv" ? { stream_path: `cctv/ue${sliceId}` } : {}),
    },
  };
}

export function defaultClientImage(appType: string): string {
  if (appType === "cctv" || appType === "physical_ai" || appType === "ott" || appType === "iot") {
    return TYPE_DEFAULTS[appType].client_image;
  }
  return "";
}
