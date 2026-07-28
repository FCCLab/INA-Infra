"""Pydantic request/response models for OpenAPI."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


LOC_NAMES = {0: "Edge", 1: "Regional", 2: "Central"}
LOC_IDS = {"Edge": 0, "Regional": 1, "Central": 2, "edge": 0, "regional": 1, "central": 2}

_K8S_NS_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


# Edge workers that can host rfsim DU / UEs (Multus parent differs for usrp).
EDGE_RF_NODES = ("usrp", "edge-0", "edge-1")


class Profile(BaseModel):
    """Deployment profile: name == K8s namespace on workload clusters."""

    name: str = Field("ina-infra", description="K8s namespace / profile name")
    subnet: str = Field("10.1.140.0/24", description="Multus macvlan CIDR")
    max_slices: int = Field(16, ge=1, le=32)
    dnn_prefix: str = Field("10.140", description="DNN pool prefix → {dnn_prefix}.{n}.0/24")
    du_node: str = Field(
        "usrp",
        description="Edge node hostname for OAI DU (usrp | edge-0 | edge-1)",
    )
    ue_node: str = Field(
        "usrp",
        description="Edge node hostname for OAI UEs (usrp | edge-0 | edge-1)",
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
        if node not in EDGE_RF_NODES:
            raise ValueError(
                f"invalid edge RF node {v!r}; expected one of {list(EDGE_RF_NODES)}"
            )
        return node


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
    dry_run: bool = False
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
