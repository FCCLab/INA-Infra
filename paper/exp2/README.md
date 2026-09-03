# Experiment 2: Benefits of Medium-Term Scaling (PM)

## 1. Objective
Isolate and demonstrate the benefits of dynamic compute scaling (PM) for network functions (CU-UP, UPF, App servers) under realistic time-varying diurnal traffic loads compared to static peak-capacity overprovisioning.

---

## 2. Experimental Comparison Matrix

| Scheme Name | PL (Placement) | PM (Compute Scaling) | PS (PRB Scheduling) | Compute Resource Policy |
| :--- | :---: | :---: | :---: | :--- |
| **Full Algorithm (Proposed)** | Yes | Yes | Yes | Dynamic CPU/RAM/GPU elasticity following medium-term traffic demand curves |
| **No PM Baseline (Static)** | Yes | No | Yes | Fixed CPU/RAM/GPU capacity allocated at initial slice admission (sized for peak) |

---

## 3. Diurnal Traffic Scenario (24-Hour Profile)

To emulate realistic subscriber activity, an accelerated or simulated 24-hour diurnal traffic cycle is executed:

| Time Interval | Period Label | Traffic Multiplier | Description |
| :--- | :--- | :---: | :--- |
| **00:00 - 08:00** | Night / Off-Peak | 20% (0.2x) | Low background activity, idle compute opportunities |
| **08:00 - 12:00** | Morning Office Hours | 100% (1.0x) | Nominal operational traffic baseline |
| **12:00 - 14:00** | Lunch Break | 40% (0.4x) | Dip in enterprise/office compute demand |
| **14:00 - 18:00** | Afternoon Peak Burst | 120% (1.2x) | Peak load, high contention on compute queues |
| **18:00 - 24:00** | Evening Streaming | 80% (0.8x) | Shift toward entertainment / OTT video traffic |

---

## 4. Sensitivity Scenario: Traffic Forecast Error
Evaluate the robustness of the PM scaling controller against imperfect traffic predictions by injecting Gaussian estimation error into the scaling trigger:
* **Forecast Error Levels**: 0% (Oracle), 10%, 20%, 30%, and 50% Mean Absolute Percentage Error (MAPE).
* Evaluates over-scaling penalties (cost) and under-scaling penalties (queue backlog / packet drops).

---

## 5. Evaluation Metrics

1. **CPU / Memory Allocation vs. Actual Demand**:
   * Allocated CPU Cores vs. Consumed CPU Cores over time.
2. **Resource Wastage Ratio**:
   * Wastage = Integral over time of (Allocated Resources - Consumed Resources) / Total Allocated Resources.
3. **Compute OPEX**:
   * Compute Cost = Sum over time [ Active Cores(t) * Unit Cost per Core-Hour ].
4. **Queue Backlog & Processing Latency**:
   * Packet buffer depth in CU-UP / UPF queues (packets or bytes).
   * Internal NF packet processing delay (ms).
5. **OPEX Reduction Percentage**:
   * Cost Savings (%) = ((Cost_No_PM - Cost_PM) / Cost_No_PM) * 100 (Target: 25% to 40% reduction).

---

## 6. Execution Workflow

1. **Step 1: Benchmark Baseline Setup (No PM)**:
   * Allocate static peak resources to CU-UP, UPF, and App pods (e.g., 4.0 CPU cores, 8 GB RAM).
   * Play the 24-hour traffic trace and record resource usage, latency, and queue backlog.
2. **Step 2: Dynamic Scaling Run (PM Enabled)**:
   * Activate the PM control loop (evaluating scaling thresholds at medium-term intervals, e.g., every 60 seconds).
   * Replay the exact same 24-hour traffic trace.
   * PM dynamically adjusts CPU requests/limits and worker thread allocations.
3. **Step 3: Forecast Error Stress Testing**:
   * Inject 10%, 20%, 30%, and 50% forecast noise into the PM decision module.
   * Measure degradation in SLA satisfaction and excess resource consumption.
4. **Step 4: Data Processing & Plotting**:
   * Extract time-series data from Prometheus (`node_cpu_seconds_total`, `container_cpu_usage_seconds_total`, queue lengths).
   * Plot comparative time-series and bar charts.

---

## 7. Expected Figures & Deliverables

* **Figure 2A: Time-Series CPU Allocation vs. Traffic Demand**:
  * No PM: Flat line at maximum peak allocation (large area of wasted compute).
  * PM: Dynamic staircase curve tightly tracking traffic demand with minimal overhead headroom.
* **Figure 2B: Queue Backlog & Processing Delay Over Time**:
  * Shows that PM promptly expands capacity before the 14:00-18:00 afternoon burst, preventing queue overflow.
* **Figure 2C: OPEX Savings & Wastage Comparison**:
  * Bar plot showing 25% to 40% OPEX savings across different slice combinations.
* **Figure 2D: Robustness vs. Traffic Forecast Error**:
  * SLA satisfaction remains > 98% even with up to 30% prediction error due to safety margins.

---

## 8. Key Reviewer Takeaway
PM eliminates the high cost of static peak-load overprovisioning by dynamically resizing compute resources according to diurnal traffic fluctuations, achieving substantial OPEX savings while safeguarding buffer queues during bursts.
