"""Physical network substrate (DCs, capacities, costs, delays).

This is NOT slice SLA config. Slice requirements live on each ``Slice``.
``Network`` describes the shared infrastructure all slices compete for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class Network:
    """Edge / Regional / Central sites and conversion / cost / delay models.

    Location ids (used everywhere as dict keys):
      0 = Edge,  1 = Regional,  2 = Central
    """

    # --- Sites -----------------------------------------------------------------
    locations: List[int] = field(default_factory=lambda: [0, 1, 2])

    # --- NF (CU / UPF) capacity per site ---------------------------------------
    # c_n = CPU units for network functions; r_n = RAM units
    c_n_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 400, 1: 800, 2: 5000}
    )
    r_n_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 5120, 1: 20480, 2: 102400}
    )

    # --- APP capacity per site -------------------------------------------------
    # Application workload: CPU, RAM, GPU
    c_a_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 400, 1: 700, 2: 5000}
    )
    r_a_capacity: Dict[int, float] = field(default_factory=dict)  # default: 2× r_n
    g_a_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 150, 1: 300, 2: 1200}
    )

    # --- Cost coefficients (cheaper toward Central) ----------------------------
    # Used in objectives: cost ≈ amount × p_*[location]
    p_c: Dict[int, float] = field(
        default_factory=lambda: {0: 0.5, 1: 0.05, 2: 0.001}  # CPU cost
    )
    p_r: Dict[int, float] = field(
        default_factory=lambda: {0: 0.1, 1: 0.01, 2: 0.002}  # RAM cost
    )
    p_g: Dict[int, float] = field(
        default_factory=lambda: {0: 1.0, 1: 0.5, 2: 0.1}  # GPU cost
    )
    p_prb_ded: float = 0.5  # cost weight for dedicated PRBs
    p_prb_prio: float = 0.1  # cost weight for shared/priority PRBs

    # --- Resource → throughput conversion --------------------------------------
    # T ≤ alpha_cu * CU_CPU, etc. (bottleneck of all five)
    alpha_cu: float = 1.02  # CU CPU → Mbps
    alpha_upf: float = 0.81  # UPF CPU → Mbps
    gamma_c: float = 0.5  # APP CPU → Mbps
    gamma_r: float = 0.008  # APP RAM → Mbps
    gamma_g: float = 1.0  # APP GPU → Mbps
    min_r_cu: float = 10.0  # lower bound on CU RAM in MILP
    min_r_upf: float = 10.0  # lower bound on UPF RAM in MILP

    # --- Link delays (ms), used only in PL -------------------------------------
    # End-to-end delay ≈ D_F1[CU] + D_N3[CU,UPF] + D_N6[UPF,APP]
    d_f1: Dict[int, float] = field(
        default_factory=lambda: {0: 1, 1: 15, 2: 30}  # radio↔CU by CU site
    )
    d_n3: Dict[Tuple[int, int], float] = field(
        default_factory=lambda: {
            (0, 0): 1, (0, 1): 10, (0, 2): 20,
            (1, 0): 10, (1, 1): 1, (1, 2): 10,
            (2, 0): 20, (2, 1): 10, (2, 2): 1,
        }
    )
    d_n6: Dict[Tuple[int, int], float] = field(
        default_factory=lambda: {
            (0, 0): 5, (0, 1): 15, (0, 2): 30,
            (1, 0): 15, (1, 1): 5, (1, 2): 15,
            (2, 0): 30, (2, 1): 15, (2, 2): 5,
        }
    )

    # --- Radio pool & objective weights ----------------------------------------
    b_total: int = 273  # total PRBs available at the cell
    w_c: float = 1.0  # weight on money/resource cost in objective
    w_p: float = 1000.0  # weight on SLA shortfalls (keep large → prefer meeting SLA)
    beta_demand: float = 0.1  # PM only: weight on demand shortfall vs T̄ shortfall

    gurobi_output: int = 0  # 0 = quiet Gurobi log; 1 = verbose

    def __post_init__(self) -> None:
        # Default APP RAM = 2× NF RAM per site if not provided
        if not self.r_a_capacity:
            self.r_a_capacity = {k: v * 2.0 for k, v in self.r_n_capacity.items()}

    def format_settings(self) -> str:
        """Human-readable substrate settings (for samples / debugging)."""
        loc_names = {0: "Edge", 1: "Regional", 2: "Central"}
        lines = [
            "NETWORK settings:",
            f"  Sites: {', '.join(loc_names[j] for j in self.locations)}",
            f"  Total radio PRBs available at the cell: {self.b_total}",
            f"  Objective weights: resource-cost weight={self.w_c}, "
            f"SLA-shortfall weight={self.w_p} (larger → prefer meeting SLA)",
            f"  Medium-layer demand shortfall weight: {self.beta_demand}",
            f"  PRB cost weights: dedicated={self.p_prb_ded}, "
            f"shared/priority={self.p_prb_prio}",
            "  Resource → throughput (Mbps per unit):",
            f"    CU CPU→{self.alpha_cu}, UPF CPU→{self.alpha_upf}, "
            f"APP CPU→{self.gamma_c}, APP RAM→{self.gamma_r}, APP GPU→{self.gamma_g}",
            f"  Minimum RAM lower bounds in MILP: CU={self.min_r_cu}, "
            f"UPF={self.min_r_upf}",
            "  Per-site capacity and unit cost (cheaper toward Central):",
        ]
        for j in self.locations:
            name = loc_names[j]
            lines.append(
                f"    {name}: NF CPU/RAM capacity={self.c_n_capacity[j]}/"
                f"{self.r_n_capacity[j]}; "
                f"APP CPU/RAM/GPU capacity={self.c_a_capacity[j]}/"
                f"{self.r_a_capacity[j]}/{self.g_a_capacity[j]}; "
                f"unit cost CPU/RAM/GPU={self.p_c[j]}/{self.p_r[j]}/{self.p_g[j]}; "
                f"radio↔CU delay (F1)={self.d_f1[j]} ms"
            )
        def _delay_table(title: str, row_label: str, col_label: str, matrix) -> list[str]:
            names = [loc_names[j] for j in self.locations]
            col_w = max(len(n) for n in names + [row_label])
            cell_w = max(4, max(len(n) for n in names))
            out = [f"  {title} (ms; rows={row_label}, cols={col_label}):"]
            header = f"    {row_label:<{col_w}}" + "".join(f"  {n:>{cell_w}}" for n in names)
            out.append(header)
            out.append(f"    {'-' * col_w}" + "".join(f"  {'-' * cell_w}" for _ in names))
            for i in self.locations:
                row = f"    {loc_names[i]:<{col_w}}"
                for j in self.locations:
                    row += f"  {matrix[(i, j)]:>{cell_w}.0f}"
                out.append(row)
            return out

        lines.extend(
            _delay_table(
                "Midhaul delay CU→UPF (N3)", "CU\\UPF", "UPF site", self.d_n3
            )
        )
        lines.extend(
            _delay_table(
                "Backhaul delay UPF→APP (N6)", "UPF\\APP", "APP site", self.d_n6
            )
        )
        return "\n".join(lines)
