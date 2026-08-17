import type { SliceAppType } from "../api/client";

export const GRAFANA_BASE = "http://10.1.137.105:3000";

export type DashboardLink = {
  href: string;
  label: string;
  title: string;
};

export type AppDashboardPair = {
  id: Exclude<SliceAppType, "none" | "custom">;
  name: string;
  sliceId: number;
  /** Application control UI (CCTV video wall, Physical AI sidecar, OTT/IoT control). */
  dashboard: DashboardLink;
  /** Dedicated Grafana metrics dashboard. */
  grafana: DashboardLink;
};

function grafanaUrl(uid: string, slug: string, refresh = "5s"): string {
  return `${GRAFANA_BASE}/d/${uid}/${slug}?orgId=1&refresh=${refresh}`;
}

export const APPLICATION_DASHBOARDS: Record<
  Exclude<SliceAppType, "none" | "custom">,
  AppDashboardPair
> = {
  cctv: {
    id: "cctv",
    name: "CCTV",
    sliceId: 1,
    dashboard: {
      href: "http://10.1.137.120:30080/",
      label: "CCTV Dashboard",
      title: "Open CCTV dashboard & Swagger (10.1.137.120:30080)",
    },
    grafana: {
      href: grafanaUrl("ffvbyfvl0i29sd", "cctv-dashboard", "2s"),
      label: "Grafana: CCTV",
      title: "CCTV YOLO / e2e metrics on Grafana (10.1.137.105:3000)",
    },
  },
  physical_ai: {
    id: "physical_ai",
    name: "Physical AI",
    sliceId: 2,
    dashboard: {
      href: "http://10.1.137.133:30082/",
      label: "Physical AI Dashboard",
      title: "Open Physical AI dashboard (HF token + vLLM) on 10.1.137.133:30082",
    },
    grafana: {
      href: grafanaUrl("ina-physical-ai", "physical-ai-metrics"),
      label: "Grafana: Physical AI",
      title: "Physical AI / vLLM metrics on Grafana",
    },
  },
  ott: {
    id: "ott",
    name: "OTT",
    sliceId: 3,
    dashboard: {
      href: "http://10.1.137.110:30083/",
      label: "OTT Portal & Console",
      title: "Open OTT Video Streaming Portal & UE Reception Console on 10.1.137.110:30083",
    },
    grafana: {
      href: grafanaUrl("ina-ott-metrics", "ott-metrics"),
      label: "Grafana: OTT",
      title: "OTT time-series metrics on Grafana",
    },
  },
  iot: {
    id: "iot",
    name: "IoT",
    sliceId: 4,
    dashboard: {
      href: "http://10.1.137.110:30084/",
      label: "IoT Dashboard",
      title: "Open IoT control dashboard (MQTT, periods, publish) on 10.1.137.110:30084",
    },
    grafana: {
      href: grafanaUrl("ina-iot-metrics", "iot-metrics"),
      label: "Grafana: IoT",
      title: "IoT time-series metrics on Grafana",
    },
  },
};

export function dashboardPairFor(appType: SliceAppType): AppDashboardPair | null {
  if (appType === "none" || appType === "custom") return null;
  return APPLICATION_DASHBOARDS[appType] ?? null;
}

export const APPLICATION_DASHBOARD_LIST = Object.values(APPLICATION_DASHBOARDS);
