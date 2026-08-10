"""Pydantic request/response models for OpenAPI."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


LOC_NAMES = {0: "Edge", 1: "Regional", 2: "Central"}
LOC_IDS = {"Edge": 0, "Regional": 1, "Central": 2, "edge": 0, "regional": 1, "central": 2}

_K8S_NS_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


# Edge RF node names are live-discovered from the edge cluster (see /clusters/edge/nodes).
# Keep a soft default preference order for UI when the API is unreachable.
EDGE_RF_NODE_FALLBACK = ("usrp", "edge-0", "edge-1", "edge-2")

_K8S_NODE_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$", re.I)


class Profile(BaseModel):
    """Deployment profile: name == K8s namespace on workload clusters."""

    name: str = Field("ina-infra", description="K8s namespace / profile name")
    subnet: str = Field("10.1.140.0/24", description="Multus macvlan CIDR")
    max_slices: int = Field(16, ge=1, le=32)
    dnn_prefix: str = Field("10.140", description="DNN pool prefix → {dnn_prefix}.{n}.0/24")
    du_node: str = Field(
        "usrp",
        description="Edge node hostname for OAI DU (kubernetes.io/hostname)",
    )
    ue_node: str = Field(
        "usrp",
        description="Edge node hostname for OAI UEs (kubernetes.io/hostname)",
    )

    @field_validator("name")
    @classmethod
    def _valid_ns(cls, v: str) -> str:
        if not _K8S_NS_RE.match(v):
            raise ValueError(f"invalid K8s namespace / profile name: {v!r}")
        return v

    @field_validator("du_node", "ue_node")
    @classmethod
    def _valid_edge_rf_node(cls, v: str) -> str:
        node = (v or "").strip()
        if not node or not _K8S_NODE_RE.match(node):
            raise ValueError(f"invalid edge RF node hostname: {v!r}")
        return node


class EdgeNodeOut(BaseModel):
    name: str
    ready: bool = False
    internal_ip: str = ""
    roles: List[str] = Field(default_factory=list)
    multus_master: str = ""
    capacity_cpu: str = ""
    capacity_memory: str = ""


class EdgeNodesOut(BaseModel):
    cluster: str = "edge"
    nodes: List[EdgeNodeOut] = Field(default_factory=list)
    error: Optional[str] = None
    default_du: str = "usrp"
    default_ue: str = "usrp"

class SliceIn(BaseModel):
    id: int
    t_bar: float = Field(..., description="Min throughput SLA (Mbps)")
    d_bar: float = Field(..., description="Max end-to-end delay SLA (ms)")
    h_s: int = Field(0, description="Hard isolation: 1 = dedicated PRBs")
    eta_t0: float = Field(1.0, description="Planning radio efficiency (Mbps/PRB)")
    slice_type: str = ""


class NetworkIn(BaseModel):
    """Optional network overrides; omitted fields keep defaults."""

    b_total: Optional[int] = None
    w_c: Optional[float] = None
    w_p: Optional[float] = None
    beta_demand: Optional[float] = None
    p_prb_ded: Optional[float] = None
    p_prb_prio: Optional[float] = None
    alpha_cu: Optional[float] = None
    alpha_upf: Optional[float] = None
    gamma_c: Optional[float] = None
    gamma_r: Optional[float] = None
    gamma_g: Optional[float] = None
    min_r_cu: Optional[float] = None
    min_r_upf: Optional[float] = None
    c_n_capacity: Optional[Dict[int, float]] = None
    r_n_capacity: Optional[Dict[int, float]] = None
    c_a_capacity: Optional[Dict[int, float]] = None
    r_a_capacity: Optional[Dict[int, float]] = None
    g_a_capacity: Optional[Dict[int, float]] = None
    p_c: Optional[Dict[int, float]] = None
    p_r: Optional[Dict[int, float]] = None
    p_g: Optional[Dict[int, float]] = None
    d_rf: Optional[float] = None  # UE → DU (RF), ms
    d_f1: Optional[Dict[str, float]] = None  # CU-UP site "0"|"1"|"2" (DU@Edge)
    d_n3: Optional[Dict[str, float]] = None  # "i-j" CU-UP → UPF
    d_n6: Optional[Dict[str, float]] = None  # "i-j" UPF → APP


class PlacementOut(BaseModel):
    cu: str
    upf: str
    app: str
    cu_id: int
    upf_id: int
    app_id: int


class ResourcesOut(BaseModel):
    a_c_cu: float = 0.0
    a_r_cu: float = 0.0
    a_c_upf: float = 0.0
    a_r_upf: float = 0.0
    a_c_app: float = 0.0
    a_r_app: float = 0.0
    a_g_app: float = 0.0
    b_min: Optional[float] = None
    b_ded: Optional[float] = None


class SliceResultOut(BaseModel):
    id: int
    slice_type: str = ""
    t_bar: float
    d_bar: float
    h_s: int
    eta_t0: float
    placement: PlacementOut
    resources: ResourcesOut


class SharedIps(BaseModel):
    gw_central: str
    gw_regional: str
    gw_edge: str
    amf_n2: str
    nrf_sbi: str = Field(
        "",
        description="NRF Nnrf (HTTP/2 SBI) Multus IP; default host .11 on profile subnet",
    )
    smf_n4: str
    cucp_n2: str
    cucp_f1c: str
    cucp_e1: str
    du_f1: str
    du_rf: str
    flexric_e2: str
    xapp_e2: str
    gateway: str = Field(..., description="Default NAD gateway (central site GW)")
    prefix_len: int = 24

    @model_validator(mode="after")
    def _backfill_nrf_sbi(self) -> "SharedIps":
        """Older stored plans omit nrf_sbi; derive .11 from AMF N2 prefix."""
        if self.nrf_sbi:
            return self
        if self.amf_n2 and self.amf_n2.count(".") == 3:
            prefix = self.amf_n2.rsplit(".", 1)[0]
            object.__setattr__(self, "nrf_sbi", f"{prefix}.11")
        return self


class SliceIps(BaseModel):
    n: int
    slice_id: int
    upf_n3: str
    upf_n4: str
    upf_n6: str
    cuup_e1: str
    cuup_f1u: str
    cuup_n3: str
    ue_rf: str
    dnn_cidr: str
    site_cu: str = ""
    site_upf: str = ""
    site_app: str = ""
    cluster_cu: str = ""
    cluster_upf: str = ""


class IpPlan(BaseModel):
    profile: Profile
    subnet: str
    n_slices: int
    shared: SharedIps
    slices: List[SliceIps] = Field(default_factory=list)
    bases: Dict[str, int] = Field(default_factory=dict)


class PlSolveRequest(BaseModel):
    slices: List[SliceIn]
    network: Optional[NetworkIn] = None
    profile: Optional[Profile] = None


class PlSolveResponse(BaseModel):
    ok: bool
    message: str = ""
    deploy_map: Dict[str, PlacementOut] = Field(default_factory=dict)
    resources: Dict[str, ResourcesOut] = Field(default_factory=dict)
    slices: List[SliceResultOut] = Field(default_factory=list)
    ip_plan: Optional[IpPlan] = None
    profile: Optional[Profile] = None
    result_file: Optional[str] = Field(
        None, description="JSON dump path under ina-infra/backend/results/"
    )


class PlApplyRequest(BaseModel):
    result: PlSolveResponse
    slices: List[SliceIn] = Field(default_factory=list)
    profile: Optional[Profile] = None
    commit_message: str = "ina-pl: deploy profile manifests"
    dry_run: bool = False
    include_core: bool = Field(
        True,
        description=(
            "On central, also emit dedicated-core MySQL + NRF/AUSF/UDM/UDR/AMF/SMF "
            "into the profile namespace (alongside Multus NADs / IP ConfigMaps)."
        ),
    )
    include_ran: bool = Field(
        True,
        description=(
            "Emit profile gNB on edge (CU-CP/DU/FlexRIC/UEs) and co-located "
            "UPF NFDeploy + CU-UP Deployment on each slice's PL UPF site."
        ),
    )
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "regional", "edge"],
        description="Clusters to push via push_git_repos.sh",
    )


class ProfileRolloutRequest(BaseModel):
    skip_ues: bool = False
    skip_ran: bool = False
    only_ues: bool = False
    ignore_pfcp: bool = Field(
        False,
        description="Continue even if SMF↔UPF PFCP association logs are not detected",
    )
    slice_count: Optional[int] = Field(
        None, description="Override N; default from ina-pl-placement"
    )
    ue_gap_sec: Optional[int] = None
    pdu_wait_sec: Optional[int] = None
    du_settle_sec: Optional[int] = None
    pfcp_wait_sec: Optional[int] = None
    timeout_sec: Optional[int] = Field(
        None, description="Per-deployment kubectl rollout status timeout"
    )
    wall_timeout_sec: int = Field(
        900,
        description="Hard wall-clock timeout for the whole staged rollout script",
    )


class ProfileRolloutResponse(BaseModel):
    ok: bool
    profile: str
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None


class ProfileRolloutStopResponse(BaseModel):
    ok: bool
    profile: str
    stopped: bool = False
    message: str = ""


class DeployStatusItem(BaseModel):
    name: str
    exists: bool
    ready: int = 0
    desired: int = 0
    available: int = 0
    up_to_date: int = 0
    ready_text: str = "—"
    status: str = "Missing"
    ok: bool = False


class ConfigSyncStatus(BaseModel):
    """Config Sync RootSync progress for a workload cluster."""

    name: str = ""
    namespace: str = "config-management-system"
    exists: bool = False
    overall: str = "unknown"  # synced | syncing | error | missing | unknown
    summary: str = ""
    source_commit: str = ""
    render_commit: str = ""
    sync_commit: str = ""
    last_synced_commit: str = ""
    syncing: bool = False
    stalled: bool = False
    reconciling: bool = False
    error_count: int = 0
    message: str = ""
    updated_at: Optional[str] = None
    error: Optional[str] = None
    repo: str = ""
    branch: str = ""


class ClusterDeployStatus(BaseModel):
    cluster: str
    context: str = ""
    namespace: str = ""
    namespace_exists: bool = False
    namespace_phase: Optional[str] = None
    overall: str = "unknown"
    summary: str = ""
    error: Optional[str] = None
    deployments: List[DeployStatusItem] = Field(default_factory=list)
    expected: List[str] = Field(default_factory=list)
    config_sync: Optional[ConfigSyncStatus] = None


class ClusterConfigSyncOut(BaseModel):
    cluster: str
    context: str = ""
    config_sync: ConfigSyncStatus


class ProfileClusterStatusOut(BaseModel):
    namespace: str
    cluster: str = "central"
    context: str = "central@central"
    namespace_exists: bool = False
    namespace_phase: Optional[str] = None
    overall: str = "unknown"
    summary: str = ""
    error: Optional[str] = None
    deployments: List[DeployStatusItem] = Field(default_factory=list)
    expected: List[str] = Field(default_factory=list)
    clusters: List[ClusterDeployStatus] = Field(
        default_factory=list,
        description="Per-cluster status (central, regional, edge)",
    )
    config_syncs: List[ClusterConfigSyncOut] = Field(
        default_factory=list,
        description="Config Sync RootSync status (mgmt, central, regional, edge)",
    )
    config_sync_overall: str = "unknown"
    config_sync_summary: str = ""


class NetworkOut(BaseModel):
    settings_text: str
    b_total: int
    w_c: float
    w_p: float
    locations: List[str]
    c_n_capacity: Dict[int, float]
    r_n_capacity: Dict[int, float]
    c_a_capacity: Dict[int, float]
    r_a_capacity: Dict[int, float]
    g_a_capacity: Dict[int, float]


class ProfileDefaultsOut(BaseModel):
    profile: Profile
    slices: List[SliceIn]
    network: NetworkIn = Field(default_factory=NetworkIn)


class ProfileRecord(BaseModel):
    """Persisted profile: identity + Multus + slice SLAs + network + last PL result."""

    profile: Profile
    slices: List[SliceIn] = Field(default_factory=list)
    network: NetworkIn = Field(default_factory=NetworkIn)
    pl_result: Optional[PlSolveResponse] = Field(
        None, description="Last successful PlanningLayer solve for this profile"
    )
    pl_result_file: Optional[str] = Field(
        None, description="Path to last JSON dump under backend/results/"
    )
    deployed: bool = Field(
        False, description="True after a successful Deploy (GitOps push)"
    )
    deployed_at: Optional[str] = Field(
        None, description="UTC ISO timestamp of last successful Deploy"
    )
    deploy_files: List[str] = Field(
        default_factory=list,
        description="Last generated GitOps file list (dry or real deploy)",
    )
    deploy_clusters: List[str] = Field(
        default_factory=list,
        description="Clusters touched by last deploy",
    )
    updated_at: str = ""


class PlApplyResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str = ""
    written_files: List[str] = Field(default_factory=list)
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None
    deployed: bool = False
    profile: Optional[ProfileRecord] = None


class PlUndeployRequest(BaseModel):
    profile: Optional[Profile] = None
    commit_message: str = "ina-pl: undeploy profile manifests"
    dry_run: bool = Field(
        False,
        description=(
            "True = Clear (remove local namespaces/<profile>/ + clear deploy "
            "state; skip Gitea push). False = Undeploy (clear + push + "
            "cluster cleanup)."
        ),
    )
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "regional", "edge"],
    )


class PlUndeployResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str = ""
    removed_paths: List[str] = Field(default_factory=list)
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None
    deployed: bool = False
    profile: Optional[ProfileRecord] = None


class PlPushRequest(BaseModel):
    """Push already-rendered GitOps repos to Gitea (no re-render)."""

    profile: Optional[Profile] = None
    commit_message: str = "ina-pl: push profile manifests"
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "regional", "edge"],
    )


class PlPushResponse(BaseModel):
    ok: bool
    message: str = ""
    written_files: List[str] = Field(
        default_factory=list,
        description="Files currently under namespaces/<profile>/ after push",
    )
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None
    deployed: bool = False
    profile: Optional[ProfileRecord] = None


class ProfileCreateRequest(BaseModel):
    """Create a new profile (optional copy of slices/network from another)."""

    profile: Profile
    slices: List[SliceIn] = Field(default_factory=list)
    network: Optional[NetworkIn] = None
    copy_from: Optional[str] = Field(
        None,
        description="If set, copy missing slices/network from this saved profile",
    )


class ProfileListOut(BaseModel):
    profiles: List[ProfileRecord]
    names: List[str]


# ── Medium (PM) / Short (PS) loops ───────────────────────────────────────────


class PmLoopParams(BaseModel):
    interval_sec: float = Field(
        10.0,
        ge=0.1,
        le=3600.0,
        description="Loop interval (s) — interval_sec: seconds between PM solves",
    )
    demand_multiplier: float = Field(
        1.0,
        ge=0.0,
        le=100.0,
        description="Demand scale — demand_multiplier: scale PS/t_bar demand before PM",
    )
    max_cycles: int = Field(
        0,
        ge=0,
        description="Max cycles — max_cycles: stop after N solves; 0 = until Stop",
    )


class PsLoopParams(BaseModel):
    interval_sec: float = Field(
        1.0,
        ge=0.1,
        le=3600.0,
        description="Loop interval (s) — interval_sec: seconds between PS solves",
    )
    mcs_min: int = Field(
        5,
        ge=1,
        le=28,
        description="MCS min — mcs_min: lower bound for random η sampling",
    )
    mcs_max: int = Field(
        28,
        ge=1,
        le=28,
        description="MCS max — mcs_max: upper bound for random η sampling",
    )
    mcs_fixed: Optional[int] = Field(
        None,
        ge=1,
        le=28,
        description="Fixed MCS — mcs_fixed: use fixed MCS instead of random",
    )
    max_cycles: int = Field(
        0,
        ge=0,
        description="Max cycles — max_cycles: stop after N solves; 0 = until Stop",
    )
    seed: int = Field(
        2025,
        description="Random seed — seed: RNG seed for MCS sampling",
    )


class PmLoopRequest(BaseModel):
    profile: Profile
    params: Optional[PmLoopParams] = None


class PsLoopRequest(BaseModel):
    profile: Profile
    params: Optional[PsLoopParams] = None


class PmSliceResultOut(BaseModel):
    id: int
    demand: float = Field(..., description="Demand (Mbps) — demand: PM input throughput target")
    compute_cap: float = Field(
        ...,
        description="Compute cap (Mbps) — compute_cap: achievable throughput from resources",
    )
    resources: ResourcesOut


class PsSliceResultOut(BaseModel):
    id: int
    eta: float = Field(..., description="Channel η — eta: Mbps per PRB from MCS")
    b_min: float = Field(..., description="Reserved PRBs — b_min: guaranteed PRB reservation")
    b_ded: float = Field(..., description="Dedicated PRBs — b_ded: isolated subset of b_min")
    b_max: float = Field(..., description="PRB ceiling — b_max: b_min + shared extra")
    radio_mbps: float = Field(
        ...,
        description="Radio Mbps — radio_mbps: estimated b_max × eta",
    )
    demand: float = Field(
        ...,
        description="Demand→PM — demand: radio potential fed to PM loop state",
    )


class PmSolveResponse(BaseModel):
    ok: bool
    profile: str
    cycle: int = 0
    message: str = ""
    slices: List[PmSliceResultOut] = Field(default_factory=list)
    demand: Dict[int, float] = Field(default_factory=dict)


class PsSolveResponse(BaseModel):
    ok: bool
    profile: str
    cycle: int = 0
    message: str = ""
    slices: List[PsSliceResultOut] = Field(default_factory=list)
    extra: float = 0.0
    demand: Dict[int, float] = Field(default_factory=dict)


class PmLoopStatusOut(BaseModel):
    profile: str
    running: bool = False
    cycle: int = 0
    params: PmLoopParams = Field(default_factory=PmLoopParams)
    last_result: Optional[PmSolveResponse] = None
    demand: Dict[int, float] = Field(default_factory=dict)


class PsLoopStatusOut(BaseModel):
    profile: str
    running: bool = False
    cycle: int = 0
    params: PsLoopParams = Field(default_factory=PsLoopParams)
    last_result: Optional[PsSolveResponse] = None
    demand: Dict[int, float] = Field(default_factory=dict)


class PmLoopStopResponse(BaseModel):
    ok: bool
    profile: str
    stopped: bool = False
    message: str = ""


class PsLoopStopResponse(BaseModel):
    ok: bool
    profile: str
    stopped: bool = False
    message: str = ""


# ── Operator agents (RAN operator clients) ───────────────────────────────────

_CPU_QTY_RE = re.compile(r"^([0-9]+(\.[0-9]+)?)(m)?$")
_MEM_QTY_RE = re.compile(
    r"^([0-9]+(\.[0-9]+)?)(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)?$",
    re.IGNORECASE,
)
_GPU_QTY_RE = re.compile(r"^([0-9]+(\.[0-9]+)?)$")


def _validate_cpu_qty(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    if not _CPU_QTY_RE.match(s):
        raise ValueError(f"invalid CPU quantity: {v!r} (use e.g. 200m or 1)")
    return s


def _validate_mem_qty(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    if not _MEM_QTY_RE.match(s):
        raise ValueError(f"invalid memory quantity: {v!r} (use e.g. 128Mi or 1Gi)")
    return s


def _validate_gpu_qty(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    if not _GPU_QTY_RE.match(s):
        raise ValueError(f"invalid GPU quantity: {v!r} (use e.g. 1 or 0.5)")
    return s


def _empty_to_none_cpu(v):
    if v is None or v == "":
        return None
    return _validate_cpu_qty(str(v))


def _empty_to_none_mem(v):
    if v is None or v == "":
        return None
    return _validate_mem_qty(str(v))


def _empty_to_none_gpu(v):
    if v is None or v == "":
        return None
    return _validate_gpu_qty(str(v))


class OperatorNfReported(BaseModel):
    """NF workload reported by a connected operator agent."""

    name: str = Field(..., description="Deployment / logical NF name (e.g. oai-cu-up)")
    kind: str = Field("", description="cuup | cucp | du | other")
    namespace: str = ""
    controllable: List[str] = Field(
        default_factory=lambda: ["cpu", "memory"],
        description="Compute kinds this agent can control for the NF (cpu|memory|gpu|vram)",
    )
    cpu_limit: Optional[str] = None
    cpu_request: Optional[str] = None
    memory_limit: Optional[str] = None
    memory_request: Optional[str] = None
    gpu_limit: Optional[str] = None
    gpu_request: Optional[str] = None
    vram_limit: Optional[str] = None
    vram_request: Optional[str] = None
    ready_replicas: int = 0
    replicas: int = 0

    @field_validator("controllable", mode="before")
    @classmethod
    def _ctrl(cls, v):
        if v is None or v == "":
            return ["cpu", "memory"]
        if isinstance(v, str):
            v = [v]
        out = []
        for item in v:
            s = str(item).strip().lower()
            if s in ("cpu", "memory", "ram"):
                out.append("memory" if s == "ram" else s)
            elif s in ("gpu", "vram"):
                out.append(s)
        return out or ["cpu", "memory"]

    @field_validator("cpu_limit", "cpu_request", mode="before")
    @classmethod
    def _cpu(cls, v):
        return _empty_to_none_cpu(v)

    @field_validator("memory_limit", "memory_request", "vram_limit", "vram_request", mode="before")
    @classmethod
    def _mem(cls, v):
        return _empty_to_none_mem(v)

    @field_validator("gpu_limit", "gpu_request", mode="before")
    @classmethod
    def _gpu(cls, v):
        return _empty_to_none_gpu(v)


class OperatorRegisterRequest(BaseModel):
    """Heartbeat / register payload from operator agent → ina-infra."""

    id: str = Field(..., min_length=1, max_length=128, description="Stable agent id")
    cluster: str = Field("edge", description="Cluster name (edge/regional/central)")
    namespace: str = Field("oai-benchmark", description="Watched namespace")
    version: str = ""
    nfs: List[OperatorNfReported] = Field(default_factory=list)
    message: str = ""


class OperatorResourceTarget(BaseModel):
    """Desired compute resources for one NF: CPU, RAM, GPU, VRAM."""

    cpu_limit: Optional[str] = None
    cpu_request: Optional[str] = None
    memory_limit: Optional[str] = None
    memory_request: Optional[str] = None
    gpu_limit: Optional[str] = None
    gpu_request: Optional[str] = None
    vram_limit: Optional[str] = None
    vram_request: Optional[str] = None
    # Fields provided in the last set_resources call (partial apply).
    changed_fields: List[str] = Field(default_factory=list)
    generation: int = 0
    updated_at: str = ""

    @field_validator("cpu_limit", "cpu_request", mode="before")
    @classmethod
    def _cpu(cls, v):
        return _empty_to_none_cpu(v)

    @field_validator("memory_limit", "memory_request", "vram_limit", "vram_request", mode="before")
    @classmethod
    def _mem(cls, v):
        return _empty_to_none_mem(v)

    @field_validator("gpu_limit", "gpu_request", mode="before")
    @classmethod
    def _gpu(cls, v):
        return _empty_to_none_gpu(v)


# Back-compat alias
OperatorCpuTarget = OperatorResourceTarget

OPERATOR_RESOURCE_KEYS = (
    "cpu_limit",
    "cpu_request",
    "memory_limit",
    "memory_request",
    "gpu_limit",
    "gpu_request",
    "vram_limit",
    "vram_request",
)


class OperatorResourceSetRequest(BaseModel):
    cpu_limit: Optional[str] = Field(None, description="e.g. 300m")
    cpu_request: Optional[str] = Field(None, description="e.g. 50m")
    memory_limit: Optional[str] = Field(None, description="RAM e.g. 512Mi")
    memory_request: Optional[str] = Field(None, description="RAM e.g. 128Mi")
    gpu_limit: Optional[str] = Field(None, description="e.g. 1")
    gpu_request: Optional[str] = Field(None, description="e.g. 1")
    vram_limit: Optional[str] = Field(None, description="e.g. 8Gi")
    vram_request: Optional[str] = Field(None, description="e.g. 8Gi")

    @field_validator("cpu_limit", "cpu_request", mode="before")
    @classmethod
    def _cpu(cls, v):
        return _empty_to_none_cpu(v)

    @field_validator("memory_limit", "memory_request", "vram_limit", "vram_request", mode="before")
    @classmethod
    def _mem(cls, v):
        return _empty_to_none_mem(v)

    @field_validator("gpu_limit", "gpu_request", mode="before")
    @classmethod
    def _gpu(cls, v):
        return _empty_to_none_gpu(v)

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(getattr(self, k) is None for k in OPERATOR_RESOURCE_KEYS):
            raise ValueError("set at least one of cpu_*/memory_*/gpu_*/vram_*")
        return self


# Back-compat alias
OperatorCpuSetRequest = OperatorResourceSetRequest


class OperatorNfOut(BaseModel):
    name: str
    kind: str = ""
    namespace: str = ""
    controllable: List[str] = Field(
        default_factory=lambda: ["cpu", "memory"],
        description="Compute kinds controllable for this NF",
    )
    reported_cpu_limit: Optional[str] = None
    reported_cpu_request: Optional[str] = None
    reported_memory_limit: Optional[str] = None
    reported_memory_request: Optional[str] = None
    reported_gpu_limit: Optional[str] = None
    reported_gpu_request: Optional[str] = None
    reported_vram_limit: Optional[str] = None
    reported_vram_request: Optional[str] = None
    ready_replicas: int = 0
    replicas: int = 0
    desired: Optional[OperatorResourceTarget] = None
    applied_generation: int = 0
    apply_status: str = ""
    apply_message: str = ""


class OperatorOut(BaseModel):
    id: str
    cluster: str
    namespace: str
    version: str = ""
    online: bool = False
    ws_connected: bool = False
    last_seen: str = ""
    message: str = ""
    nfs: List[OperatorNfOut] = Field(default_factory=list)


class OperatorListOut(BaseModel):
    operators: List[OperatorOut] = Field(default_factory=list)
    stale_after_sec: int = 30


class OperatorDesiredOut(BaseModel):
    """Polled by the operator agent."""

    id: str
    targets: Dict[str, OperatorResourceTarget] = Field(default_factory=dict)


class OperatorApplyReport(BaseModel):
    """Operator agent reports apply result for a target generation."""

    nf: str
    generation: int
    ok: bool
    cpu_limit: Optional[str] = None
    cpu_request: Optional[str] = None
    memory_limit: Optional[str] = None
    memory_request: Optional[str] = None
    gpu_limit: Optional[str] = None
    gpu_request: Optional[str] = None
    vram_limit: Optional[str] = None
    vram_request: Optional[str] = None
    message: str = ""


# ── Benchmark (oai-benchmark) ─────────────────────────────────────────────────


class BenchmarkDeployRequest(BaseModel):
    """Render + optionally push the oai-benchmark GitOps stack."""

    commit_message: str = "ina-benchmark: deploy oai-benchmark"
    dry_run: bool = Field(
        False,
        description=(
            "True = render only (no Gitea push). False = render + push "
            "central/edge."
        ),
    )
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "edge"],
        description="Clusters to push (default: central + edge)",
    )
    du_node: str = Field(
        "usrp",
        description="Edge worker hostname for OAI DU (rfsim)",
    )
    ue_node: str = Field(
        "usrp",
        description="Edge worker hostname for OAI nrUE (rfsim)",
    )


class BenchmarkDeployResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str = ""
    written_files: List[str] = Field(default_factory=list)
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None
    deployed: bool = False


class BenchmarkUndeployRequest(BaseModel):
    """Clear local oai-benchmark GitOps; optionally push + cluster cleanup."""

    commit_message: str = "ina-benchmark: undeploy oai-benchmark"
    dry_run: bool = Field(
        False,
        description=(
            "True = Clear (remove local namespaces/oai-benchmark/; skip push). "
            "False = Undeploy (clear + push + force-delete namespaces)."
        ),
    )
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "edge"],
    )


class BenchmarkUndeployResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str = ""
    removed_paths: List[str] = Field(default_factory=list)
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None
    deployed: bool = False


class BenchmarkRunRequest(BaseModel):
    """CPU sweep on oai-benchmark (Operators agent apply per step)."""

    min_cpu: str = Field("50m", description="Lowest CPU (request=limit) for step 1")
    max_cpu: str = Field("1000m", description="Highest CPU; always included as last step")
    cpu_step: str = Field(
        "50m",
        description="CPU increment (default 50m, 100m, … 1000m)",
    )
    step_sec: float = Field(
        120.0, ge=0.1, description="Measure window length per step (seconds)"
    )
    warmup_sec: float = Field(
        60.0, ge=0.0, description="Warm-up after CPU apply, before measure start"
    )
    operator_id: str = Field(
        "edge-oai-benchmark",
        description="Operator agent id (default edge-oai-benchmark)",
    )
    nf: str = Field(
        "oai-cu-up",
        description="NF to resize (default oai-cu-up / U-plane)",
    )


class BenchmarkStepOut(BaseModel):
    index: int
    cpu: str
    phase: str = "pending"
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    message: str = ""


class BenchmarkRunStatusOut(BaseModel):
    id: Optional[int] = None
    running: bool = False
    status: str = "idle"
    message: str = ""
    operator_id: str = ""
    nf: str = ""
    min_cpu: str = ""
    max_cpu: str = ""
    steps: int = 0
    step_sec: float = 0.0
    warmup_sec: float = 0.0
    current_index: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    step_list: List[BenchmarkStepOut] = Field(default_factory=list)


class BenchmarkRunStopResponse(BaseModel):
    ok: bool
    message: str = ""
    status: Optional[BenchmarkRunStatusOut] = None
