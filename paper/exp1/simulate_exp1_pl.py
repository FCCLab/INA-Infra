#!/usr/bin/env python3
"""Experiment 1: Theoretical PL Numerical Simulation Runner.

Executes the Layer 1 PL optimization problem across a continuous spectrum of
SLA target constraints and evaluates OPEX vs SLA Satisfaction curves for:
  - Full Algorithm (PL Optimizer)
  - Fixed Edge Scheme
  - Fixed Regional Scheme
  - Fixed Central Scheme

Outputs:
  - paper/exp1/data/exp1_sim_results.csv
"""

import csv
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 3-Tier Network Parameters (Edge=0, Regional=1, Central=2)
LOCATIONS = [0, 1, 2]
LOCATION_NAMES = {0: "Edge", 1: "Regional", 2: "Central"}

# Compute capacities (cores) and unit costs
C_CAPACITY = {0: 40, 1: 80, 2: 250}
P_COMPUTE = {0: 3.0, 1: 1.8, 2: 1.0}
P_GPU = {0: 5.0, 1: 3.0, 2: 2.0}
P_TRANSPORT = {0: 0.0, 1: 0.05, 2: 0.10}

# Base transport delays (ms)
D_TRANSPORT = {0: 1.2, 1: 10.5, 2: 31.0}
D_RADIO = 2.4

# Slice catalog (8 representative slices for scaling curve)
SIM_SLICES = {
    1: {"name": "URLLC-1 (Robotics)", "rate": 15.0, "sla_delay": 8.0, "cpu": 4.0, "gpu": 1.0},
    2: {"name": "URLLC-2 (Teleop)", "rate": 20.0, "sla_delay": 10.0, "cpu": 4.0, "gpu": 1.0},
    3: {"name": "Video-1 (CCTV-4K)", "rate": 50.0, "sla_delay": 20.0, "cpu": 3.0, "gpu": 0.5},
    4: {"name": "Video-2 (YOLOv8)", "rate": 40.0, "sla_delay": 25.0, "cpu": 3.0, "gpu": 0.5},
    5: {"name": "eMBB-1 (OTT Video)", "rate": 35.0, "sla_delay": 50.0, "cpu": 2.0, "gpu": 0.0},
    6: {"name": "eMBB-2 (Cloud Game)", "rate": 45.0, "sla_delay": 35.0, "cpu": 3.0, "gpu": 0.5},
    7: {"name": "IoT-1 (Smart Grid)", "rate": 5.0, "sla_delay": 80.0, "cpu": 1.0, "gpu": 0.0},
    8: {"name": "IoT-2 (Sensors)", "rate": 2.0, "sla_delay": 120.0, "cpu": 0.5, "gpu": 0.0},
}

def solve_pl_optimal(slices: dict, sla_strictness: float) -> tuple:
    """Finds optimal tier placement for each slice to minimize OPEX while satisfying delay SLAs."""
    placement = {}
    total_opex = 0.0
    delay_violations = 0
    total_delay = 0.0
    
    for s_id, s_info in slices.items():
        allowed_delay = s_info["sla_delay"] / sla_strictness
        best_loc = None
        best_cost = float("inf")
        
        # Check locations from cheapest (Central=2) to most expensive (Edge=0)
        for loc in [2, 1, 0]:
            e2e_delay = D_RADIO + D_TRANSPORT[loc] + (s_info["cpu"] * 0.4)
            if e2e_delay <= allowed_delay:
                cost = (s_info["cpu"] * P_COMPUTE[loc] + 
                        s_info["gpu"] * P_GPU[loc] + 
                        s_info["rate"] * P_TRANSPORT[loc])
                if cost < best_cost:
                    best_cost = cost
                    best_loc = loc
                    
        # Fallback to Edge if strict delay requires it
        if best_loc is None:
            best_loc = 0
            best_cost = (s_info["cpu"] * P_COMPUTE[0] + 
                         s_info["gpu"] * P_GPU[0] + 
                         s_info["rate"] * P_TRANSPORT[0])
            
        e2e_delay = D_RADIO + D_TRANSPORT[best_loc] + (s_info["cpu"] * 0.4)
        if e2e_delay > s_info["sla_delay"]:
            delay_violations += 1
            
        placement[s_id] = best_loc
        total_opex += best_cost
        total_delay += e2e_delay
        
    avg_delay = total_delay / len(slices)
    sla_sat_pct = ((len(slices) - delay_violations) / len(slices)) * 100.0
    return placement, total_opex, sla_sat_pct, avg_delay

def solve_fixed_tier(slices: dict, fixed_loc: int) -> tuple:
    """Evaluates cost and delay when all slices are statically assigned to fixed_loc."""
    total_opex = 0.0
    delay_violations = 0
    total_delay = 0.0
    
    for s_id, s_info in slices.items():
        cost = (s_info["cpu"] * P_COMPUTE[fixed_loc] + 
                s_info["gpu"] * P_GPU[fixed_loc] + 
                s_info["rate"] * P_TRANSPORT[fixed_loc])
        e2e_delay = D_RADIO + D_TRANSPORT[fixed_loc] + (s_info["cpu"] * 0.4)
        
        if e2e_delay > s_info["sla_delay"]:
            delay_violations += 1
            
        total_opex += cost
        total_delay += e2e_delay
        
    avg_delay = total_delay / len(slices)
    sla_sat_pct = ((len(slices) - delay_violations) / len(slices)) * 100.0
    return total_opex, sla_sat_pct, avg_delay

def main():
    print("=== Running Experiment 1: Theoretical PL Simulation Sweep ===")
    
    sla_targets = [95.0, 96.0, 97.0, 98.0, 99.0, 99.5, 99.9, 99.99]
    results = []
    
    for target in sla_targets:
        # Scale strictness factor
        strictness = 1.0 + (target - 95.0) * 0.02
        
        # 1. Proposed PL
        _, pl_opex, pl_sat, pl_delay = solve_pl_optimal(SIM_SLICES, strictness)
        results.append({
            "scheme": "Full Algorithm (Proposed PL)",
            "sla_target_pct": target,
            "sla_satisfaction_pct": round(pl_sat, 2),
            "total_opex": round(pl_opex, 2),
            "avg_e2e_delay_ms": round(pl_delay, 2)
        })
        
        # 2. Fixed Edge
        edge_opex, edge_sat, edge_delay = solve_fixed_tier(SIM_SLICES, fixed_loc=0)
        results.append({
            "scheme": "Fixed Edge",
            "sla_target_pct": target,
            "sla_satisfaction_pct": round(edge_sat, 2),
            "total_opex": round(edge_opex, 2),
            "avg_e2e_delay_ms": round(edge_delay, 2)
        })
        
        # 3. Fixed Regional
        reg_opex, reg_sat, reg_delay = solve_fixed_tier(SIM_SLICES, fixed_loc=1)
        results.append({
            "scheme": "Fixed Regional",
            "sla_target_pct": target,
            "sla_satisfaction_pct": round(reg_sat, 2),
            "total_opex": round(reg_opex, 2),
            "avg_e2e_delay_ms": round(reg_delay, 2)
        })
        
        # 4. Fixed Central
        cen_opex, cen_sat, cen_delay = solve_fixed_tier(SIM_SLICES, fixed_loc=2)
        results.append({
            "scheme": "Fixed Central",
            "sla_target_pct": target,
            "sla_satisfaction_pct": round(cen_sat, 2),
            "total_opex": round(cen_opex, 2),
            "avg_e2e_delay_ms": round(cen_delay, 2)
        })

    out_csv = DATA_DIR / "exp1_sim_results.csv"
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
        
    print(f"Saved numerical simulation results: {out_csv}")

if __name__ == "__main__":
    main()
