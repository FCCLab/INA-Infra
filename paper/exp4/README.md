# Experiment 4: Synergy Between PL - PM - PS (Ablation & Layer Breakdown)

## 1. Objective
Provide a rigorous ablation study and synergy analysis that isolates and quantifies the exact marginal gain of each control tier (PL, PM, and PS). This proves to reviewers that all three timescales are strictly necessary to achieve end-to-end SLA satisfaction and OPEX minimization.

---

## 2. Experimental Scheme Matrix (Stepwise Ablation)

| Scheme ID | Scheme Name | PL (Placement) | PM (Compute Scaling) | PS (PRB Scheduling) | Configuration Description |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **S0** | Static Baseline | No | No | No | Fixed central placement, static peak compute, static equal PRBs |
| **S1** | + PL Only | Yes | No | No | Optimal multi-tier placement, static peak compute, static equal PRBs |
| **S2** | + PL + PM | Yes | Yes | No | Optimal placement + dynamic compute scaling, static equal PRBs |
| **S3** | Full Proposed (+ PL + PM + PS) | Yes | Yes | Yes | Fully integrated multi-timescale optimization across all layers |

---

## 3. Integrated Realistic Evaluation Scenario

The evaluation combines all real-world dynamics simultaneously over an extended evaluation window:
* **Heterogeneous Slices**: eMBB (4K OTT), URLLC (Physical-AI), Video Analytics (CCTV), and IoT.
* **Diurnal Compute Cycles**: 24-hour traffic fluctuations (20% to 120% load multipliers).
* **Wireless Channel Fluctuations**: Rayleigh/Rician fading, UE mobility (pedestrian + vehicular), and deep fading bursts.
* **Multi-Cluster Infrastructure**: Edge (`gpu-a40` / USRP), Regional (`gpu-gh82`), and Central (`gpu-gh81` / 5GC).

---

## 4. Evaluation Metrics & Quantifiable Targets

1. **Normalized SLA Violation Index (%)**:
   * Measures the cumulative volume of packets that exceed delay limits or fail throughput guarantees.
   * Baseline S0 normalized to 100%.
2. **Normalized Network OPEX (%)**:
   * Combined cost of compute core-hours, memory, GPU allocation, and inter-cluster transport bandwidth.
   * Baseline S0 normalized to 100%.
3. **End-to-End Resource Efficiency**:
   * Useful Throughput delivered per Unit Cost (Mbps / Dollar).
4. **Tail Latency (99th and 99.9th percentile delay in ms)**.

---

## 5. Execution Workflow

1. **Step 1: Execute S0 (Static Baseline)**:
   * Deploy all components centrally with fixed maximum resources and static PRBs.
   * Run the full dynamic traffic + channel profile and log SLA drops and cost.
2. **Step 2: Execute S1 (+ PL)**:
   * Activate multi-cluster placement (PL). Run the exact same profile.
   * Quantify delay reduction and initial OPEX savings from workload tiering.
3. **Step 3: Execute S2 (+ PL + PM)**:
   * Enable dynamic compute resizing on top of PL.
   * Measure reduction in idle compute waste and resulting OPEX drop during off-peak hours.
4. **Step 4: Execute S3 (Full + PL + PM + PS)**:
   * Enable sub-slot PRB scheduling (PS) alongside PL and PM.
   * Measure how PS eliminates radio-induced SLA drops during channel fading.
5. **Step 5: Aggregation & Waterfall Plot Generation**:
   * Aggregate normalized metrics across all runs into a unified waterfall breakdown.

---

## 6. Expected Figures & Journal Visualizations

### A. Waterfall Plot 1: SLA Violation Reduction
* **S0 (Static Baseline)**: 100% (High violations due to transport delays + radio fading)
* **S1 (+ PL)**: 65% (35% drop — eliminates core transport bottleneck for URLLC/CCTV)
* **S2 (+ PL + PM)**: 35% (30% drop — prevents compute queue overflow during bursts)
* **S3 (+ PL + PM + PS)**: 10% (25% drop — eliminates radio fading throughput drops)

### B. Waterfall Plot 2: Network OPEX Reduction
* **S0 (Static Baseline)**: 100% (High cost from peak compute overprovisioning + transport)
* **S1 (+ PL)**: 80% (20% savings — offloads non-critical compute from expensive edge)
* **S2 (+ PL + PM)**: 55% (25% savings — dynamic compute downscaling during off-peak)
* **S3 (+ PL + PM + PS)**: 50% (5% savings — higher spectral efficiency and fewer retransmissions)

### C. Layer Synergy & Contribution Matrix

| Control Layer | Primary Operational Role | Isolated Benefit | Target Key Metric |
| :--- | :--- | :--- | :--- |
| **PL (Long-Term)** | Multi-Cluster Topology & Placement | Eliminates geographical / transport latency | Latency SLA, Transport OPEX |
| **PM (Medium-Term)** | Elastic Compute Sizing | Eliminates idle compute overprovisioning | CPU/RAM Wastage, Compute OPEX |
| **PS (Short-Term)** | Fast Radio PRB Scheduling | Adapts to fast channel variations & fading | Throughput, Packet Loss Rate |
| **PL + PM + PS** | End-to-End Multi-Tier Control | Synergy across radio, compute, and topology | Joint SLA-OPEX Pareto Frontier |

---

## 7. Key Reviewer Takeaway
This ablation proves that no single layer is sufficient on its own: PL optimizes **where** functions reside, PM optimizes **how much** compute is active, and PS optimizes **how** radio resources handle channel dynamics. All three layers act in harmony to deliver maximum SLA guarantees at minimum OPEX.
