#!/usr/bin/env python3
"""Master Test Plan & Orchestration Runner for Experiment 1 Scheme A (exp1-a).

Scheme A: Full Algorithm (Proposed PL)
  - Placement: Slice 1 (CCTV @ Regional), Slice 2 (Physical-AI @ Edge), Slice 3 (OTT @ Central), Slice 4 (IoT @ Central)

Execution Workflow:
  1. Check System Bringup (5GC, RAN, UPFs, Apps in exp1-a). If not healthy -> deploy via `exp1_a_deploy.py`
  2. Check UE Bringup & 5G PDU Sessions. If not healthy -> deploy via `exp1_a_deploy_ue.py`
  3. Start Testing at [Timestamp] -> Run multi-slice traffic workload originating from UEs
  4. Stop Testing at [Timestamp] -> Record exact test window into `timestamps_exp1_a.csv`
  5. Download & Extract Results -> Aggregate physical samples to `exp1_a_latency_summary.csv` & `exp1_a_metrics.json`
  6. Plot Publication Figures -> Generate `fig1a_scheme_a_*.png` in `plots/` (plain text formatting)

Usage:
  python3 paper/exp1/exp1_a.py [--duration 15] [--force-redeploy]
"""

import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import exp1_a_check_system
import exp1_a_check_ue
import exp1_a_deploy
import exp1_a_deploy_ue
import exp1_a_start_testing
import exp1_a_download_result
import exp1_a_plot_results

def run_pipeline(duration_seconds: int = 15, force_redeploy: bool = False, skip_plots: bool = False):
    """Executes the full Scheme A testbed validation pipeline."""
    pipeline_t0 = time.time()
    
    print("################################################################")
    print(" EXPERIMENT 1 - SCHEME A (PROPOSED PL) MASTER TEST RUNNER")
    print(f" Execution Started: {datetime.now(timezone.utc).isoformat()}")
    print("################################################################\n")
    
    # -------------------------------------------------------------
    # Step 1: System Bringup Check & Auto-Deploy
    # -------------------------------------------------------------
    print("[STEP 1/6] Validating System Infrastructure Bringup (Namespace: exp1-a)...")
    system_healthy = False if force_redeploy else exp1_a_check_system.check_system_health(verbose=True)
    
    if not system_healthy:
        print("\n>>> System is not fully ready. Triggering Scheme A GitOps deployment...")
        exp1_a_deploy.main()
        
        print("\nWaiting for system pods to reach Ready state...")
        max_wait = 90
        t_wait = time.time()
        while time.time() - t_wait < max_wait:
            time.sleep(5)
            if exp1_a_check_system.check_system_health(verbose=False):
                system_healthy = True
                print("\n[OK] System infrastructure is now fully healthy and ready.")
                break
                
        if not system_healthy:
            print("\nWarning: System check still reports unready components after deploy timeout. Proceeding with caution.")
    else:
        print("[OK] System infrastructure verified healthy.")
        
    # -------------------------------------------------------------
    # Step 2: UE & 5G PDU Session Bringup Check
    # -------------------------------------------------------------
    print("\n[STEP 2/6] Validating 5G UEs & PDU Sessions (Namespace: exp1-a on Edge)...")
    ue_ready = exp1_a_check_ue.check_ue_health(verbose=True)
    
    if not ue_ready:
        print("\n>>> UEs or 5G PDU sessions not ready. Triggering UE bringup...")
        exp1_a_deploy_ue.bringup_ues()
        
        print("\nWaiting for 5G PDU sessions to establish...")
        max_wait = 45
        t_wait = time.time()
        while time.time() - t_wait < max_wait:
            time.sleep(3)
            if exp1_a_check_ue.check_ue_health(verbose=False):
                ue_ready = True
                print("\n[OK] All 4 UEs have active 5G PDU sessions.")
                break
                
        if not ue_ready:
            print("\nWarning: Some UEs could not verify PDU session reachability. Proceeding with available UEs.")
    else:
        print("[OK] 5G UEs and PDU sessions verified active.")
        
    # -------------------------------------------------------------
    # Step 3 & 4: Start Testing, Run Traffic, Stop & Record Timestamps
    # -------------------------------------------------------------
    print(f"\n[STEP 3-4/6] Running Real Multi-Slice 5G Traffic Test ({duration_seconds}s Window)...")
    exp1_a_start_testing.start_testing(duration_seconds=duration_seconds)
    
    # -------------------------------------------------------------
    # Step 5: Download & Aggregate Empirical Test Results
    # -------------------------------------------------------------
    print("\n[STEP 5/6] Extracting & Aggregating Empirical Test Results...")
    exp1_a_download_result.download_and_process()
    
    # -------------------------------------------------------------
    # Step 6: Plot Publication Figures
    # -------------------------------------------------------------
    if not skip_plots:
        print("\n[STEP 6/6] Generating Publication Figures from Testbed Results...")
        exp1_a_plot_results.main()
    else:
        print("\n[STEP 6/6] Skipping publication figure generation (--skip-plots enabled).")
        
    total_elapsed = round(time.time() - pipeline_t0, 2)
    print("\n################################################################")
    print(f" SCHEME A TEST PIPELINE COMPLETED SUCCESSFULLY (Elapsed: {total_elapsed}s)")
    print(" Output Files:")
    print("   - Timestamps:    paper/exp1/data/timestamps_exp1_a.csv")
    print("   - Raw Samples:   paper/exp1/data/exp1_a_raw_samples.csv")
    print("   - Summary Stats: paper/exp1/data/exp1_a_latency_summary.csv")
    print("   - Metrics JSON:  paper/exp1/data/exp1_a_metrics.json")
    print("   - Figures:       paper/exp1/plots/fig1a_scheme_a_*.png")
    print("################################################################")

def main():
    parser = argparse.ArgumentParser(description="Master Test Orchestrator for Scheme A (exp1-a).")
    parser.add_argument("--duration", type=int, default=15, help="Test measurement duration in seconds (default: 15)")
    parser.add_argument("--force-redeploy", action="store_true", help="Force redeployment of system manifests before test")
    parser.add_argument("--skip-plots", action="store_true", help="Skip publication figure generation")
    args = parser.parse_args()
    
    run_pipeline(
        duration_seconds=args.duration,
        force_redeploy=args.force_redeploy,
        skip_plots=args.skip_plots
    )

if __name__ == "__main__":
    main()
