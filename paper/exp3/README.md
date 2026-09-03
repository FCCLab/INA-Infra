# Experiment 3: Benefits of Short-Term Scheduling (PS)

## 1. Objective
Demonstrate that fast, short-term Physical Resource Block (PRB) scheduling (PS) adapts to sub-slot wireless channel dynamics, mobility-induced fading, and interference variations, preserving throughput and preventing severe SLA violations compared to static PRB allocation.

---

## 2. Experimental Comparison Matrix

| Scheme Name | PL (Placement) | PM (Compute Scaling) | PS (PRB Scheduling) | Radio PRB Policy |
| :--- | :---: | :---: | :---: | :--- |
| **Full Algorithm (Proposed)** | Yes | Yes | Yes | Dynamic, sub-slot CQI/MCS-aware PRB allocation via Near-RT RIC / FlexRIC xApp |
| **No PS Baseline (Static PRB)** | Yes | Yes | No | Fixed static PRB quota allocated per slice (e.g., equal partition or static percentage) |

---

## 3. Radio Channel & Mobility Scenarios

Realistic channel traces are generated to emulate different UE operating conditions:

| Mobility Profile | Velocity Range | Doppler / Channel Model | Typical CQI / MCS Range | Spectral Efficiency (SE) Range |
| :--- | :--- | :--- | :--- | :--- |
| **Stationary UE** | 0 km/h | AWGN / Static Line-of-Sight (LOS) | CQI: 12 - 15, MCS: 22 - 28 | 4.5 - 6.0 bps/Hz |
| **Pedestrian / Walking** | 3 - 5 km/h | Rayleigh Fading / Slow Shadowing | CQI: 8 - 13, MCS: 14 - 22 | 2.5 - 4.5 bps/Hz |
| **Vehicular UE** | 30 - 60 km/h | Fast Fading / Doppler Spread | CQI: 4 - 11, MCS: 6 - 18 | 1.2 - 3.5 bps/Hz |
| **UAV / Drone** | 20 - 40 km/h (Altitude) | 3D Ray-tracing / Rician K-factor | CQI: 6 - 14, MCS: 10 - 24 | 2.0 - 5.0 bps/Hz |

---

## 4. Stress Scenario: Deep Fading Event

* **Timeline**:
  * t = 0s to 100s: Normal channel variation (Pedestrian / Vehicular mix).
  * t = 100s to 130s (30 seconds duration): Severe deep fading event on Slice 2 (URLLC) where SNR drops by 15 dB (CQI drops from 12 to 3).
  * t = 130s onwards: Channel recovers to nominal state.
* **Evaluation Target**: Measure SLA degradation, promptness of PRB compensation by PS, and recovery time.

---

## 5. Evaluation Metrics

1. **Achieved Throughput (Mbps)**:
   * Instantaneous and time-averaged throughput delivered to each slice client.
2. **SLA Violation Rate (%)**:
   * Percentage of transmission time slots where achieved throughput falls below the slice rate guarantee.
3. **Cell-Edge / Low-SNR Throughput**:
   * 5th-percentile throughput distribution across degraded channel intervals.
4. **PRB Utilization & Allocation Efficiency**:
   * Total PRBs assigned vs. spectral efficiency achieved per PRB.
5. **Deep-Fading Recovery Time (ms)**:
   * Time elapsed from SNR drop to full throughput SLA restoration.

---

## 6. Execution Workflow

1. **Step 1: Radio Channel Profile Emulation**:
   * Feed time-varying CQI/MCS sequences to OAI DU and UEs or FlexRIC xApp (`nws-xapp`).
2. **Step 2: Static PRB Run (No PS)**:
   * Fix slice PRB limits in OAI DU (e.g., 25% PRB per slice).
   * Stream active traffic across all 4 slices and record throughput and packet drops.
3. **Step 3: Dynamic PS Run (Proposed)**:
   * Activate the Near-RT RIC PS scheduling loop.
   * Replay identical channel traces. When channel degradation occurs on Slice 2, PS instantaneously reallocates unused or low-priority PRBs to maintain throughput.
4. **Step 4: Deep Fading Injection**:
   * Inject 15 dB attenuation at t = 100s and record transient response curves.
5. **Step 5: Plotting & Log Extraction**:
   * Parse InfluxDB / Prometheus PRB metrics (`prb_utilization`, `mac_dl_bitrate`, `cqi_history`).

---

## 7. Expected Figures & Deliverables

* **Figure 3A: Time-Series Channel Variation vs. Throughput**:
  * Top Subplot: Fluctuating Spectral Efficiency trace over 300 seconds.
  * Middle Subplot (No PS): Fixed PRB line leads to steep throughput drops whenever SE dips.
  * Bottom Subplot (Dynamic PS): Throughput stays flat at the required SLA target by dynamically expanding PRB quota.
* **Figure 3B: Deep Fading Transient Response (t = 100s to 130s)**:
  * Demonstrates sub-second PRB reallocation and fast throughput recovery under PS.
* **Figure 3C: CDF of Throughput and SLA Violation Rate**:
  * Cumulative distribution showing near-zero SLA violations under PS vs. 20-35% violation rate under static allocation.

---

## 8. Key Reviewer Takeaway
PS provides indispensable millisecond-level channel responsiveness. Without PS, even an optimal long-term placement (PL) and medium-term compute sizing (PM) will fail user SLAs whenever wireless channel quality suddenly degrades.
