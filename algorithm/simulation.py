import gurobipy as gp
from gurobipy import GRB
import random
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
import matplotlib.patches as patches

# ==================================================================
# 1. Parameter Initialization
# ==================================================================
J_LOCATIONS = [0, 1, 2]
S_SLICES = range(1, 9)

# Infrastructure Capacity
C_N_CAPACITY = {0: 400, 1: 800, 2: 5000}
R_N_CAPACITY = {0: 5120, 1: 20480, 2: 102400}
APP_RATIO = 2.0
C_A_CAPACITY = {0: 400, 1: 700, 2: 5000}
R_A_CAPACITY = {k: v * 2.0 for k, v in R_N_CAPACITY.items()}
G_A_CAPACITY = {0: 150, 1: 300, 2: 1200}

# Power/Cost Coefficients
P_C = {0: 0.5, 1: 0.05, 2: 0.001};
P_R = {0: 0.1, 1: 0.01, 2: 0.002};
P_G = {0: 1.0, 1: 0.5, 2: 0.1}
P_PRB_DED = 0.5;
P_PRB_PRIO = 0.1

# Resource Conversion Coefficients
ALPHA_CU = 1.02;
ALPHA_UPF = 0.81
GAMMA_C = 0.5;
GAMMA_R = 0.008;
GAMMA_G = 1.0
MIN_R_CU = 10;
MIN_R_UPF = 10

# Latency Parameters
D_F1 = {0: 1, 1: 15, 2: 30}
D_N3 = {(0, 0): 1, (0, 1): 10, (0, 2): 20, (1, 0): 10, (1, 1): 1, (1, 2): 10, (2, 0): 20, (2, 1): 10, (2, 2): 1}
D_N6 = {
    (0, 0): 5, (0, 1): 15, (0, 2): 30,
    (1, 0): 15, (1, 1): 5, (1, 2): 15,
    (2, 0): 30, (2, 1): 15, (2, 2): 5
}

B_TOTAL = 273;
W_C = 1.0;
W_P = 1000.0
BETA_DEMAND = 0.1

NUM_PM_CYCLES = 10;
PS_STEPS_PER_PM = 10

ACTIVATION_SCHEDULE = {0: [1, 2, 3], 3: [4, 5, 6], 6: [7, 8]}

MCS_TABLE = {
    0: (2, 120), 1: (2, 157), 2: (2, 193), 3: (2, 251), 4: (2, 308),
    5: (2, 379), 6: (2, 449), 7: (2, 526), 8: (2, 602), 9: (2, 679),
    10: (4, 340), 11: (4, 378), 12: (4, 434), 13: (4, 490), 14: (4, 553),
    15: (4, 616), 16: (4, 658),
    17: (6, 438), 18: (6, 466), 19: (6, 517), 20: (6, 567), 21: (6, 616),
    22: (6, 666), 23: (6, 719), 24: (6, 772), 25: (6, 822), 26: (6, 873),
    27: (6, 910), 28: (6, 948)
}
SCS_KHZ = 30;
MIMO_LAYERS = 4;
OVERHEAD = 0.14;
SCALING_FACTOR = 1


def calculate_eta(mcs_index):
    if mcs_index not in MCS_TABLE: return 0
    Qm, R_1024 = MCS_TABLE[mcs_index]
    R = R_1024 / 1024.0
    res_per_sec = 12 * 28000
    eta = 1e-6 * MIMO_LAYERS * Qm * SCALING_FACTOR * R * res_per_sec * (1 - OVERHEAD)
    return eta


SLICES_INFO = {}
random.seed(2025)

# Generate slice requirements (SLA) based on slice type
for s in S_SLICES:
    if s in [1, 2]:
        t_min = 40;
        d_max = 100;
        hard_iso = 0;
        s_type = "mMTC"
    elif s == 3:
        t_min = 40;
        d_max = 30;
        hard_iso = 0;
        s_type = "mMTC(Reg)"
    elif s in [4, 5]:
        if s == 4:
            t_min = 60;
            d_max = 20;
            hard_iso = 1;
            s_type = "URLLC(Split)"
        else:
            t_min = 60;
            d_max = 10;
            hard_iso = 1;
            s_type = "URLLC(Edge)"
    elif s == 6:
        t_min = 100;
        d_max = 15;
        hard_iso = 0;
        s_type = "eMBB(Edge)"
    else:
        t_min = 100;
        d_max = 35;
        hard_iso = 0;
        s_type = "eMBB"

    init_mcs = random.randint(10, 20)
    eta_init = calculate_eta(init_mcs)
    SLICES_INFO[s] = {'T_bar': t_min, 'D_bar': d_max, 'H_s': hard_iso, 'eta_t0': eta_init, 'Type': s_type}


def run_layer_1_pl(active_slices):
    if not active_slices: return {}, {}
    m = gp.Model("PL_Layer1");
    m.setParam('OutputFlag', 0)

    # Decision Variables
    x = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    y = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    z = m.addVars(active_slices, J_LOCATIONS, vtype=GRB.BINARY)
    a_c_cu = m.addVars(active_slices, lb=0);
    a_r_cu = m.addVars(active_slices, lb=MIN_R_CU)
    a_c_upf = m.addVars(active_slices, lb=0);
    a_r_upf = m.addVars(active_slices, lb=MIN_R_UPF)
    a_c_app = m.addVars(active_slices, lb=0);
    a_r_app = m.addVars(active_slices, lb=0);
    a_g_app = m.addVars(active_slices, lb=0)
    b_ded = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    b_min = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)

    # Auxiliary/Slack Variables
    T_plan = m.addVars(active_slices, lb=0);
    D_plan = m.addVars(active_slices, lb=0)
    xi_D = m.addVars(active_slices, lb=0);
    xi_PRB = m.addVars(active_slices, lb=0);
    xi_Com = m.addVars(active_slices, lb=0)
    xy = m.addVars(active_slices, J_LOCATIONS, J_LOCATIONS, vtype=GRB.BINARY)
    yz = m.addVars(active_slices, J_LOCATIONS, J_LOCATIONS, vtype=GRB.BINARY)

    # Objective Function
    cost_infra = 0
    for s in active_slices:
        for j in J_LOCATIONS:
            cost_infra += (a_c_cu[s] * P_C[j] + a_r_cu[s] * P_R[j]) * x[s, j]
            cost_infra += (a_c_upf[s] * P_C[j] + a_r_upf[s] * P_R[j]) * y[s, j]
            cost_infra += (a_c_app[s] * P_C[j] + a_r_app[s] * P_R[j] + a_g_app[s] * P_G[j]) * z[s, j]
    cost_prb = gp.quicksum(P_PRB_DED * b_ded[s] + P_PRB_PRIO * (b_min[s] - b_ded[s]) for s in active_slices)
    cost_penalty = gp.quicksum(xi_D[s] + xi_PRB[s] + xi_Com[s] for s in active_slices)
    m.setObjective(W_C * (cost_infra + cost_prb) + W_P * cost_penalty, GRB.MINIMIZE)

    # Constraints: Topology and Mapping
    for s in active_slices:
        m.addConstr(gp.quicksum(x[s, j] for j in J_LOCATIONS) == 1)
        m.addConstr(gp.quicksum(y[s, j] for j in J_LOCATIONS) == 1)
        m.addConstr(gp.quicksum(z[s, j] for j in J_LOCATIONS) == 1)
        m.addConstr(T_plan[s] <= ALPHA_CU * a_c_cu[s])
        m.addConstr(T_plan[s] <= ALPHA_UPF * a_c_upf[s])
        m.addConstr(T_plan[s] <= GAMMA_C * a_c_app[s])
        m.addConstr(T_plan[s] <= GAMMA_R * a_r_app[s])
        m.addConstr(T_plan[s] <= GAMMA_G * a_g_app[s])
        m.addConstr(b_ded[s] <= b_min[s])
        m.addConstr(b_min[s] <= B_TOTAL)
        m.addConstr(b_ded[s] >= b_min[s] * SLICES_INFO[s]['H_s'])
        m.addConstr(xi_PRB[s] >= SLICES_INFO[s]['T_bar'] - SLICES_INFO[s]['eta_t0'] * b_min[s])
        m.addConstr(xi_Com[s] >= SLICES_INFO[s]['T_bar'] - T_plan[s])

        # Link Linearization
        for j in J_LOCATIONS:
            for k in J_LOCATIONS:
                m.addConstr(xy[s, j, k] >= x[s, j] + y[s, k] - 1)
                m.addConstr(xy[s, j, k] <= x[s, j])
                m.addConstr(xy[s, j, k] <= y[s, k])
                for l in J_LOCATIONS:
                    m.addConstr(yz[s, k, l] >= y[s, k] + z[s, l] - 1)
                    m.addConstr(yz[s, k, l] <= y[s, k])
                    m.addConstr(yz[s, k, l] <= z[s, l])

        # Delay Calculation
        delay_expr = (gp.quicksum(D_F1[j] * x[s, j] for j in J_LOCATIONS) +
                      gp.quicksum(D_N3[j, k] * xy[s, j, k] for j in J_LOCATIONS for k in J_LOCATIONS) +
                      gp.quicksum(D_N6[k, l] * yz[s, k, l] for k in J_LOCATIONS for l in J_LOCATIONS))
        m.addConstr(D_plan[s] == delay_expr)
        m.addConstr(xi_D[s] >= D_plan[s] - SLICES_INFO[s]['D_bar'])

    # Constraints: Infrastructure Capacity
    for j in J_LOCATIONS:
        m.addConstr(gp.quicksum(a_c_cu[s] * x[s, j] + a_c_upf[s] * y[s, j] for s in active_slices) <= C_N_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_cu[s] * x[s, j] + a_r_upf[s] * y[s, j] for s in active_slices) <= R_N_CAPACITY[j])
        m.addConstr(gp.quicksum(a_c_app[s] * z[s, j] for s in active_slices) <= C_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_app[s] * z[s, j] for s in active_slices) <= R_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_g_app[s] * z[s, j] for s in active_slices) <= G_A_CAPACITY[j])

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


def run_layer_2_pm(active_slices, deployment_map, t_demand):
    m = gp.Model("PM_Layer2");
    m.setParam('OutputFlag', 0)

    # Decision Variables (Compute Resources)
    a_c_cu = m.addVars(active_slices, lb=0);
    a_r_cu = m.addVars(active_slices, lb=MIN_R_CU)
    a_c_upf = m.addVars(active_slices, lb=0);
    a_r_upf = m.addVars(active_slices, lb=MIN_R_UPF)
    a_c_app = m.addVars(active_slices, lb=0);
    a_r_app = m.addVars(active_slices, lb=0);
    a_g_app = m.addVars(active_slices, lb=0)
    T_curr = m.addVars(active_slices, lb=0);
    xi_SLA = m.addVars(active_slices, lb=0);
    xi_Dem = m.addVars(active_slices, lb=0)

    # Objective
    cost_expr = 0
    for s in active_slices:
        loc_cu, loc_upf, loc_app = deployment_map[s]
        cost_expr += (a_c_cu[s] * P_C[loc_cu] + a_r_cu[s] * P_R[loc_cu] +
                      a_c_upf[s] * P_C[loc_upf] + a_r_upf[s] * P_R[loc_upf] +
                      a_c_app[s] * P_C[loc_app] + a_r_app[s] * P_R[loc_app] + a_g_app[s] * P_G[loc_app])

    m.setObjective(W_C * cost_expr + W_P * gp.quicksum(xi_SLA[s] + BETA_DEMAND * xi_Dem[s] for s in active_slices),
                   GRB.MINIMIZE)

    # Constraints: Capacity and Throughput
    for j in J_LOCATIONS:
        s_at_j_cu = [s for s in active_slices if deployment_map[s][0] == j]
        s_at_j_upf = [s for s in active_slices if deployment_map[s][1] == j]
        s_at_j_app = [s for s in active_slices if deployment_map[s][2] == j]

        m.addConstr(
            gp.quicksum(a_c_cu[s] for s in s_at_j_cu) + gp.quicksum(a_c_upf[s] for s in s_at_j_upf) <= C_N_CAPACITY[j])
        m.addConstr(
            gp.quicksum(a_r_cu[s] for s in s_at_j_cu) + gp.quicksum(a_r_upf[s] for s in s_at_j_upf) <= R_N_CAPACITY[j])

        m.addConstr(gp.quicksum(a_c_app[s] for s in s_at_j_app) <= C_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_r_app[s] for s in s_at_j_app) <= R_A_CAPACITY[j])
        m.addConstr(gp.quicksum(a_g_app[s] for s in s_at_j_app) <= G_A_CAPACITY[j])

    for s in active_slices:
        m.addConstr(T_curr[s] <= ALPHA_CU * a_c_cu[s])
        m.addConstr(T_curr[s] <= ALPHA_UPF * a_c_upf[s])
        m.addConstr(T_curr[s] <= GAMMA_C * a_c_app[s])
        m.addConstr(T_curr[s] <= GAMMA_R * a_r_app[s])
        m.addConstr(T_curr[s] <= GAMMA_G * a_g_app[s])
        m.addConstr(xi_SLA[s] >= SLICES_INFO[s]['T_bar'] - T_curr[s])
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


def run_layer_3_ps(eta_realtime, active_slices):
    m = gp.Model("PS_Layer3");
    m.setParam('OutputFlag', 0)

    # Decision Variables (Radio Resources)
    b_ded = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    b_min = m.addVars(active_slices, vtype=GRB.INTEGER, lb=0)
    xi_prb = m.addVars(active_slices, lb=0)

    # Objective
    cost = gp.quicksum(P_PRB_DED * b_ded[s] + P_PRB_PRIO * (b_min[s] - b_ded[s]) for s in active_slices)
    m.setObjective(W_C * cost + W_P * gp.quicksum(xi_prb[s] for s in active_slices), GRB.MINIMIZE)

    # Constraints
    m.addConstr(gp.quicksum(b_min[s] for s in active_slices) <= B_TOTAL)
    for s in active_slices:
        m.addConstr(b_ded[s] <= b_min[s])
        m.addConstr(b_ded[s] >= b_min[s] * SLICES_INFO[s]['H_s'])
        m.addConstr(xi_prb[s] >= SLICES_INFO[s]['T_bar'] - eta_realtime[s] * b_min[s])
    m.optimize()
    res = {}
    if m.status == GRB.OPTIMAL:
        for s in active_slices: res[s] = b_min[s].x
    return res


if __name__ == "__main__":
    current_deploy_map = {}
    current_resources = {}  # For Main Algo
    baseline_resources = {}  # For Static Baseline

    active_slices = []

    # Data Recording
    history_t_actual = {s: [] for s in S_SLICES}
    history_violation = {s: [] for s in S_SLICES}
    history_t_baseline = {s: [] for s in S_SLICES}
    history_vio_baseline = {s: [] for s in S_SLICES}

    history_analysis = {s: {'SLA': [], 'ComputeCap': [], 'RadioGuaranteed': []} for s in S_SLICES}
    history_usage = {j: {'APP_CPU': [], 'APP_RAM': [], 'APP_GPU': [], 'NF_CPU': [], 'NF_RAM': []} for j in J_LOCATIONS}

    print("Starting Simulation...")

    for pm_cycle in range(NUM_PM_CYCLES):
        new_slices = ACTIVATION_SCHEDULE.get(pm_cycle, [])
        if new_slices:
            active_slices.extend(new_slices)
            active_slices.sort()

            # --- Trigger PL Layer (Long-term) ---
            current_deploy_map, new_baselines = run_layer_1_pl(active_slices)

            # Update resource baselines
            current_resources.update(new_baselines)
            baseline_resources.update(new_baselines)

        radio_demands_buffer = {s: [] for s in active_slices}
        current_compute_caps = {}
        baseline_compute_caps = {}

        for s in active_slices:
            # Calculate compute capacity limits
            res = current_resources[s]
            cap = min(ALPHA_CU * res['a_c_cu'], ALPHA_UPF * res['a_c_upf'],
                      GAMMA_C * res['a_c_app'], GAMMA_R * res['a_r_app'], GAMMA_G * res['a_g_app'])
            current_compute_caps[s] = cap

            # Baseline Compute Cap (Static from PL)
            res_b = baseline_resources[s]
            cap_b = min(ALPHA_CU * res_b['a_c_cu'], ALPHA_UPF * res_b['a_c_upf'],
                        GAMMA_C * res_b['a_c_app'], GAMMA_R * res_b['a_r_app'], GAMMA_G * res_b['a_g_app'])
            baseline_compute_caps[s] = cap_b

        # --- Resource Logging ---
        for j in J_LOCATIONS:
            app_cpu = 0;
            app_ram = 0;
            app_gpu = 0;
            nf_cpu = 0;
            nf_ram = 0
            for s in active_slices:
                res = current_resources[s]
                loc_cu, loc_upf, loc_app = current_deploy_map[s]
                if loc_app == j:
                    app_cpu += res['a_c_app'];
                    app_ram += res['a_r_app'];
                    app_gpu += res['a_g_app']
                if loc_cu == j:
                    nf_cpu += res['a_c_cu'];
                    nf_ram += res['a_r_cu']
                if loc_upf == j:
                    nf_cpu += res['a_c_upf'];
                    nf_ram += res['a_r_upf']
            app_cpu_u = (app_cpu / C_A_CAPACITY[j]) * 100 if C_A_CAPACITY[j] > 0 else 0
            app_ram_u = (app_ram / R_A_CAPACITY[j]) * 100 if R_A_CAPACITY[j] > 0 else 0
            app_gpu_u = (app_gpu / G_A_CAPACITY[j]) * 100 if G_A_CAPACITY[j] > 0 else 0
            nf_cpu_u = (nf_cpu / C_N_CAPACITY[j]) * 100 if C_N_CAPACITY[j] > 0 else 0
            nf_ram_u = (nf_ram / R_N_CAPACITY[j]) * 100 if R_N_CAPACITY[j] > 0 else 0

            for _ in range(PS_STEPS_PER_PM):
                history_usage[j]['APP_CPU'].append(app_cpu_u)
                history_usage[j]['APP_RAM'].append(app_ram_u)
                history_usage[j]['APP_GPU'].append(app_gpu_u)
                history_usage[j]['NF_CPU'].append(nf_cpu_u)
                history_usage[j]['NF_RAM'].append(nf_ram_u)

        # --- PS Cycle (Loop) ---
        for ps_step in range(PS_STEPS_PER_PM):
            eta_now = {}
            for s in S_SLICES:
                if s in active_slices:
                    mcs = random.randint(5, 28)
                    eta_now[s] = calculate_eta(mcs)
                else:
                    eta_now[s] = 0

            # 1. Dynamic Algo: PS Layer Optimization
            b_min_map = run_layer_3_ps(eta_now, active_slices)
            total_reserved = sum(b_min_map.values())
            slack = max(0, B_TOTAL - total_reserved)
            extra = slack / len(active_slices) if active_slices else 0

            # 2. Static Baseline: Fixed resources (No optimization)
            total_reserved_base = sum(baseline_resources[s]['b_min'] for s in active_slices)
            slack_base = max(0, B_TOTAL - total_reserved_base)
            extra_base = slack_base / len(active_slices) if active_slices else 0

            for s in S_SLICES:
                if s in active_slices:
                    # A. Dynamic Performance Calc
                    radio_pot = (b_min_map[s] + extra) * eta_now[s]
                    actual_t = min(radio_pot, current_compute_caps[s])
                    violation = max(0, SLICES_INFO[s]['T_bar'] - actual_t)

                    radio_demands_buffer[s].append(radio_pot)
                    history_analysis[s]['SLA'].append(SLICES_INFO[s]['T_bar'])
                    history_analysis[s]['ComputeCap'].append(current_compute_caps[s])
                    history_analysis[s]['RadioGuaranteed'].append(b_min_map[s] * eta_now[s])
                    history_t_actual[s].append(actual_t)
                    history_violation[s].append(violation)

                    # B. Static Performance Calc
                    b_min_static = baseline_resources[s]['b_min']
                    radio_pot_base = (b_min_static + extra_base) * eta_now[s]
                    actual_t_base = min(radio_pot_base, baseline_compute_caps[s])
                    violation_base = max(0, SLICES_INFO[s]['T_bar'] - actual_t_base)

                    history_t_baseline[s].append(actual_t_base)
                    history_vio_baseline[s].append(violation_base)
                else:
                    history_analysis[s]['SLA'].append(0)
                    history_analysis[s]['ComputeCap'].append(0)
                    history_analysis[s]['RadioGuaranteed'].append(0)
                    history_t_actual[s].append(0)
                    history_violation[s].append(0)
                    history_t_baseline[s].append(0)
                    history_vio_baseline[s].append(0)

        # --- Trigger PM Layer (Medium-term) ---
        if active_slices:
            next_demand = {s: np.mean(radio_demands_buffer[s]) for s in active_slices}
            current_resources = run_layer_2_pm(active_slices, current_deploy_map, next_demand)
            # Static baseline does not run PM

        print(f"PM Cycle {pm_cycle} finished.")

    # --- Visualization 1: Main Algo Throughput ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    # Color mapping for slices 1-8
    colors = plt.cm.tab10(np.linspace(0, 1, len(S_SLICES) + 1))


    def get_color(s):
        return colors[s - 1]


    time_axis = range(NUM_PM_CYCLES * PS_STEPS_PER_PM)
    for s in S_SLICES:
        ax1.plot(time_axis, history_t_actual[s], linewidth=2, label=f"S{s}", color=get_color(s), alpha=0.8)
    for ax in [ax1, ax2]:
        ax.axvline(x=30, color='black', linestyle='--', alpha=0.5, label="Batch 2")
        ax.axvline(x=60, color='black', linestyle='--', alpha=0.5, label="Batch 3")
        ax.grid(True, linestyle='--', alpha=0.4)

    sla_map = {}
    for s in S_SLICES:
        sla_val = SLICES_INFO[s]['T_bar']
        if sla_val not in sla_map: sla_map[sla_val] = []
        sla_map[sla_val].append(f"S{s}")
    sla_colors = ['purple', 'orange', 'green', 'blue', 'red', 'brown']
    for i, (sla_val, s_list) in enumerate(sla_map.items()):
        s_label = ",".join(s_list)
        color = sla_colors[i % len(sla_colors)]
        ax1.axhline(y=sla_val, color=color, linestyle=':', label=f"{s_label} SLA ({sla_val})")

    ax1.set_title("Real-time Throughput (Dynamic Algorithm)", fontsize=16);
    ax1.set_ylabel("Throughput (Mbps)", fontsize=14);

    # Text annotations for batches
    y_text_pos = 20
    ax1.text(15, y_text_pos, "{S1, S2, S3}", color='brown', fontsize=10, ha='center', fontweight='bold')
    ax1.text(45, y_text_pos, "{S1~S6}", color='brown', fontsize=10, ha='center', fontweight='bold')
    ax1.text(80, y_text_pos, "{S1~S8}", color='brown', fontsize=9, ha='center', fontweight='bold')

    ax1.legend(bbox_to_anchor=(1.01, 1), loc='upper left')

    for s in S_SLICES:
        ax2.plot(time_axis, history_violation[s], linewidth=2, label=f"S{s}", color=get_color(s), alpha=0.8)
    ax2.set_title("SLA Violation Magnitude", fontsize=16);
    ax2.set_ylabel("Violation (Mbps)", fontsize=14);
    ax2.set_xlabel("Time Steps", fontsize=14)
    plt.tight_layout();
    plt.show()

    # ==================================================================
    # Visualization 2: Optimized Deployment Topology
    # ==================================================================
    fig, ax = plt.subplots(figsize=(12, 7))

    # 1. Setup Background Zones (Edge, Regional, Central)
    zone_centers = {0: 0, 1: 1, 2: 2}
    zone_width = 0.6
    colors_zones = ['#e6f2ff', '#fff0e6', '#e6ffe6']  # Light Blue, Orange, Green
    labels_zones = ["Edge DC", "Regional DC", "Central DC"]

    for j in J_LOCATIONS:
        center = zone_centers[j]
        rect = patches.Rectangle((center - zone_width / 2, 0), zone_width, 10,
                                 linewidth=0, facecolor=colors_zones[j], alpha=0.5, zorder=0)
        ax.add_patch(rect)
        ax.text(center, 9.5, labels_zones[j], ha='center', va='center', fontsize=12, fontweight='bold', color='gray')

    # 2. Define Plot Styles
    markers = {'CU': 'o', 'UPF': 's', 'APP': '^'}
    # Offsets for visualization
    offsets = {'APP': 0.15, 'UPF': 0, 'CU': -0.15}

    # 3. Plot Nodes and Links
    for s in S_SLICES:
        if s not in current_deploy_map: continue

        loc_cu, loc_upf, loc_app = current_deploy_map[s]
        y_pos = s

        x_app = zone_centers[loc_app] + offsets['APP']
        x_upf = zone_centers[loc_upf] + offsets['UPF']
        x_cu = zone_centers[loc_cu] + offsets['CU']

        slice_color = get_color(s)

        # 3.1 Draw connections
        ax.plot([x_app, x_upf], [y_pos, y_pos], color=slice_color, linestyle='-', linewidth=2, alpha=0.6, zorder=1)
        ax.plot([x_upf, x_cu], [y_pos, y_pos], color=slice_color, linestyle='-', linewidth=2, alpha=0.6, zorder=1)

        # 3.2 Draw nodes
        ax.scatter(x_app, y_pos, marker=markers['APP'], s=180, color=slice_color, edgecolors='black', zorder=2)
        ax.scatter(x_upf, y_pos, marker=markers['UPF'], s=180, color=slice_color, edgecolors='black', zorder=2)
        ax.scatter(x_cu, y_pos, marker=markers['CU'], s=180, color=slice_color, edgecolors='black', zorder=2)

    # 4. Chart Decoration
    ax.set_yticks(S_SLICES)
    ax.set_yticklabels([f"Slice {s}" for s in S_SLICES], fontsize=11)
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels([])
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0.5, 10)
    ax.set_ylabel("Network Slice", fontsize=14)
    ax.set_title("Optimal Deployment Topology ", fontsize=16)

    ax.grid(True, axis='y', linestyle='--', alpha=0.3)

    # Custom Legend
    legend_elements = [
        Line2D([0], [0], marker='o', color='w', label='CU-UP', markerfacecolor='gray', markersize=10,
               markeredgecolor='k'),
        Line2D([0], [0], marker='s', color='w', label='UPF', markerfacecolor='gray', markersize=10,
               markeredgecolor='k'),
        Line2D([0], [0], marker='^', color='w', label='APP', markerfacecolor='gray', markersize=10,
               markeredgecolor='k'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', ncol=4)

    plt.tight_layout()
    plt.show()

    # --- Visualization 3: Bottleneck ---
    fig_bn, axes_bn = plt.subplots(4, 2, figsize=(18, 14), sharex=True)
    axes_bn = axes_bn.flatten()
    fig_bn.suptitle("Bottleneck Analysis", fontsize=24)  # Increased Title

    # Iterate through all slices
    for i, s in enumerate(S_SLICES):
        ax = axes_bn[i]
        ax.plot(time_axis, history_analysis[s]['SLA'], color='black', linestyle='--', linewidth=1.5, label='SLA')
        ax.step(time_axis, history_analysis[s]['ComputeCap'], where='post', color='green', linewidth=2,
                label='Compute Cap')
        ax.plot(time_axis, history_analysis[s]['RadioGuaranteed'], color='blue', linewidth=1.5,
                label='Radio Guaranteed')
        ax.plot(time_axis, history_t_actual[s], color='red', linewidth=2, label='Actual Throughput')

        ax.set_title(f"S{s}", fontsize=18)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.axvline(x=30, color='gray', linestyle=':', alpha=0.5)
        ax.axvline(x=60, color='gray', linestyle=':', alpha=0.5)
        ax.tick_params(axis='both', which='major', labelsize=14)

        # Set Y-axis labels
        ax.set_ylabel("Throughput (Mbps)", fontsize=16)

        # Set X-axis labels
        if i >= 6:
            ax.set_xlabel("Time Steps", fontsize=16)

        if i == 0: ax.legend(loc='upper right', fontsize=14)

    fig_bn.tight_layout(rect=[0, 0.03, 1, 0.97])
    plt.show()
    # --- Visualization 4: Resource Usage ---
    fig_res, axes_res = plt.subplots(3, 2, figsize=(14, 12), sharex=True)
    loc_names = {0: "Edge", 1: "Regional", 2: "Central"}
    for j in J_LOCATIONS:
        ax_app = axes_res[j, 0]
        ax_app.plot(time_axis, history_usage[j]['APP_CPU'], color='blue', linewidth=2, label='APP CPU')
        ax_app.plot(time_axis, history_usage[j]['APP_RAM'], color='orange', linewidth=2, label='APP RAM')
        ax_app.plot(time_axis, history_usage[j]['APP_GPU'], color='green', linewidth=2, label='APP GPU')
        ax_app.set_title(f"{loc_names[j]} DC - APP Resources", fontsize=12);
        ax_app.set_ylim(0, 110);
        ax_app.grid(True, linestyle='--', alpha=0.3)
        if j == 0: ax_app.legend(loc='upper right', fontsize=9)
        ax_nf = axes_res[j, 1]
        ax_nf.plot(time_axis, history_usage[j]['NF_CPU'], color='red', linewidth=2, label='NF CPU')
        ax_nf.plot(time_axis, history_usage[j]['NF_RAM'], color='purple', linewidth=2, label='NF RAM')
        ax_nf.set_title(f"{loc_names[j]} DC - NF Resources", fontsize=12);
        ax_nf.set_ylim(0, 110);
        ax_nf.grid(True, linestyle='--', alpha=0.3)
        if j == 0: ax_nf.legend(loc='upper right', fontsize=9)
    axes_res[2, 0].set_xlabel("Time Steps");
    axes_res[2, 1].set_xlabel("Time Steps")
    fig_res.suptitle("Resource Utilization", fontsize=16);
    fig_res.tight_layout(rect=[0, 0.03, 1, 0.97]);
    plt.show()

    # ==================================================================
    # Visualization 5: Comparison (Dynamic vs Static) - Refined
    # ==================================================================
    fig_comp, (ax_c1, ax_c2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    comp_limit = 60
    time_comp = list(range(comp_limit))
    # Plot slices S1-S6
    slices_to_plot = [1, 2, 3, 4, 5, 6]

    for s in slices_to_plot:
        data_proposed = history_t_actual[s][:comp_limit]
        data_baseline = history_t_baseline[s][:comp_limit]
        ax_c1.plot(time_comp, data_proposed, linewidth=2, linestyle='-', color=get_color(s))
        ax_c1.plot(time_comp, data_baseline, linewidth=2, linestyle='--', color=get_color(s), alpha=0.6)

    ax_c1.axvline(x=30, color='black', linestyle='-.', alpha=0.3, label="Batch 2 Trigger")
    ax_c1.set_title("Throughput Comparison: Dynamic (Solid) vs Static (Dashed)", fontsize=22)
    ax_c1.set_ylabel("Throughput (Mbps)", fontsize=18)
    ax_c1.grid(True, linestyle='--', alpha=0.3)
    ax_c1.tick_params(axis='both', which='major', labelsize=14)

    # Custom Legend
    legend_elements = []
    legend_elements.append(Line2D([0], [0], color='black', lw=2, linestyle='-', label='Dynamic (PL+PM+PS)'))
    legend_elements.append(Line2D([0], [0], color='black', lw=2, linestyle='--', label='Static (PL Only)'))
    for s in slices_to_plot:
        legend_elements.append(Line2D([0], [0], color=get_color(s), lw=2, label=f'Slice {s}'))
    ax_c1.legend(handles=legend_elements, bbox_to_anchor=(1.01, 1), loc='upper left', fontsize=20)

    # Subplot 2: SLA Violation
    for s in slices_to_plot:
        vio_proposed = history_violation[s][:comp_limit]
        vio_baseline = history_vio_baseline[s][:comp_limit]
        ax_c2.plot(time_comp, vio_proposed, linewidth=2, linestyle='-', color=get_color(s))
        ax_c2.plot(time_comp, vio_baseline, linewidth=2, linestyle='--', color=get_color(s), alpha=0.6)

    ax_c2.axvline(x=30, color='black', linestyle='-.', alpha=0.3)
    ax_c2.set_title("SLA Violation Comparison: Dynamic (Solid) vs Static (Dashed)", fontsize=22)
    ax_c2.set_ylabel("Violation (Mbps)", fontsize=18)
    ax_c2.set_xlabel("Time Steps (First 60)", fontsize=18)
    ax_c2.grid(True, linestyle='--', alpha=0.3)
    ax_c2.tick_params(axis='both', which='major', labelsize=14)

    plt.tight_layout()
    plt.show()