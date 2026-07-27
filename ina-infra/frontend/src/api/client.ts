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

export type PlSolveResponse = {
  ok: boolean;
  message: string;
  deploy_map: Record<string, PlacementOut>;
  resources: Record<string, ResourcesOut>;
  slices: SliceResultOut[];
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
  network: () => request<NetworkOut>("/api/v1/network"),
  solve: (slices: SliceIn[]) =>
    request<PlSolveResponse>("/api/v1/pl/solve", {
      method: "POST",
      body: JSON.stringify({ slices }),
    }),
  apply: (body: {
    result: PlSolveResponse;
    slices: SliceIn[];
    commit_message: string;
    dry_run: boolean;
  }) =>
    request<PlApplyResponse>("/api/v1/pl/apply", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
