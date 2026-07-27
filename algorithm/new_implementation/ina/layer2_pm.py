"""Layer 2 — Medium-term (PM): re-allocate compute; placement fixed.

Does not change CU/UPF/APP sites or PRBs — only CPU/RAM/GPU amounts.
"""

from __future__ import annotations

from typing import Dict, Sequence

import gurobipy as gp
from gurobipy import GRB

from ina.models import DeployTuple, Slice, SliceResources
from ina.network import Network


class MediumLayer:
    """PM MILP: resize compute toward SLA and recent demand.

    Input
    -----
    slices : list[Slice]
        Each slice needs ``t_bar``, ``demand``, and a placement
        (``slice.placement`` or ``deployment_map[id]``).

    Output
    ------
    dict[slice_id, SliceResources]  (no ``b_min``; also updates ``slice.resources``)
    """

    def __init__(self, network: Network | None = None):
        self.network = network or Network()

    def solve(
        self,
        slices: Sequence[Slice],
        deployment_map: Dict[int, DeployTuple] | None = None,
    ) -> Dict[int, SliceResources]:
        slices = list(slices)
        if not slices:
            return {}

        # Resolve placement: explicit map wins, else Slice.placement from PL
        deploy = deployment_map or {}
        for s in slices:
            if s.id not in deploy:
                if s.placement is None:
                    raise ValueError(f"Slice {s.id} has no placement")
                deploy[s.id] = s.placement

        net = self.network
        locs = net.locations
        ids = [s.id for s in slices]
        by_id = {s.id: s for s in slices}

        m = gp.Model("PM_Layer2")
        m.setParam("OutputFlag", net.gurobi_output)

        # ----- Variables: compute only (no placement binaries) -----------------
        a_c_cu = m.addVars(ids, lb=0)
        a_r_cu = m.addVars(ids, lb=net.min_r_cu)
        a_c_upf = m.addVars(ids, lb=0)
        a_r_upf = m.addVars(ids, lb=net.min_r_upf)
        a_c_app = m.addVars(ids, lb=0)
        a_r_app = m.addVars(ids, lb=0)
        a_g_app = m.addVars(ids, lb=0)
        t_curr = m.addVars(ids, lb=0)  # achievable throughput with new compute
        xi_sla = m.addVars(ids, lb=0)  # shortfall vs T̄
        xi_dem = m.addVars(ids, lb=0)  # shortfall vs recent demand

        # ----- Objective: cheap compute at fixed sites + shortfall penalties ---
        cost = 0
        for sid in ids:
            loc_cu, loc_upf, loc_app = deploy[sid]
            cost += (
                a_c_cu[sid] * net.p_c[loc_cu]
                + a_r_cu[sid] * net.p_r[loc_cu]
                + a_c_upf[sid] * net.p_c[loc_upf]
                + a_r_upf[sid] * net.p_r[loc_upf]
                + a_c_app[sid] * net.p_c[loc_app]
                + a_r_app[sid] * net.p_r[loc_app]
                + a_g_app[sid] * net.p_g[loc_app]
            )
        m.setObjective(
            net.w_c * cost
            + net.w_p * gp.quicksum(xi_sla[sid] + net.beta_demand * xi_dem[sid] for sid in ids),
            GRB.MINIMIZE,
        )

        # ----- Capacity at each DC (sum slices whose NF is placed there) -------
        for j in locs:
            at_cu = [sid for sid in ids if deploy[sid][0] == j]
            at_upf = [sid for sid in ids if deploy[sid][1] == j]
            at_app = [sid for sid in ids if deploy[sid][2] == j]
            m.addConstr(
                gp.quicksum(a_c_cu[sid] for sid in at_cu)
                + gp.quicksum(a_c_upf[sid] for sid in at_upf)
                <= net.c_n_capacity[j]
            )
            m.addConstr(
                gp.quicksum(a_r_cu[sid] for sid in at_cu)
                + gp.quicksum(a_r_upf[sid] for sid in at_upf)
                <= net.r_n_capacity[j]
            )
            m.addConstr(gp.quicksum(a_c_app[sid] for sid in at_app) <= net.c_a_capacity[j])
            m.addConstr(gp.quicksum(a_r_app[sid] for sid in at_app) <= net.r_a_capacity[j])
            m.addConstr(gp.quicksum(a_g_app[sid] for sid in at_app) <= net.g_a_capacity[j])

        # ----- Throughput coupling + soft SLA / demand -------------------------
        for sid in ids:
            sl = by_id[sid]
            m.addConstr(t_curr[sid] <= net.alpha_cu * a_c_cu[sid])
            m.addConstr(t_curr[sid] <= net.alpha_upf * a_c_upf[sid])
            m.addConstr(t_curr[sid] <= net.gamma_c * a_c_app[sid])
            m.addConstr(t_curr[sid] <= net.gamma_r * a_r_app[sid])
            m.addConstr(t_curr[sid] <= net.gamma_g * a_g_app[sid])
            m.addConstr(xi_sla[sid] >= sl.t_bar - t_curr[sid])
            m.addConstr(xi_dem[sid] >= sl.demand - t_curr[sid])

        m.optimize()
        if m.status != GRB.OPTIMAL:
            return {}

        out: Dict[int, SliceResources] = {}
        for sid in ids:
            res = SliceResources(
                a_c_cu=a_c_cu[sid].x,
                a_r_cu=a_r_cu[sid].x,
                a_c_upf=a_c_upf[sid].x,
                a_r_upf=a_r_upf[sid].x,
                a_c_app=a_c_app[sid].x,
                a_r_app=a_r_app[sid].x,
                a_g_app=a_g_app[sid].x,
                # b_min intentionally omitted — radio is handled by PS
            )
            out[sid] = res
            by_id[sid].resources = res
        return out
