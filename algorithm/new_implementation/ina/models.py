"""Domain objects: each Slice carries its own SLA / runtime properties.

Layers take ``list[Slice]`` — not a separate SLA config dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Location id: 0=Edge, 1=Regional, 2=Central
Location = int
# Placement of one slice: (CU site, UPF site, APP site)
DeployTuple = Tuple[Location, Location, Location]


@dataclass
class Slice:
    """One network slice — input object for PL / PM / PS.

    SLA fields (set at creation):
      t_bar, d_bar, h_s, eta_t0, slice_type

    Runtime fields (set by caller before PM/PS):
      demand  — target throughput for MediumLayer (Mbps)
      eta     — real-time PRB efficiency for ShortLayer (Mbps/PRB)

    Output fields (filled by layers):
      placement  — (CU, UPF, APP) location tuple after PL
      resources  — allocated compute / PRBs after PL or PM
    """

    id: int
    t_bar: float  # T̄: minimum throughput SLA (Mbps)
    d_bar: float  # D̄: maximum end-to-end delay SLA (ms); used in PL only
    h_s: int = 0  # hard isolation: 1 → dedicated PRBs (typical URLLC)
    eta_t0: float = 1.0  # η at planning time (PL PRB sizing)
    slice_type: str = ""  # label only, e.g. "mMTC", "URLLC(Edge)"

    # --- set before calling PM / PS -------------------------------------------
    demand: float = 0.0  # PM: desired throughput from recent radio demand
    eta: float = 0.0  # PS: current channel efficiency (from MCS)

    # --- filled by layers -----------------------------------------------------
    placement: Optional[DeployTuple] = None
    resources: Optional["SliceResources"] = None


@dataclass
class SliceResources:
    """Allocated resources for one slice (output of PL / PM).

    Naming: a_c_* = CPU amount, a_r_* = RAM, a_g_* = GPU.
    Prefix: cu = CU-UP, upf = UPF, app = application.
    """

    a_c_cu: float = 0.0  # CU CPU
    a_r_cu: float = 0.0  # CU RAM
    a_c_upf: float = 0.0  # UPF CPU
    a_r_upf: float = 0.0  # UPF RAM
    a_c_app: float = 0.0  # APP CPU
    a_r_app: float = 0.0  # APP RAM
    a_g_app: float = 0.0  # APP GPU
    b_min: float | None = None  # reserved PRBs (PL / PS)
    b_ded: float | None = None  # dedicated subset of b_min (PS; PL may omit)

    def compute_cap(self, network) -> float:
        """Throughput upper bound from compute (min over CU/UPF/APP conversions)."""
        return min(
            network.alpha_cu * self.a_c_cu,
            network.alpha_upf * self.a_c_upf,
            network.gamma_c * self.a_c_app,
            network.gamma_r * self.a_r_app,
            network.gamma_g * self.a_g_app,
        )


@dataclass
class PlResult:
    """Return value of PlanningLayer.solve().

    deploy_map[slice_id] = (loc_CU, loc_UPF, loc_APP)
    resources[slice_id]  = SliceResources including b_min
    """

    deploy_map: Dict[int, DeployTuple] = field(default_factory=dict)
    resources: Dict[int, SliceResources] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True if Gurobi found an optimal solution."""
        return bool(self.deploy_map)

    def apply_to(self, slices: list[Slice]) -> None:
        """Copy placement/resources onto the input Slice objects."""
        for s in slices:
            if s.id in self.deploy_map:
                s.placement = self.deploy_map[s.id]
                s.resources = self.resources[s.id]


@dataclass
class PsResult:
    """Return value of ShortLayer.solve().

    MILP decides:
      ``b_min[sid]`` — reserved (guaranteed) PRBs
      ``b_ded[sid]`` — dedicated PRBs (≤ b_min; = b_min when h_s=1)

    Derived after solve (not a Gurobi variable):
      ``extra`` — leftover PRBs shared equally: (b_total − Σ b_min) / n
      ``b_max[sid]`` — usable ceiling that step: b_min + extra
    """

    b_min: Dict[int, float] = field(default_factory=dict)
    b_ded: Dict[int, float] = field(default_factory=dict)
    b_max: Dict[int, float] = field(default_factory=dict)
    extra: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.b_min)

    def apply_to(self, slices: list[Slice]) -> None:
        """Write b_min / b_ded onto each slice's resources (create if needed)."""
        for s in slices:
            if s.id not in self.b_min:
                continue
            if s.resources is None:
                s.resources = SliceResources()
            s.resources.b_min = self.b_min[s.id]
            s.resources.b_ded = self.b_ded[s.id]
