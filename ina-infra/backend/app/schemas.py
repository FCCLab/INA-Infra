"""Pydantic request/response models for OpenAPI."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


LOC_NAMES = {0: "Edge", 1: "Regional", 2: "Central"}
LOC_IDS = {"Edge": 0, "Regional": 1, "Central": 2, "edge": 0, "regional": 1, "central": 2}


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
    c_n_capacity: Optional[Dict[int, float]] = None
    r_n_capacity: Optional[Dict[int, float]] = None
    c_a_capacity: Optional[Dict[int, float]] = None
    r_a_capacity: Optional[Dict[int, float]] = None
    g_a_capacity: Optional[Dict[int, float]] = None
    p_c: Optional[Dict[int, float]] = None
    p_r: Optional[Dict[int, float]] = None
    p_g: Optional[Dict[int, float]] = None
    d_f1: Optional[Dict[int, float]] = None


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


class PlSolveRequest(BaseModel):
    slices: List[SliceIn]
    network: Optional[NetworkIn] = None


class PlSolveResponse(BaseModel):
    ok: bool
    message: str = ""
    deploy_map: Dict[str, PlacementOut] = Field(default_factory=dict)
    resources: Dict[str, ResourcesOut] = Field(default_factory=dict)
    slices: List[SliceResultOut] = Field(default_factory=list)


class PlApplyRequest(BaseModel):
    result: PlSolveResponse
    slices: List[SliceIn] = Field(default_factory=list)
    commit_message: str = "ina-pl: apply planning intent"
    dry_run: bool = False
    clusters: List[str] = Field(
        default_factory=lambda: ["central", "regional", "edge"],
        description="Clusters to push via push_git_repos.sh",
    )


class PlApplyResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str = ""
    written_files: List[str] = Field(default_factory=list)
    push_stdout: str = ""
    push_stderr: str = ""
    exit_code: Optional[int] = None


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
