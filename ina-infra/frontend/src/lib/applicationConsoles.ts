import type { SliceAppType } from "../api/client";

export const GRAFANA_BASE = "http://10.1.137.105:3000";

export type ExternalLink = {
  href: string;
  label: string;
  title: string;
};

export type AppConsolePair = {
  id: Exclude<SliceAppType, "none" | "custom">;
  name: string;
  sliceId: number;
  /** Application control console (CCTV video wall, Physical AI sidecar, OTT/IoT control). */
  console: ExternalLink;
  /** Dedicated Grafana metrics dashboard. */
  grafana: ExternalLink;
};

function grafanaUrl(uid: string, slug: string, refresh = "5s"): string {
  return `${GRAFANA_BASE}/d/${uid}/${slug}?orgId=1&refresh=${refresh}`;
}

/** Application N6 / console Multus on site L2 (not profile 10.1.140). */
export function applicationMultusIp(sliceId: number): string {
  return `10.1.137.${210 + sliceId}`;
}

function applicationConsoleHref(sliceId: number, port: number): string {
  const ip = applicationMultusIp(sliceId);
  return port === 80 ? `http://${ip}/` : `http://${ip}:${port}/`;
}

export const APPLICATION_CONSOLES: Record<
  Exclude<SliceAppType, "none" | "custom">,
  AppConsolePair
> = {
  cctv: {
    id: "cctv",
    name: "CCTV",
    sliceId: 1,
    console: {
      href: applicationConsoleHref(1, 80),
      label: "CCTV Console",
      title: "Open CCTV console & Swagger (10.1.137.211)",
    },
    grafana: {
      href: grafanaUrl("ina-cctv-metrics", "cctv-metrics"),
      label: "Grafana Dashboard",
      title: "Open CCTV Grafana dashboard (5G UL/DL and latency)",
    },
  },
  physical_ai: {
    id: "physical_ai",
    name: "Physical AI",
    sliceId: 2,
    console: {
      href: applicationConsoleHref(2, 80),
      label: "Physical AI Console",
      title: "Open Physical AI console (HF token + vLLM) on 10.1.137.212",
    },
    grafana: {
      href: grafanaUrl("ina-physical-ai", "physical-ai-metrics"),
      label: "Grafana Dashboard",
      title: "Open Physical AI Grafana dashboard (latency and throughput)",
    },
  },
  ott: {
    id: "ott",
    name: "OTT",
    sliceId: 3,
    console: {
      href: applicationConsoleHref(3, 80),
      label: "OTT Console",
      title: "Open OTT Video Streaming Portal & UE Reception Console on 10.1.137.213",
    },
    grafana: {
      href: grafanaUrl("ina-ott-metrics", "ott-metrics"),
      label: "Grafana Dashboard",
      title: "Open OTT Grafana dashboard (5G UL/DL and latency)",
    },
  },
  iot: {
    id: "iot",
    name: "IoT",
    sliceId: 4,
    console: {
      href: applicationConsoleHref(4, 80),
      label: "IoT Console",
      title: "Open IoT control console (MQTT, periods, publish) on 10.1.137.214",
    },
    grafana: {
      href: grafanaUrl("ina-iot-metrics", "iot-metrics"),
      label: "Grafana Dashboard",
      title: "Open IoT Grafana dashboard (5G UL/DL and latency)",
    },
  },
};

export function consolePairFor(appType: SliceAppType): AppConsolePair | null {
  if (appType === "none" || appType === "custom") return null;
  return APPLICATION_CONSOLES[appType] ?? null;
}

export const APPLICATION_CONSOLE_LIST = Object.values(APPLICATION_CONSOLES);
