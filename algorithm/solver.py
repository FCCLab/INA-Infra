import gurobipy as gp
from gurobipy import GRB
import random
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
import time

"""
Three-layer optimization workflow (paper-aligned implementation):
1) Layer 1 PL: `run_layer_1_pl(active_slice_info)`
   Joint deployment + initial resource planning (objective Eq. (5), constraints Eq. (6-1)~(6-12), delay Eq. (7)).
2) Layer 2 PM: `run_layer_2_pm(active_slice_info, deployment_map, t_demand)`
   PM-timescale compute resource re-allocation with fixed deployment (objective Eq. (8), constraints Eq. (9-1)~(9-8)).
3) Layer 3 PS: `run_layer_3_ps(eta_realtime, active_slice_info)`
   PS-timescale PRB reservation and scheduling (objective Eq. (10), constraints Eq. (11-1)~(11-4), eta update Eq. (12)).
"""

plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42

J_LOCATIONS = [0, 1, 2]
S_SLICES = range(1, 5)

# Capacity parameters (paper resource-capacity symbols)
C_N_CAPACITY = {0: 400, 1: 600, 2: 2500}
R_N_CAPACITY = {0: 10240, 1: 20480, 2: 102400}
APP_RATIO = 2.0
C_A_CAPACITY = {0: 400, 1: 600, 2: 5000}
R_A_CAPACITY = {k: v * 2.0 for k, v in R_N_CAPACITY.items()}
G_A_CAPACITY = {0: 150, 1: 200, 2: 1200}

# Cost coefficients in objective
P_C = {0: 0.5, 1: 0.05, 2: 0.001}
P_R = {0: 0.1, 1: 0.01, 2: 0.002}
P_G = {0: 1.0, 1: 0.5, 2: 0.1}
P_PRB_DED = 0.5
P_PRB_PRIO = 0.1

# Throughput conversion coefficients in compute model
ALPHA_CU = 1.02
ALPHA_UPF = 0.81
GAMMA_C = 0.5
GAMMA_R = 0.008
GAMMA_G = 1.0
MIN_R_CU = 10
MIN_R_UPF = 10

# Delay coefficients in end-to-end delay equation
D_F1 = {0: 1, 1: 15, 2: 30}
D_N3 = {(0, 0): 1, (0, 1): 10, (0, 2): 20, (1, 0): 10, (1, 1): 1, (1, 2): 10, (2, 0): 20, (2, 1): 10, (2, 2): 1}
D_N6 = {
    (0, 0): 5, (0, 1): 15, (0, 2): 30,
    (1, 0): 15, (1, 1): 5, (1, 2): 15,
    (2, 0): 30, (2, 1): 15, (2, 2): 5
}

B_TOTAL = 273
W_C = 1.0
W_P = 1000.0
BETA_DEMAND = 0.1

NUM_PM_CYCLES = 1000  # 1000 cycles * 10 steps = 10000 steps
PS_STEPS_PER_PM = 10

ACTIVATION_SCHEDULE = {
    0: [1, 2, 3, 4]
}

MCS_TABLE = {
    0: (2, 120), 1: (2, 157), 2: (2, 193), 3: (2, 251), 4: (2, 308),
    5: (2, 379), 6: (2, 449), 7: (2, 526), 8: (2, 602), 9: (2, 679),
    10: (4, 340), 11: (4, 378), 12: (4, 434), 13: (4, 490), 14: (4, 553),
    15: (4, 616), 16: (4, 658),
    17: (6, 438), 18: (6, 466), 19: (6, 517), 20: (6, 567), 21: (6, 616),
    22: (6, 666), 23: (6, 719), 24: (6, 772), 25: (6, 822), 26: (6, 873),
    27: (6, 910), 28: (6, 948)
}
SCS_KHZ = 30
MIMO_LAYERS = 2
OVERHEAD = 0.14
SCALING_FACTOR = 1

VIVID_COLORS = ['#FF0000', '#00CC00', '#0000FF', '#FF00FF']


def calculate_eta(mcs_index):
    if mcs_index not in MCS_TABLE: return 0
    Qm, R_1024 = MCS_TABLE[mcs_index]
    R = R_1024 / 1024.0
    res_per_sec = 12 * 28000
    eta = 1e-6 * MIMO_LAYERS * Qm * SCALING_FACTOR * R * res_per_sec * (1 - OVERHEAD)
    return eta


SLICES_INFO = {}
random.seed(1000)

for s in S_SLICES:
    if s == 1:
        t_min = 10;
        d_max = 100;
        hard_iso = 0;
        s_type = "mMTC"
    elif s == 2:
        t_min = 40;
        d_max = 10;
        hard_iso = 1;
        s_type = "URLLC"
    elif s == 3:
        t_min = 100;
        d_max = 35;
        hard_iso = 0;
        s_type = "eMBB"
    elif s == 4:
        t_min = 70;
        d_max = 25;
        hard_iso = 0;
        s_type = "eMBB(Edge)"

    init_mcs = random.randint(10, 20)
    eta_init = calculate_eta(init_mcs)
    SLICES_INFO[s] = {'T_bar': t_min, 'D_bar': d_max, 'H_s': hard_iso, 'eta_t0': eta_init, 'Type': s_type}


def run_layer_1_pl(active_slice_info):
    """
    Layer 1 PL optimization.

    Purpose:
    - Jointly decide CU/UPF/APP placement and initial resource allocation.
    - Build baseline PRB reservation for active slices.

    Input:
    - active_slice_info: dict[slice_id] -> slice parameters
      (`T_bar`, `D_bar`, `H_s`, `eta_t0`, ...).

    Output:
    - deploy_map[s] = (loc_cu, loc_upf, loc_app)
    - init_resources[s] = initial planned compute resources + `b_min`
    """
    if not active_slice_info: return {}, {}
    active_slices = sorted(active_slice_info.keys())
    m = gp.Model("PL_Layer1");
    m.setParam('OutputFlag', 0)

    # Paper variables: x_{s,j}, y_{s,j}, z_{s,j} (Sec. III-B-1, before Eq. (5))
    x = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    y = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    z = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    # Paper variables: c_s^C, r_s^C, c_s^U, r_s^U, c_s^A, r_s^A, g_s^A (before Eq. (5))
    a_c_cu = m.addVars(active_slices, lb=0);
    a_r_cu = m.addVars(active_slices, lb=MIN_R_CU)
    a_c_upf = m.addVars(active_slices, lb=0);
    a_r_upf = m.addVars(active_slices, lb=MIN_R_UPF)
    a_c_app = m.addVars(active_slices, lb=0);
    a_r_app = m.addVars(active_slices, lb=0);
    a_g_app = m.addVars(active_slices, lb=0)
    # Paper variables: b_hat_s (dedicated) and b_tilde_s (minimum guaranteed) (Eq. (4), Eq. (6-12))
    b_ded = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    b_min = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    # Paper variables: T_s, D_s, xi_s^D, xi_s^R, xi_s^C (Eq. (5), Eq. (6-8)~(6-11))
    T_plan = m.addVars(active_slices, lb=0);
    D_plan = m.addVars(active_slices, lb=0)
    xi_D = m.addVars(active_slices, lb=0);
    xi_PRB = m.addVars(active_slices, lb=0);
    xi_Com = m.addVars(active_slices, lb=0)
    # Auxiliary binaries for linearizing Eq. (7): x_{s,j}y_{s,k}, y_{s,k}z_{s,l}
    xy = m.addVars(active_slices, J_LOCATIONS, J_LOCATIONS, vtype=GRB.BINARY)
    yz = m.addVars(active_slices, J_LOCATIONS, J_LOCATIONS, vtype=GRB.BINARY)

    # Paper objective Eq. (5): minimize W_C*(C_I + C_R) + W_P*(xi^D + xi^R + xi^C)
    cost_infra = 0
    for s in active_slices:
        for j in J_LOCATIONS:
            cost_infra += (a_c_cu[s] * P_C[j] + a_r_cu[s] * P_R[j]) * x[s, j]
            cost_infra += (a_c_upf[s] * P_C[j] + a_r_upf[s] * P_R[j]) * y[s, j]
            cost_infra += (a_c_app[s] * P_C[j] + a_r_app[s] * P_R[j] + a_g_app[s] * P_G[j]) * z[s, j]
    cost_prb = gp.quicksum(P_PRB_DED * b_ded[s] + P_PRB_PRIO * (b_min[s] - b_ded[s]) for s in active_slices)
    cost_penalty = gp.quicksum(xi_D[s] + xi_PRB[s] + xi_Com[s] for s in active_slices)
    m.setObjective(W_C * (cost_infra + cost_prb) + W_P * cost_penalty, GRB.MINIMIZE)

    for s in active_slices:
        # Eq. (6-1): each CU-UP / UPF / APP is deployed at exactly one node
        m.addConstr(gp.quicksum(x[s, j] for j in J_LOCATIONS) == 1)
        m.addConstr(gp.quicksum(y[s, j] for j in J_LOCATIONS) == 1)
        m.addConstr(gp.quicksum(z[s, j] for j in J_LOCATIONS) == 1)
        # Eq. (6-8): throughput bottleneck model T_s <= min{...}
        m.addConstr(T_plan[s] <= ALPHA_CU * a_c_cu[s])
        m.addConstr(T_plan[s] <= ALPHA_UPF * a_c_upf[s])
        m.addConstr(T_plan[s] <= GAMMA_C * a_c_app[s])
        m.addConstr(T_plan[s] <= GAMMA_R * a_r_app[s])
        m.addConstr(T_plan[s] <= GAMMA_G * a_g_app[s])
        # Eq. (6-12): PRB logic (0 <= b_hat <= b_tilde, hard isolation: b_hat >= b_tilde * H_s)
        m.addConstr(b_ded[s] <= b_min[s])
        m.addConstr(b_min[s] <= B_TOTAL)
        m.addConstr(b_ded[s] >= b_min[s] * active_slice_info[s]['H_s'])
        # Eq. (6-10): xi_s^C >= T_bar_s - T_s
        # Eq. (6-11): xi_s^R >= T_bar_s - eta_s(t0) * b_tilde_s
        m.addConstr(xi_PRB[s] >= active_slice_info[s]['T_bar'] - active_slice_info[s]['eta_t0'] * b_min[s])
        m.addConstr(xi_Com[s] >= active_slice_info[s]['T_bar'] - T_plan[s])
        # Linearization for Eq. (7): xy = x*y, yz = y*z
        for j in J_LOCATIONS:
            for k in J_LOCATIONS:
                m.addConstr(xy[s, j, k] >= x[s, j] + y[s, k] - 1)
                m.addConstr(xy[s, j, k] <= x[s, j])
                m.addConstr(xy[s, j, k] <= y[s, k])
                for l in J_LOCATIONS:
                    m.addConstr(yz[s, k, l] >= y[s, k] + z[s, l] - 1)
                    m.addConstr(yz[s, k, l] <= y[s, k])
                    m.addConstr(yz[s, k, l] <= z[s, l])
        # Eq. (7): end-to-end delay D_s aggregation (F1 + N3 + N6)
        delay_expr = (gp.quicksum(D_F1[j] * x[s, j] for j in J_LOCATIONS) +
                      gp.quicksum(D_N3[j, k] * xy[s, j, k] for j in J_LOCATIONS for k in J_LOCATIONS) +
                      gp.quicksum(D_N6[k, l] * yz[s, k, l] for k in J_LOCATIONS for l in J_LOCATIONS))
        m.addConstr(D_plan[s] == delay_expr)
        # Eq. (6-9): xi_s^D >= D_s - D_bar_s
        m.addConstr(xi_D[s] >= D_plan[s] - active_slice_info[s]['D_bar'])

    # Eq. (6-2)~(6-6): NF/App resource capacity constraints per node
    for j in J_LOCATIONS:
        m.addConstr(gp.quicksum(a_c_cu[s] * x[s, j] + a_c_upf[s] * y[s, j] for s in active_slices) <= C_N_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_cu[s] * x[s, j] + a_r_upf[s] * y[s, j] for s in active_slices) <= R_N_CAPACITY[j])
        m.addConstr(gp.quicksum(a_c_app[s] * z[s, j] for s in active_slices) <= C_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_app[s] * z[s, j] for s in active_slices) <= R_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_g_app[s] * z[s, j] for s in active_slices) <= G_A_CAPACITY[j])

    # Eq. (6-12): total minimum guaranteed PRBs cannot exceed B
    m.addConstr(gp.quicksum(b_min[s] for s in active_slices) <= B_TOTAL)
    m.optimize()

    deploy_map = {};
    init_resources = {}
    if m.status == GRB.OPTIMAL:
        for s in active_slices:
            loc = [0, 0, 0]
            for i, var in enumerate([x, y, z]): loc[i] = [j for j in J_LOCATIONS if var[s, j].x > 0.5][0]
            deploy_map[s] = tuple(loc)
            init_resources[s] = {
                'a_c_cu': a_c_cu[s].x, 'a_r_cu': a_r_cu[s].x,
                'a_c_upf': a_c_upf[s].x, 'a_r_upf': a_r_upf[s].x,
                'a_c_app': a_c_app[s].x, 'a_r_app': a_r_app[s].x, 'a_g_app': a_g_app[s].x,
                'b_min': b_min[s].x
            }
    return deploy_map, init_resources


def run_layer_2_pm(active_slice_info, deployment_map, t_demand):
    """
    Layer 2 PM optimization.

    Purpose:
    - Re-optimize compute resources per PM cycle with fixed deployment.
    - Balance infrastructure cost, SLA deficit, and demand-tracking deficit.

    Inputs:
    - active_slice_info: dict[slice_id] -> slice parameters.
    - deployment_map: dict from Layer 1, fixed locations for CU/UPF/APP.
    - t_demand: predicted throughput demand per slice in next PM cycle.

    Output:
    - new_res[s]: updated compute resource allocation.
    """
    active_slices = sorted(active_slice_info.keys())
    m = gp.Model("PM_Layer2");
    m.setParam('OutputFlag', 0)
    # Paper variables: c_s^C(t), r_s^C(t), c_s^U(t), r_s^U(t), c_s^A(t), r_s^A(t), g_s^A(t) (Eq. (8), Eq. (9))
    a_c_cu = m.addVars(active_slices, lb=0);
    a_r_cu = m.addVars(active_slices, lb=MIN_R_CU)
    a_c_upf = m.addVars(active_slices, lb=0);
    a_r_upf = m.addVars(active_slices, lb=MIN_R_UPF)
    a_c_app = m.addVars(active_slices, lb=0);
    a_r_app = m.addVars(active_slices, lb=0);
    a_g_app = m.addVars(active_slices, lb=0)
    # Paper variables: T_s(t), xi_s^{SLA-T}(t), xi_s^{Demand-T}(t) (Eq. (8), Eq. (9-6)~(9-8))
    T_curr = m.addVars(active_slices, lb=0);
    xi_SLA = m.addVars(active_slices, lb=0);
    xi_Dem = m.addVars(active_slices, lb=0)

    # Paper objective Eq. (8): minimize W_C*C_I(s,t) + W_P*(xi^{SLA-T} + beta*xi^{Demand-T})
    cost_expr = 0
    for s in active_slices:
        loc_cu, loc_upf, loc_app = deployment_map[s]
        cost_expr += (a_c_cu[s] * P_C[loc_cu] + a_r_cu[s] * P_R[loc_cu] +
                      a_c_upf[s] * P_C[loc_upf] + a_r_upf[s] * P_R[loc_upf] +
                      a_c_app[s] * P_C[loc_app] + a_r_app[s] * P_R[loc_app] + a_g_app[s] * P_G[loc_app])

    m.setObjective(W_C * cost_expr + W_P * gp.quicksum(xi_SLA[s] + BETA_DEMAND * xi_Dem[s] for s in active_slices),
                   GRB.MINIMIZE)

    for j in J_LOCATIONS:
        s_at_j_cu = [s for s in active_slices if deployment_map[s][0] == j]
        s_at_j_upf = [s for s in active_slices if deployment_map[s][1] == j]
        s_at_j_app = [s for s in active_slices if deployment_map[s][2] == j]

        # Eq. (9-1), Eq. (9-2): NF-side dynamic capacity constraints
        m.addConstr(
            gp.quicksum(a_c_cu[s] for s in s_at_j_cu) + gp.quicksum(a_c_upf[s] for s in s_at_j_upf) <= C_N_CAPACITY[j])
        m.addConstr(
            gp.quicksum(a_r_cu[s] for s in s_at_j_cu) + gp.quicksum(a_r_upf[s] for s in s_at_j_upf) <= R_N_CAPACITY[j])

        # Eq. (9-3), Eq. (9-4), Eq. (9-5): APP-side dynamic capacity constraints
        m.addConstr(gp.quicksum(a_c_app[s] for s in s_at_j_app) <= C_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_app[s] for s in s_at_j_app) <= R_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_g_app[s] for s in s_at_j_app) <= G_A_CAPACITY[j])

    for s in active_slices:
        # Eq. (9-6): throughput bottleneck model at PM time t
        m.addConstr(T_curr[s] <= ALPHA_CU * a_c_cu[s])
        m.addConstr(T_curr[s] <= ALPHA_UPF * a_c_upf[s])
        m.addConstr(T_curr[s] <= GAMMA_C * a_c_app[s])
        m.addConstr(T_curr[s] <= GAMMA_R * a_r_app[s])
        m.addConstr(T_curr[s] <= GAMMA_G * a_g_app[s])
        # Eq. (9-7): SLA throughput shortfall slack
        # Eq. (9-8): demand-tracking shortfall slack
        m.addConstr(xi_SLA[s] >= active_slice_info[s]['T_bar'] - T_curr[s])
        m.addConstr(xi_Dem[s] >= t_demand[s] - T_curr[s])
    m.optimize()
    new_res = {}
    if m.status == GRB.OPTIMAL:
        for s in active_slices:
            new_res[s] = {
                'a_c_cu': a_c_cu[s].x, 'a_r_cu': a_r_cu[s].x,
                'a_c_upf': a_c_upf[s].x, 'a_r_upf': a_r_upf[s].x,
                'a_c_app': a_c_app[s].x, 'a_r_app': a_r_app[s].x, 'a_g_app': a_g_app[s].x
            }
    return new_res


def run_layer_3_ps(eta_realtime, active_slice_info):
    """
    Layer 3 PS optimization.

    Purpose:
    - Re-optimize PRB reservation at each PS step from real-time eta.

    Inputs:
    - eta_realtime[s]: effective spectral efficiency input (paper \bar{eta}_s(t), Eq. (12)).
    - active_slice_info: dict[slice_id] -> slice parameters.

    Output:
    - res[s] = optimized minimum PRB reservation `b_min`.
    """
    active_slices = sorted(active_slice_info.keys())
    m = gp.Model("PS_Layer3");
    m.setParam('OutputFlag', 0)
    # Paper variables: b_hat_s(t), b_tilde_s(t), xi_s^{PRB}(t) (Eq. (10), Eq. (11))
    b_ded = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    b_min = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    xi_prb = m.addVars(active_slices, lb=0)
    # Paper objective Eq. (10): minimize W_C*C_R(s,t) + W_P*xi_s^{PRB}(t)
    cost = gp.quicksum(P_PRB_DED * b_ded[s] + P_PRB_PRIO * (b_min[s] - b_ded[s]) for s in active_slices)
    m.setObjective(W_C * cost + W_P * gp.quicksum(xi_prb[s] for s in active_slices), GRB.MINIMIZE)
    # Eq. (11-2): global PRB budget
    m.addConstr(gp.quicksum(b_min[s] for s in active_slices) <= B_TOTAL)
    for s in active_slices:
        # Eq. (11-1), Eq. (11-3): PRB logic + hard-isolation requirement
        m.addConstr(b_ded[s] <= b_min[s])
        m.addConstr(b_ded[s] >= b_min[s] * active_slice_info[s]['H_s'])
        # Eq. (11-4): PRB-side throughput shortfall slack
        m.addConstr(xi_prb[s] >= active_slice_info[s]['T_bar'] - eta_realtime[s] * b_min[s])
    m.optimize()
    res = {}
    if m.status == GRB.OPTIMAL:
        for s in active_slices: res[s] = b_min[s].x
    return res


if __name__ == "__main__":
    current_deploy_map = {}
    current_resources = {}
    baseline_resources = {}
    periodical_resources = {}

    active_slices = []

    history_t_actual = {s: [] for s in S_SLICES}
    history_violation = {s: [] for s in S_SLICES}

    history_t_baseline = {s: [] for s in S_SLICES}
    history_vio_baseline = {s: [] for s in S_SLICES}

    history_t_periodical = {s: [] for s in S_SLICES}
    history_vio_periodical = {s: [] for s in S_SLICES}

    exec_times = {'Layer1': [], 'Layer2': [], 'Layer3': []}
    current_mcs_state = {s: random.randint(10, 20) for s in S_SLICES}

    print("Starting Simulation (10,000 Steps, All Slices Active)...")

    for pm_cycle in range(NUM_PM_CYCLES):
        if pm_cycle % 50 == 0:
            print(f"Processing PM Cycle {pm_cycle}/{NUM_PM_CYCLES}...")

        new_slices = ACTIVATION_SCHEDULE.get(pm_cycle, [])
        if new_slices:
            active_slices.extend(new_slices)
            active_slices.sort()
            active_slice_info = {s: SLICES_INFO[s] for s in active_slices}

            t_start = time.time()
            current_deploy_map, new_baselines = run_layer_1_pl(active_slice_info)
            exec_times['Layer1'].append(time.time() - t_start)

            current_resources.update(new_baselines)
            baseline_resources.update(new_baselines)
            periodical_resources.update(new_baselines)

        radio_demands_buffer = {s: [] for s in active_slices}
        radio_demands_buffer_p = {s: [] for s in active_slices}

        current_compute_caps = {}
        baseline_compute_caps = {}
        periodical_compute_caps = {}

        for s in active_slices:
            res = current_resources[s]
            cap = min(ALPHA_CU * res['a_c_cu'], ALPHA_UPF * res['a_c_upf'],
                      GAMMA_C * res['a_c_app'], GAMMA_R * res['a_r_app'], GAMMA_G * res['a_g_app'])
            current_compute_caps[s] = cap

            res_b = baseline_resources[s]
            cap_b = min(ALPHA_CU * res_b['a_c_cu'], ALPHA_UPF * res_b['a_c_upf'],
                        GAMMA_C * res_b['a_c_app'], GAMMA_R * res_b['a_r_app'], GAMMA_G * res_b['a_g_app'])
            baseline_compute_caps[s] = cap_b

            res_p = periodical_resources[s]
            cap_p = min(ALPHA_CU * res_p['a_c_cu'], ALPHA_UPF * res_p['a_c_upf'],
                        GAMMA_C * res_p['a_c_app'], GAMMA_R * res_p['a_r_app'], GAMMA_G * res_p['a_g_app'])
            periodical_compute_caps[s] = cap_p

        # Single baseline: run PS once at PM-cycle start.
        active_slice_info = {s: SLICES_INFO[s] for s in active_slices}
        eta_cycle_snapshot = {}
        for s in S_SLICES:
            if s in active_slices:
                mcs = current_mcs_state[s]
                eta_cycle_snapshot[s] = calculate_eta(mcs)
            else:
                eta_cycle_snapshot[s] = 0

        periodical_b_min_map = run_layer_3_ps(eta_cycle_snapshot, active_slice_info)
        total_reserved_p = sum(periodical_b_min_map.values())
        slack_p = max(0, B_TOTAL - total_reserved_p)

        for ps_step in range(PS_STEPS_PER_PM):
            eta_now = {}
            for s in S_SLICES:
                if s in active_slices:
                    prev_mcs = current_mcs_state[s]
                    delta = random.randint(-5, 5)
                    mcs = max(5, min(prev_mcs + delta, 28))
                    current_mcs_state[s] = mcs
                    eta_now[s] = calculate_eta(mcs)
                else:
                    eta_now[s] = 0

            t_start = time.time()
            b_min_map = run_layer_3_ps(eta_now, active_slice_info)
            exec_times['Layer3'].append(time.time() - t_start)

            total_reserved = sum(b_min_map.values())
            slack = max(0, B_TOTAL - total_reserved)

            # Shared slack PRB is distributed proportionally to SLA throughput targets.
            total_sla_demand = sum(SLICES_INFO[s]['T_bar'] for s in active_slices) if active_slices else 1
            total_reserved_base = sum(baseline_resources[s]['b_min'] for s in active_slices)
            slack_base = max(0, B_TOTAL - total_reserved_base)

            for s in S_SLICES:
                if s in active_slices:
                    weight = SLICES_INFO[s]['T_bar'] / total_sla_demand

                    extra_prb = slack * weight
                    radio_pot = (b_min_map[s] + extra_prb) * eta_now[s]
                    actual_t = min(radio_pot, current_compute_caps[s])
                    violation = max(0, SLICES_INFO[s]['T_bar'] - actual_t)

                    radio_demands_buffer[s].append(radio_pot)
                    history_t_actual[s].append(actual_t)
                    history_violation[s].append(violation)

                    extra_base = slack_base * weight
                    b_min_static = baseline_resources[s]['b_min']
                    radio_pot_base = (b_min_static + extra_base) * eta_now[s]
                    actual_t_base = min(radio_pot_base, baseline_compute_caps[s])
                    violation_base = max(0, SLICES_INFO[s]['T_bar'] - actual_t_base)

                    history_t_baseline[s].append(actual_t_base)
                    history_vio_baseline[s].append(violation_base)

                    extra_p = slack_p * weight
                    radio_pot_p = (periodical_b_min_map[s] + extra_p) * eta_now[s]
                    actual_t_p = min(radio_pot_p, periodical_compute_caps[s])
                    violation_p = max(0, SLICES_INFO[s]['T_bar'] - actual_t_p)

                    radio_demands_buffer_p[s].append(radio_pot_p)
                    history_t_periodical[s].append(actual_t_p)
                    history_vio_periodical[s].append(violation_p)

                else:
                    history_t_actual[s].append(0);
                    history_violation[s].append(0)
                    history_t_baseline[s].append(0);
                    history_vio_baseline[s].append(0)
                    history_t_periodical[s].append(0);
                    history_vio_periodical[s].append(0)

        if active_slices:
            next_demand = {s: np.mean(radio_demands_buffer[s]) for s in active_slices}
            t_start = time.time()
            current_resources = run_layer_2_pm(active_slice_info, current_deploy_map, next_demand)
            exec_times['Layer2'].append(time.time() - t_start)

            next_demand_p = {s: np.mean(radio_demands_buffer_p[s]) for s in active_slices}
            periodical_resources = run_layer_2_pm(active_slice_info, current_deploy_map, next_demand_p)

    print("Simulation Finished.")

    print("\n" + "=" * 50)
    print("SIMULATION RESULTS (Averaged over 10,000 Steps)")
    print("=" * 50)

    total_steps = NUM_PM_CYCLES * PS_STEPS_PER_PM

    # Average system violation sum per step (Mbps).
    avg_vio_dynamic = sum(sum(history_violation[s]) for s in S_SLICES) / total_steps
    avg_vio_static = sum(sum(history_vio_baseline[s]) for s in S_SLICES) / total_steps
    avg_vio_periodical = sum(sum(history_vio_periodical[s]) for s in S_SLICES) / total_steps

    # Average system throughput sum per step (Mbps).
    avg_thr_dynamic = sum(sum(history_t_actual[s]) for s in S_SLICES) / total_steps
    avg_thr_static = sum(sum(history_t_baseline[s]) for s in S_SLICES) / total_steps
    avg_thr_periodical = sum(sum(history_t_periodical[s]) for s in S_SLICES) / total_steps


    # Non-violation rate across all slices and all time steps.
    def calc_non_violation_rate(history_dict):
        total_valid_samples = 0
        non_violation_samples = 0
        for s in S_SLICES:
            samples = history_dict[s]
            total_valid_samples += len(samples)
            non_violation_samples += sum(1 for v in samples if v < 1e-5)

        return (non_violation_samples / total_valid_samples) * 100 if total_valid_samples > 0 else 0


    rate_dynamic = calc_non_violation_rate(history_violation)
    rate_static = calc_non_violation_rate(history_vio_baseline)
    rate_periodical = calc_non_violation_rate(history_vio_periodical)

    print(f"{'Metric':<30} | {'Multi':<12} | {'Static':<10} | {'Single'}")
    print("-" * 70)
    print(
        f"{'Avg Violation (Mbps/step)':<30} | {avg_vio_dynamic:<12.4f} | {avg_vio_static:<10.4f} | {avg_vio_periodical:.4f}")
    print(
        f"{'Avg Throughput (Mbps/step)':<30} | {avg_thr_dynamic:<12.4f} | {avg_thr_static:<10.4f} | {avg_thr_periodical:.4f}")
    print(
        f"{'Non-Violation Rate (%)':<30} | {rate_dynamic:<12.2f} | {rate_static:<10.2f} | {rate_periodical:.2f}")
    print("-" * 70)

    # Average execution time per optimization layer.
    avg_l1 = np.mean(exec_times['Layer1']) if exec_times['Layer1'] else 0
    avg_l2 = np.mean(exec_times['Layer2']) if exec_times['Layer2'] else 0
    avg_l3 = np.mean(exec_times['Layer3']) if exec_times['Layer3'] else 0

    print("\nAverage Execution Times (Multi Algorithm):")
    print(f"Layer 1 (PL): {avg_l1:.6f} s")
    print(f"Layer 2 (PM): {avg_l2:.6f} s")
    print(f"Layer 3 (PS): {avg_l3:.6f} s")
    print("=" * 50 + "\n")

    # Plotting style configuration.
    FONT_AXIS_LABEL = 36
    FONT_Y_TICKS = 34
    FONT_X_TICKS = 42
    FONT_BAR_TEXT = 36
    FONT_LEGEND = 28


    def get_color(s):
        return VIVID_COLORS[s - 1]


    time_axis = list(range(total_steps))

    plt.rcParams.update({'font.size': 16})

    # Figure 1: Real-time throughput time series.
    plt.figure(figsize=(18, 10))
    for s in S_SLICES:
        plt.plot(time_axis, history_t_actual[s], linewidth=3, label=f"Slice {s}", color=get_color(s), alpha=0.9)

    plt.ylabel("Throughput (Mbps)", fontsize=FONT_AXIS_LABEL)
    plt.xlabel("Time Steps", fontsize=FONT_AXIS_LABEL)
    plt.tick_params(axis='both', labelsize=FONT_Y_TICKS)

    plt.legend(loc='upper right', fontsize=FONT_LEGEND, ncol=4)
    plt.grid(True, linestyle='--', alpha=0.4)
    plt.tight_layout()
    plt.show()

    # Shared settings for bar charts.
    methods = ['Multi', 'Static', 'Single']
    x_pos = np.arange(len(methods))
    colors = ['#2ca02c', '#d62728', '#ff7f0e']

    # Figure 2: Average violation bar chart.
    plt.figure(figsize=(16, 12))
    violations = [avg_vio_dynamic, avg_vio_static, avg_vio_periodical]
    bars1 = plt.bar(x_pos, violations, color=colors, alpha=0.8, width=0.5)

    plt.ylabel("System Violation Sum (Mbps)", fontsize=FONT_AXIS_LABEL)

    plt.xticks(x_pos, methods, fontsize=FONT_X_TICKS)

    plt.tick_params(axis='y', labelsize=FONT_Y_TICKS)
    plt.grid(axis='y', linestyle='--', alpha=0.4)

    if max(violations) > 0:
        plt.ylim(0, max(violations) * 1.35)

    for bar in bars1:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2., height,
                 f'{height:.2f}',
                 ha='center', va='bottom',
                 fontsize=FONT_BAR_TEXT,
                 fontweight='bold',
                 color='black')

    plt.tight_layout()
    plt.savefig('avg_violation.pdf', dpi=300, bbox_inches='tight')
    plt.show()

    # Figure 3: Average throughput bar chart.
    plt.figure(figsize=(16, 12))
    throughputs = [avg_thr_dynamic, avg_thr_static, avg_thr_periodical]
    bars2 = plt.bar(x_pos, throughputs, color=colors, alpha=0.8, width=0.5)

    plt.ylabel("System Throughput Sum (Mbps)", fontsize=FONT_AXIS_LABEL)

    plt.xticks(x_pos, methods, fontsize=FONT_X_TICKS)

    plt.tick_params(axis='y', labelsize=FONT_Y_TICKS)
    plt.grid(axis='y', linestyle='--', alpha=0.4)

    if max(throughputs) > 0:
        plt.ylim(0, max(throughputs) * 1.35)

    for bar in bars2:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2., height,
                 f'{height:.2f}',
                 ha='center', va='bottom',
                 fontsize=FONT_BAR_TEXT,
                 fontweight='bold',
                 color='black')

    plt.tight_layout()
    plt.savefig('avg_throughput.pdf', dpi=300, bbox_inches='tight')
    plt.show()


    # Figure 4: CDF of violation ratio.
    def get_all_violation_ratios(history_dict):
        all_ratios = []
        for s in S_SLICES:
            t_bar = SLICES_INFO[s]['T_bar']
            # Normalize violation by slice target throughput T_bar.
            if t_bar > 0:
                ratios = np.array(history_dict[s]) / float(t_bar)
            else:
                ratios = np.zeros(len(history_dict[s]))
            all_ratios.extend(ratios)
        return np.array(all_ratios)


    data_dynamic = get_all_violation_ratios(history_violation)
    data_static = get_all_violation_ratios(history_vio_baseline)
    data_periodical = get_all_violation_ratios(history_vio_periodical)

    # Align all CDF curves to the same right endpoint.
    global_max_x = max(np.max(data_dynamic), np.max(data_static), np.max(data_periodical))

    plt.figure(figsize=(16, 12))


    def plot_cdf(data, label, color, style='-'):
        sorted_data = np.sort(data)
        yvals = np.arange(len(sorted_data)) / float(len(sorted_data) - 1)

        # Extend each curve to the global max x for visual alignment.
        if len(sorted_data) > 0 and sorted_data[-1] < global_max_x:
            sorted_data = np.append(sorted_data, global_max_x)
            yvals = np.append(yvals, 1.0)

        plt.plot(sorted_data, yvals, label=label, color=color, linestyle=style, linewidth=5)


    plot_cdf(data_dynamic, 'Multi', '#2ca02c', '-')
    plot_cdf(data_static, 'Static', '#d62728', '--')
    plot_cdf(data_periodical, 'Single', '#ff7f0e', '-.')

    plt.ylabel("CDF (Probability)", fontsize=FONT_AXIS_LABEL)
    plt.xlabel("Violation Ratio", fontsize=FONT_AXIS_LABEL)
    plt.tick_params(labelsize=FONT_Y_TICKS)
    plt.legend(fontsize=FONT_LEGEND)
    plt.grid(True, linestyle='--', alpha=0.4)
    # Keep only left bound; right bound is data-driven.
    plt.xlim(left=-0.05)

    plt.tight_layout()
    plt.savefig('cdf_violation.pdf', dpi=300, bbox_inches='tight')
    plt.show()
