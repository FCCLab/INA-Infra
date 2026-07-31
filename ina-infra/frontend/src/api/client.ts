export type Profile = {
  name: string;
  subnet: string;
  max_slices: number;
  dnn_prefix: string;
  /** Edge kubernetes.io/hostname for OAI DU. */
  du_node: string;
  /** Edge kubernetes.io/hostname for OAI UEs. */
  ue_node: string;
};

export type SliceIn = {
  id: number;
  t_bar: number;
  d_bar: number;
  h_s: number;
  eta_t0: number;
  slice_type: string;
};

export type PlacementOut = {
  cu: string;
  upf: string;
  app: string;
  cu_id: number;
  upf_id: number;
  app_id: number;
};

export type ResourcesOut = {
  a_c_cu: number;
  a_r_cu: number;
  a_c_upf: number;
  a_r_upf: number;
  a_c_app: number;
  a_r_app: number;
  a_g_app: number;
  b_min: number | null;
  b_ded: number | null;
};

export type SliceResultOut = SliceIn & {
  placement: PlacementOut;
  resources: ResourcesOut;
};

export type SharedIps = {
  gw_central: string;
  gw_regional: string;
  gw_edge: string;
  amf_n2: string;
  nrf_sbi?: string;
  smf_n4: string;
  cucp_n2: string;
  cucp_f1c: string;
  cucp_e1: string;
  du_f1: string;
  du_rf: string;
  flexric_e2: string;
  xapp_e2: string;
  gateway: string;
  prefix_len: number;
};

export type SliceIps = {
  n: number;
  slice_id: number;
  upf_n3: string;
  upf_n4: string;
  upf_n6: string;
  cuup_e1: string;
  cuup_f1u: string;
  cuup_n3: string;
  ue_rf: string;
  dnn_cidr: string;
  site_cu: string;
  site_upf: string;
  site_app: string;
  cluster_cu: string;
  cluster_upf: string;
};

export type IpPlan = {
  profile: Profile;
  subnet: string;
  n_slices: number;
  shared: SharedIps;
  slices: SliceIps[];
  bases: Record<string, number>;
};

export type PlSolveResponse = {
  ok: boolean;
  message: string;
  deploy_map: Record<string, PlacementOut>;
  resources: Record<string, ResourcesOut>;
  slices: SliceResultOut[];
  ip_plan?: IpPlan | null;
  profile?: Profile | null;
  result_file?: string | null;
};

export type NetworkIn = {
  b_total?: number | null;
  w_c?: number | null;
  w_p?: number | null;
  beta_demand?: number | null;
  p_prb_ded?: number | null;
  p_prb_prio?: number | null;
  alpha_cu?: number | null;
  alpha_upf?: number | null;
  gamma_c?: number | null;
  gamma_r?: number | null;
  gamma_g?: number | null;
  min_r_cu?: number | null;
  min_r_upf?: number | null;
  c_n_capacity?: Record<string, number> | null;
  r_n_capacity?: Record<string, number> | null;
  c_a_capacity?: Record<string, number> | null;
  r_a_capacity?: Record<string, number> | null;
  g_a_capacity?: Record<string, number> | null;
  p_c?: Record<string, number> | null;
  p_r?: Record<string, number> | null;
  p_g?: Record<string, number> | null;
  d_rf?: number | null; // UE → DU (RF), ms
  d_f1?: Record<string, number> | null; // CU-UP site "0"|"1"|"2" (DU always Edge)
  d_n3?: Record<string, number> | null; // "i-j" CU-UP → UPF
  d_n6?: Record<string, number> | null; // "i-j" UPF → APP
};

export type NetworkOut = {
  settings_text: string;
  b_total: number;
  w_c: number;
  w_p: number;
  locations: string[];
};

export type ProfileDefaultsOut = {
  profile: Profile;
  slices: SliceIn[];
  network: NetworkIn;
};

export type ProfileRecord = {
  profile: Profile;
  slices: SliceIn[];
  network: NetworkIn;
  pl_result?: PlSolveResponse | null;
  pl_result_file?: string | null;
  deployed?: boolean;
  deployed_at?: string | null;
  deploy_files?: string[];
  deploy_clusters?: string[];
  updated_at: string;
};

export type PlApplyResponse = {
  ok: boolean;
  dry_run: boolean;
  message: string;
  written_files: string[];
  push_stdout: string;
  push_stderr: string;
  exit_code: number | null;
  deployed?: boolean;
  profile?: ProfileRecord | null;
};

export type PlUndeployResponse = {
  ok: boolean;
  dry_run: boolean;
  message: string;
  removed_paths: string[];
  push_stdout: string;
  push_stderr: string;
  exit_code: number | null;
  deployed?: boolean;
  profile?: ProfileRecord | null;
};

export type PlPushResponse = {
  ok: boolean;
  message: string;
  written_files: string[];
  push_stdout: string;
  push_stderr: string;
  exit_code: number | null;
  deployed?: boolean;
  profile?: ProfileRecord | null;
};

export type ProfileRolloutRequest = {
  skip_ues?: boolean;
  skip_ran?: boolean;
  only_ues?: boolean;
  slice_count?: number | null;
};

export type ProfileRolloutResponse = {
  ok: boolean;
  profile: string;
  message: string;
  stdout: string;
  stderr: string;
  exit_code: number | null;
};

export type ProfileRolloutStopResponse = {
  ok: boolean;
  profile: string;
  stopped: boolean;
  message: string;
};

export type DeployStatusItem = {
  name: string;
  exists: boolean;
  ready: number;
  desired: number;
  available: number;
  up_to_date: number;
  ready_text: string;
  status: string;
  ok: boolean;
};

export type ConfigSyncStatus = {
  name: string;
  namespace: string;
  exists: boolean;
  overall: string; // synced | syncing | error | missing | unknown
  summary: string;
  source_commit?: string;
  render_commit?: string;
  sync_commit?: string;
  last_synced_commit?: string;
  syncing?: boolean;
  stalled?: boolean;
  reconciling?: boolean;
  error_count?: number;
  message?: string;
  updated_at?: string | null;
  error?: string | null;
  repo?: string;
  branch?: string;
};

export type ClusterDeployStatus = {
  cluster: string;
  context: string;
  namespace: string;
  namespace_exists: boolean;
  namespace_phase?: string | null;
  overall: string;
  summary: string;
  error?: string | null;
  deployments: DeployStatusItem[];
  expected: string[];
  config_sync?: ConfigSyncStatus | null;
};

export type ClusterConfigSyncOut = {
  cluster: string;
  context: string;
  config_sync: ConfigSyncStatus;
};

export type ProfileClusterStatusOut = {
  namespace: string;
  cluster: string;
  context: string;
  namespace_exists: boolean;
  namespace_phase?: string | null;
  overall: string;
  summary: string;
  error?: string | null;
  deployments: DeployStatusItem[];
  expected: string[];
  clusters?: ClusterDeployStatus[];
  config_syncs?: ClusterConfigSyncOut[];
  config_sync_overall?: string;
  config_sync_summary?: string;
};

export type ProfileListOut = {
  profiles: ProfileRecord[];
  names: string[];
};

export const DEFAULT_PROFILE: Profile = {
  name: "ina-infra",
  subnet: "10.1.140.0/24",
  max_slices: 16,
  dnn_prefix: "10.140",
  du_node: "usrp",
  ue_node: "usrp",
};

/** Edge nodes that can host rfsim DU / UEs. */
/** @deprecated Prefer api.edgeNodes() — kept as offline fallback. */
export const EDGE_RF_NODES = ["usrp", "edge-0", "edge-1", "edge-2"] as const;

export type EdgeNodeOut = {
  name: string;
  ready: boolean;
  internal_ip?: string;
  roles?: string[];
  multus_master?: string;
  capacity_cpu?: string;
  capacity_memory?: string;
};

export type EdgeNodesOut = {
  cluster: string;
  nodes: EdgeNodeOut[];
  error?: string | null;
  default_du?: string;
  default_ue?: string;
};

/** Default 4-slice SLAs (see ina-infra/sla.md). */
export const DEFAULT_SLICES: SliceIn[] = [
  { id: 1, t_bar: 10, d_bar: 150, h_s: 0, eta_t0: 2.0, slice_type: "CCTV" },
  { id: 2, t_bar: 20, d_bar: 20, h_s: 1, eta_t0: 2.0, slice_type: "Physical AI" },
  { id: 3, t_bar: 40, d_bar: 50, h_s: 0, eta_t0: 2.5, slice_type: "OTT" },
  { id: 4, t_bar: 5, d_bar: 150, h_s: 0, eta_t0: 1.5, slice_type: "IoT" },
];

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs, ...fetchInit } = init || {};
  const ctrl = timeoutMs != null ? new AbortController() : null;
  const timer =
    ctrl && timeoutMs != null
      ? window.setTimeout(() => ctrl.abort(), timeoutMs)
      : null;
  try {
    const res = await fetch(path, {
      headers: {
        "Content-Type": "application/json",
        ...(fetchInit.headers || {}),
      },
      ...fetchInit,
      signal: ctrl?.signal ?? fetchInit.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch {
        /* ignore */
      }
      throw new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail),
      );
    }
    return res.json() as Promise<T>;
  } finally {
    if (timer != null) window.clearTimeout(timer);
  }
}

export type StreamHandlers = {
  onLog?: (stream: "stdout" | "stderr" | string, line: string) => void;
  onStatus?: (message: string, extra?: Record<string, unknown>) => void;
  onError?: (message: string) => void;
};

/** Consume a POST SSE stream; resolves with the final `result` event payload. */
export async function streamRequest<T>(
  path: string,
  body: unknown,
  handlers: StreamHandlers = {},
  opts?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<T> {
  const ctrl = new AbortController();
  const timeoutMs = opts?.timeoutMs;
  const timer =
    timeoutMs != null
      ? window.setTimeout(() => ctrl.abort(), timeoutMs)
      : null;
  const onOuterAbort = () => ctrl.abort();
  if (opts?.signal) {
    if (opts.signal.aborted) ctrl.abort();
    else opts.signal.addEventListener("abort", onOuterAbort, { once: true });
  }
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const errBody = await res.json();
        detail = errBody.detail || JSON.stringify(errBody);
      } catch {
        /* ignore */
      }
      throw new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail),
      );
    }
    if (!res.body) {
      throw new Error("SSE response missing body");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let result: T | undefined;
    let eventName = "message";

    const flushBlock = (block: string) => {
      const lines = block.split("\n");
      let dataLines: string[] = [];
      let ev = eventName;
      for (const line of lines) {
        if (line.startsWith("event:")) {
          ev = line.slice(6).trim() || "message";
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).replace(/^ /, ""));
        }
      }
      if (!dataLines.length) return;
      const raw = dataLines.join("\n");
      let parsed: unknown = raw;
      try {
        parsed = JSON.parse(raw);
      } catch {
        /* keep raw string */
      }
      if (ev === "log" && parsed && typeof parsed === "object") {
        const p = parsed as { stream?: string; line?: string };
        handlers.onLog?.(p.stream || "stdout", p.line ?? "");
      } else if (ev === "status" && parsed && typeof parsed === "object") {
        const p = parsed as { message?: string } & Record<string, unknown>;
        handlers.onStatus?.(p.message || "", p);
      } else if (ev === "error" && parsed && typeof parsed === "object") {
        const p = parsed as { message?: string };
        handlers.onError?.(p.message || "error");
      } else if (ev === "result") {
        result = parsed as T;
      }
      eventName = "message";
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (block.trim()) flushBlock(block);
      }
    }
    if (buf.trim()) flushBlock(buf);

    if (result === undefined) {
      throw new Error("SSE stream ended without a result event");
    }
    return result;
  } finally {
    if (timer != null) window.clearTimeout(timer);
    if (opts?.signal) opts.signal.removeEventListener("abort", onOuterAbort);
  }
}

export const api = {
  health: () => request<{ status: string }>("/api/v1/health"),
  defaults: () => request<SliceIn[]>("/api/v1/slices/defaults"),
  profileDefaults: () => request<ProfileDefaultsOut>("/api/v1/profiles/default"),
  listProfiles: () => request<ProfileListOut>("/api/v1/profiles"),
  getProfile: (name: string) =>
    request<ProfileRecord>(`/api/v1/profiles/${encodeURIComponent(name)}`),
  createProfile: (body: {
    profile: Profile;
    slices?: SliceIn[];
    network?: NetworkIn;
    copy_from?: string;
  }) =>
    request<ProfileRecord>("/api/v1/profiles", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  saveProfile: (name: string, body: ProfileRecord) =>
    request<ProfileRecord>(`/api/v1/profiles/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  restoreProfileDefaults: (name: string) =>
    request<ProfileRecord>(
      `/api/v1/profiles/${encodeURIComponent(name)}/restore-defaults`,
      { method: "POST" },
    ),
  deleteProfile: (name: string) =>
    request<{ ok: boolean; deleted: string; remaining: string[] }>(
      `/api/v1/profiles/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  network: () => request<NetworkOut>("/api/v1/network"),
  networkPreview: (network: NetworkIn) =>
    request<NetworkOut>("/api/v1/network", {
      method: "PUT",
      body: JSON.stringify(network),
    }),
  solve: (slices: SliceIn[], profile: Profile, network?: NetworkIn) =>
    request<PlSolveResponse>("/api/v1/pl/solve", {
      method: "POST",
      body: JSON.stringify({ slices, profile, network }),
    }),
  apply: (body: {
    result: PlSolveResponse;
    slices: SliceIn[];
    profile: Profile;
    commit_message: string;
    dry_run: boolean;
  }) =>
    request<PlApplyResponse>("/api/v1/pl/apply", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  applyStream: (
    body: {
      result: PlSolveResponse;
      slices: SliceIn[];
      profile: Profile;
      commit_message: string;
      dry_run: boolean;
    },
    handlers?: StreamHandlers,
    opts?: { timeoutMs?: number; signal?: AbortSignal },
  ) =>
    streamRequest<PlApplyResponse>(
      "/api/v1/pl/apply/stream",
      body,
      handlers,
      { timeoutMs: opts?.timeoutMs ?? 920_000, signal: opts?.signal },
    ),
  pushStream: (
    body: {
      profile: Profile;
      commit_message?: string;
    },
    handlers?: StreamHandlers,
    opts?: { timeoutMs?: number; signal?: AbortSignal },
  ) =>
    streamRequest<PlPushResponse>(
      "/api/v1/pl/push/stream",
      body,
      handlers,
      { timeoutMs: opts?.timeoutMs ?? 920_000, signal: opts?.signal },
    ),
  undeploy: (body: {
    profile: Profile;
    commit_message?: string;
    dry_run?: boolean;
  }) =>
    request<PlUndeployResponse>("/api/v1/pl/undeploy", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  undeployStream: (
    body: {
      profile: Profile;
      commit_message?: string;
      dry_run?: boolean;
    },
    handlers?: StreamHandlers,
    opts?: { timeoutMs?: number; signal?: AbortSignal },
  ) =>
    streamRequest<PlUndeployResponse>(
      "/api/v1/pl/undeploy/stream",
      body,
      handlers,
      { timeoutMs: opts?.timeoutMs ?? 920_000, signal: opts?.signal },
    ),
  clusterStatus: (name: string) =>
    request<ProfileClusterStatusOut>(
      `/api/v1/profiles/${encodeURIComponent(name)}/cluster-status`,
    ),
  edgeNodes: () => request<EdgeNodesOut>("/api/v1/clusters/edge/nodes"),
  profileRollout: (name: string, body: ProfileRolloutRequest = {}) =>
    request<ProfileRolloutResponse>(
      `/api/v1/profiles/${encodeURIComponent(name)}/rollout`,
      {
        method: "POST",
        body: JSON.stringify(body),
        // Staged rollout can take several minutes.
        timeoutMs: 920_000,
      },
    ),
  profileRolloutStream: (
    name: string,
    body: ProfileRolloutRequest = {},
    handlers?: StreamHandlers,
    opts?: { timeoutMs?: number; signal?: AbortSignal },
  ) =>
    streamRequest<ProfileRolloutResponse>(
      `/api/v1/profiles/${encodeURIComponent(name)}/rollout/stream`,
      body,
      handlers,
      { timeoutMs: opts?.timeoutMs ?? 920_000, signal: opts?.signal },
    ),
  profileRolloutStop: (name: string) =>
    request<ProfileRolloutStopResponse>(
      `/api/v1/profiles/${encodeURIComponent(name)}/rollout/stop`,
      { method: "POST", body: "{}" },
    ),
};
