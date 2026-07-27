"""Layer 1 — Planning (PL): joint placement + initial compute + PRBs.

Gurobi flow: Model → Variables → setObjective → addConstr → optimize → read .x
"""

from __future__ import annotations

from typing import Sequence

import gurobipy as gp
from gurobipy import GRB

from ina.models import PlResult, Slice, SliceResources
from ina.network import Network


class PlanningLayer:
    """Long-term MILP: place CU/UPF/APP and size initial resources.

    Input
    -----
    slices : list[Slice]
        Each slice must have these planning-time fields:
        - ``t_bar`` (T̄): min throughput SLA (Mbps); sizes compute + PRBs
        - ``d_bar`` (D̄): max end-to-end delay SLA (ms); constrains placement
        - ``h_s``: hard isolation — 0 shared PRBs OK, 1 fully dedicated (URLLC)
        - ``eta_t0`` (η at t₀): planning radio efficiency (Mbps/PRB) for
          ``b_min`` via roughly ``eta_t0 * b_min ≥ t_bar``

        ``b_min`` = reserved PRBs for the slice; ``b_ded ≤ b_min`` is the
        dedicated subset (``h_s=1`` ⇒ all reserved PRBs dedicated). Sum of
        ``b_min`` across slices ≤ ``network.b_total``.

    Output
    ------
    PlResult with deploy_map + resources; also writes onto each Slice
    (``.placement``, ``.resources``).
    """

    def __init__(self, network: Network | None = None):
        # Shared infrastructure (capacities, costs, delays) — not slice SLAs
        self.network = network or Network()

    def solve(self, slices: Sequence[Slice]) -> PlResult:
        slices = list(slices)
        if not slices:
            return PlResult()

        net = self.network
        locs = net.locations  # [0, 1, 2]
        ids = [s.id for s in slices]
        by_id = {s.id: s for s in slices}

        m = gp.Model("PL_Layer1")
        m.setParam("OutputFlag", net.gurobi_output)
        # Placement × continuous size terms in cost/capacity are bilinear
        m.setParam("NonConvex", 2)

        # ----- Variables -------------------------------------------------------
        # Placement binaries: x[s,j]=1 ⇒ CU of slice s at site j (same for y/z)
        x = m.addVars(ids, locs, vtype=GRB.BINARY)  # CU-UP location
        y = m.addVars(ids, locs, vtype=GRB.BINARY)  # UPF location
        z = m.addVars(ids, locs, vtype=GRB.BINARY)  # APP location

        # Compute amounts (continuous)
        a_c_cu = m.addVars(ids, lb=0)  # CU CPU
        a_r_cu = m.addVars(ids, lb=net.min_r_cu)  # CU RAM
        a_c_upf = m.addVars(ids, lb=0)  # UPF CPU
        a_r_upf = m.addVars(ids, lb=net.min_r_upf)  # UPF RAM
        a_c_app = m.addVars(ids, lb=0)  # APP CPU
        a_r_app = m.addVars(ids, lb=0)  # APP RAM
        a_g_app = m.addVars(ids, lb=0)  # APP GPU

        # Radio: b_min = reserved PRBs; b_ded = dedicated subset (≤ b_min)
        b_ded = m.addVars(ids, vtype=GRB.INTEGER, lb=0)
        b_min = m.addVars(ids, vtype=GRB.INTEGER, lb=0)

        # Planned throughput / delay and SLA shortfalls (ξ ≥ 0 = how much we miss)
        t_plan = m.addVars(ids, lb=0)  # planned throughput (Mbps)
        d_plan = m.addVars(ids, lb=0)  # planned delay (ms)
        xi_d = m.addVars(ids, lb=0)  # delay shortfall: max(0, D − D̄)
        xi_prb = m.addVars(ids, lb=0)  # radio shortfall: max(0, T̄ − η₀·b_min)
        xi_com = m.addVars(ids, lb=0)  # compute shortfall: max(0, T̄ − T_plan)

        # Linearization helpers: xy[s,j,k]=1 iff CU at j and UPF at k
        xy = m.addVars(ids, locs, locs, vtype=GRB.BINARY)
        yz = m.addVars(ids, locs, locs, vtype=GRB.BINARY)  # UPF at k, APP at l

        # ----- Objective: minimize cost + weighted shortfalls ------------------
        # infra cost only paid at the chosen site (× binary placement)
        cost_infra = 0
        for sid in ids:
            for j in locs:
                cost_infra += (a_c_cu[sid] * net.p_c[j] + a_r_cu[sid] * net.p_r[j]) * x[sid, j]
                cost_infra += (a_c_upf[sid] * net.p_c[j] + a_r_upf[sid] * net.p_r[j]) * y[sid, j]
                cost_infra += (
                    a_c_app[sid] * net.p_c[j]
                    + a_r_app[sid] * net.p_r[j]
                    + a_g_app[sid] * net.p_g[j]
                ) * z[sid, j]
        # dedicated PRBs cost more than shared/priority PRBs
        cost_prb = gp.quicksum(
            net.p_prb_ded * b_ded[sid] + net.p_prb_prio * (b_min[sid] - b_ded[sid])
            for sid in ids
        )
        cost_penalty = gp.quicksum(xi_d[sid] + xi_prb[sid] + xi_com[sid] for sid in ids)
        m.setObjective(net.w_c * (cost_infra + cost_prb) + net.w_p * cost_penalty, GRB.MINIMIZE)

        # ----- Constraints (per slice; SLA from Slice object) ------------------
        for sid in ids:
            sl = by_id[sid]

            # Exactly one site for each NF
            m.addConstr(gp.quicksum(x[sid, j] for j in locs) == 1)
            m.addConstr(gp.quicksum(y[sid, j] for j in locs) == 1)
            m.addConstr(gp.quicksum(z[sid, j] for j in locs) == 1)

            # Throughput limited by each resource conversion
            m.addConstr(t_plan[sid] <= net.alpha_cu * a_c_cu[sid])
            m.addConstr(t_plan[sid] <= net.alpha_upf * a_c_upf[sid])
            m.addConstr(t_plan[sid] <= net.gamma_c * a_c_app[sid])
            m.addConstr(t_plan[sid] <= net.gamma_r * a_r_app[sid])
            m.addConstr(t_plan[sid] <= net.gamma_g * a_g_app[sid])

            # Hard min size so site capacity actually constrains placement
            # (otherwise the solver shrinks compute on a cheap/tight site and pays ξ_com).
            m.addConstr(a_c_cu[sid] >= sl.t_bar / net.alpha_cu)
            m.addConstr(a_c_upf[sid] >= sl.t_bar / net.alpha_upf)
            m.addConstr(a_c_app[sid] >= sl.t_bar / net.gamma_c)
            m.addConstr(a_r_app[sid] >= sl.t_bar / net.gamma_r)
            m.addConstr(a_g_app[sid] >= sl.t_bar / net.gamma_g)
            m.addConstr(t_plan[sid] >= sl.t_bar)  # meet throughput SLA in PL

            # PRB structure: dedicated ≤ reserved; URLLC (h_s=1) ⇒ all dedicated
            m.addConstr(b_ded[sid] <= b_min[sid])
            m.addConstr(b_min[sid] <= net.b_total)
            m.addConstr(b_ded[sid] >= b_min[sid] * sl.h_s)

            # Soft SLA: shortfall ≥ target − achieved (driven to 0 by large w_p)
            m.addConstr(xi_prb[sid] >= sl.t_bar - sl.eta_t0 * b_min[sid])
            m.addConstr(xi_com[sid] >= sl.t_bar - t_plan[sid])

            # Linearize products for N3 / N6 delay terms
            for j in locs:
                for k in locs:
                    m.addConstr(xy[sid, j, k] >= x[sid, j] + y[sid, k] - 1)
                    m.addConstr(xy[sid, j, k] <= x[sid, j])
                    m.addConstr(xy[sid, j, k] <= y[sid, k])
                    for loc_app in locs:
                        m.addConstr(yz[sid, k, loc_app] >= y[sid, k] + z[sid, loc_app] - 1)
                        m.addConstr(yz[sid, k, loc_app] <= y[sid, k])
                        m.addConstr(yz[sid, k, loc_app] <= z[sid, loc_app])

            # Delay from placement; shortfall if above slice.d_bar
            # RF: UE→DU; F1: DU@Edge→CU; N3: CU→UPF; N6: UPF→APP
            delay = (
                net.d_rf
                + gp.quicksum(net.d_f1[j] * x[sid, j] for j in locs)
                + gp.quicksum(net.d_n3[j, k] * xy[sid, j, k] for j in locs for k in locs)
                + gp.quicksum(
                    net.d_n6[k, loc_app] * yz[sid, k, loc_app]
                    for k in locs
                    for loc_app in locs
                )
            )
            m.addConstr(d_plan[sid] == delay)
            m.addConstr(xi_d[sid] >= d_plan[sid] - sl.d_bar)

        # Site capacity: sum of slices placed at j cannot exceed DC limits
        for j in locs:
            m.addConstr(
                gp.quicksum(a_c_cu[sid] * x[sid, j] + a_c_upf[sid] * y[sid, j] for sid in ids)
                <= net.c_n_capacity[j]
            )
            m.addConstr(
                gp.quicksum(a_r_cu[sid] * x[sid, j] + a_r_upf[sid] * y[sid, j] for sid in ids)
                <= net.r_n_capacity[j]
            )
            m.addConstr(gp.quicksum(a_c_app[sid] * z[sid, j] for sid in ids) <= net.c_a_capacity[j])
            m.addConstr(gp.quicksum(a_r_app[sid] * z[sid, j] for sid in ids) <= net.r_a_capacity[j])
            m.addConstr(gp.quicksum(a_g_app[sid] * z[sid, j] for sid in ids) <= net.g_a_capacity[j])

        m.addConstr(gp.quicksum(b_min[sid] for sid in ids) <= net.b_total)

        # ----- Solve & extract -------------------------------------------------
        m.optimize()

        result = PlResult()
        if m.status != GRB.OPTIMAL:
            return result

        for sid in ids:
            # argmax of binary placement vars
            loc = [0, 0, 0]
            for i, var in enumerate([x, y, z]):
                loc[i] = [j for j in locs if var[sid, j].x > 0.5][0]
            result.deploy_map[sid] = (loc[0], loc[1], loc[2])
            result.resources[sid] = SliceResources(
                a_c_cu=a_c_cu[sid].x,
                a_r_cu=a_r_cu[sid].x,
                a_c_upf=a_c_upf[sid].x,
                a_r_upf=a_r_upf[sid].x,
                a_c_app=a_c_app[sid].x,
                a_r_app=a_r_app[sid].x,
                a_g_app=a_g_app[sid].x,
                b_min=b_min[sid].x,
            )
        result.apply_to(slices)
        return result
