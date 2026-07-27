export type Profile = {
  name: string;
  subnet: string;
  max_slices: number;
  dnn_prefix: string;
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

export type PlApplyResponse = {
  ok: boolean;
  dry_run: boolean;
  message: string;
  written_files: string[];
  push_stdout: string;
  push_stderr: string;
  exit_code: number | null;
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
  updated_at: string;
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
};

/** Default 4-slice SLAs (see ina-infra/sla.md). */
export const DEFAULT_SLICES: SliceIn[] = [
  { id: 1, t_bar: 10, d_bar: 150, h_s: 0, eta_t0: 2.0, slice_type: "CCTV" },
  { id: 2, t_bar: 20, d_bar: 20, h_s: 1, eta_t0: 2.0, slice_type: "Physical AI" },
  { id: 3, t_bar: 40, d_bar: 50, h_s: 0, eta_t0: 2.5, slice_type: "OTT" },
  { id: 4, t_bar: 5, d_bar: 150, h_s: 0, eta_t0: 1.5, slice_type: "IoT" },
];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
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
};
