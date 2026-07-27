"""Layer 3 — Short-term (PS): PRB reservation given real-time channel η.

Call after setting ``slice.eta`` from the current MCS.
"""

from __future__ import annotations

from typing import Sequence

import gurobipy as gp
from gurobipy import GRB

from ina.models import PsResult, Slice
from ina.network import Network


class ShortLayer:
    """PS MILP: choose reserved / dedicated PRBs per slice.

    Input
    -----
    slices : list[Slice]
        Uses ``t_bar``, ``h_s``, and ``eta`` (Mbps per PRB). Set ``eta`` first.

    Output
    ------
    PsResult with:
      - ``b_min`` — reserved (guaranteed) PRBs  [MILP]
      - ``b_ded`` — dedicated PRBs (≤ b_min)     [MILP]
      - ``extra`` — leftover PRBs / n            [derived]
      - ``b_max`` — usable ceiling = b_min+extra [derived]

    ``b_max`` is not a Gurobi variable; it is the equal-share of leftover
    PRBs after reservation (same rule as the classic simulation).
    """

    def __init__(self, network: Network | None = None):
        self.network = network or Network()

    def solve(self, slices: Sequence[Slice]) -> PsResult:
        slices = list(slices)
        if not slices:
            return PsResult()

        net = self.network
        ids = [s.id for s in slices]
        by_id = {s.id: s for s in slices}

        m = gp.Model("PS_Layer3")
        m.setParam("OutputFlag", net.gurobi_output)

        # ----- Variables -------------------------------------------------------
        b_ded = m.addVars(ids, vtype=GRB.INTEGER, lb=0)  # dedicated PRBs
        b_min = m.addVars(ids, vtype=GRB.INTEGER, lb=0)  # reserved PRBs (≥ b_ded)
        xi_prb = m.addVars(ids, lb=0)  # shortfall if η·b_min < T̄

        # ----- Objective: PRB cost + SLA shortfall -----------------------------
        cost = gp.quicksum(
            net.p_prb_ded * b_ded[sid] + net.p_prb_prio * (b_min[sid] - b_ded[sid])
            for sid in ids
        )
        m.setObjective(
            net.w_c * cost + net.w_p * gp.quicksum(xi_prb[sid] for sid in ids),
            GRB.MINIMIZE,
        )

        # ----- Constraints -----------------------------------------------------
        m.addConstr(gp.quicksum(b_min[sid] for sid in ids) <= net.b_total)
        for sid in ids:
            sl = by_id[sid]
            m.addConstr(b_ded[sid] <= b_min[sid])
            # h_s=1 (URLLC): force b_ded = b_min (fully dedicated)
            m.addConstr(b_ded[sid] >= b_min[sid] * sl.h_s)
            # Soft throughput SLA via radio: want eta * b_min ≥ t_bar
            m.addConstr(xi_prb[sid] >= sl.t_bar - sl.eta * b_min[sid])

        m.optimize()
        if m.status != GRB.OPTIMAL:
            return PsResult()

        b_min_map = {sid: float(round(b_min[sid].x)) for sid in ids}
        b_ded_map = {sid: float(round(max(0.0, b_ded[sid].x))) for sid in ids}
        reserved = sum(b_min_map.values())
        extra = max(0.0, net.b_total - reserved) / len(ids)
        result = PsResult(
            b_min=b_min_map,
            b_ded=b_ded_map,
            extra=extra,
            b_max={sid: b_min_map[sid] + extra for sid in ids},
        )
        result.apply_to(slices)
        return result
