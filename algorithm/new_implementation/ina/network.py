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
    # Sized for target demo placement:
    #   CCTV CU@Edge + UPF@Regional; Physical AI all@Edge;
    #   OTT CU@Regional + UPF@Central; IoT all@Central.
    c_n_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 55, 1: 52, 2: 61}
    )
    r_n_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 64, 1: 64, 2: 64}
    )

    # --- APP capacity per site -------------------------------------------------
    # Edge fits Physical AI APP only; regional fits CCTV; central fits OTT+IoT.
    # RAM sized for T̄/γ_r (PA≈2500, CCTV≈1250, OTT+IoT≈5625).
    c_a_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 41, 1: 25, 2: 90}
    )
    r_a_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 2600, 1: 1400, 2: 5625}
    )
    g_a_capacity: Dict[int, float] = field(
        default_factory=lambda: {0: 22, 1: 12, 2: 45}
    )

    # --- Cost coefficients (edge expensive → prefer regional/central when delay OK)
    p_c: Dict[int, float] = field(
        default_factory=lambda: {0: 2.5, 1: 0.25, 2: 0.001}  # CPU cost
    )
    p_r: Dict[int, float] = field(
        default_factory=lambda: {0: 0.5, 1: 0.05, 2: 0.002}  # RAM cost
    )
    p_g: Dict[int, float] = field(
        default_factory=lambda: {0: 2.5, 1: 0.5, 2: 0.1}  # GPU cost
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
    # Sites: 0=Edge, 1=Regional, 2=Central.
    # Hop RTT (ping): Edge↔Regional=20, Regional↔Central=20 → Edge↔Central=40.
    # (Values are round-trip; one-way would be half.) Same-site diagonal ≈ 2 ms.
    # E2E ≈ d_rf + d_f1[CU] + d_n3[CU,UPF] + d_n6[UPF,APP].
    # DU is always at Edge → F1 is a single row keyed by CU-UP site only.
    d_rf: float = 20.0  # UE → DU (RF / air interface), fixed
    d_f1: Dict[int, float] = field(
        default_factory=lambda: {0: 2, 1: 20, 2: 40}  # DU@Edge → CU-UP site (RTT)
    )
    d_n3: Dict[Tuple[int, int], float] = field(
        default_factory=lambda: {
            # rows=CU-UP site, cols=UPF site (RTT)
            (0, 0): 2, (0, 1): 20, (0, 2): 40,
            (1, 0): 20, (1, 1): 2, (1, 2): 20,
            (2, 0): 40, (2, 1): 20, (2, 2): 2,
        }
    )
    d_n6: Dict[Tuple[int, int], float] = field(
        default_factory=lambda: {
            # Prefer UPF↔APP co-location (cross-site N6 higher than pure hop RTT).
            (0, 0): 2, (0, 1): 25, (0, 2): 50,
            (1, 0): 25, (1, 1): 2, (1, 2): 35,
            (2, 0): 50, (2, 1): 35, (2, 2): 2,
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
                f"unit cost CPU/RAM/GPU={self.p_c[j]}/{self.p_r[j]}/{self.p_g[j]}"
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

        lines.append(f"  RF delay UE→DU: {self.d_rf} ms")
        # F1: one row (DU always at Edge)
        f1_names = [loc_names[j] for j in self.locations]
        f1_row_label = "DU@Edge"
        f1_col_w = max(len(n) for n in f1_names + [f1_row_label])
        f1_cell_w = max(4, max(len(n) for n in f1_names))
        lines.append("  F1 delay DU→CU-UP (ms; DU fixed at Edge, cols=CU-UP site):")
        lines.append(
            f"    {f1_row_label:<{f1_col_w}}"
            + "".join(f"  {n:>{f1_cell_w}}" for n in f1_names)
        )
        lines.append(
            f"    {'-' * f1_col_w}" + "".join(f"  {'-' * f1_cell_w}" for _ in f1_names)
        )
        f1_vals = f"    {f1_row_label:<{f1_col_w}}"
        for j in self.locations:
            f1_vals += f"  {self.d_f1[j]:>{f1_cell_w}.0f}"
        lines.append(f1_vals)
        lines.extend(
            _delay_table(
                "N3 delay CU-UP→UPF", "CU\\UPF", "UPF site", self.d_n3
            )
        )
        lines.extend(
            _delay_table(
                "N6 delay UPF→APP", "UPF\\APP", "APP site", self.d_n6
            )
        )
        return "\n".join(lines)
