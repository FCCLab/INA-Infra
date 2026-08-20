# 5G RLC AM Optimization & Realtime Timing Guide

This document details the root cause analysis, 3GPP protocol mechanics, and configuration tuning for optimizing **Uplink UDP and TCP throughput** in OpenAirInterface (OAI) 5G NR deployments, preventing transmission window stalls and achieving full radio link saturation.

---

## 1. Problem Overview

In default OAI 5G NR configurations under continuous Uplink UDP traffic (such as high-bitrate video streaming or `iperf3 -u`), the network frequently exhibits the following behavior:
1. **Initial Burst**: Throughput starts high (~70–130 Mbps).
2. **Sudden Drop**: After a few seconds of continuous traffic, the sustained goodput collapses to **~35–40 Mbps**, even with pristine radio signal quality (SNR > 50 dB, BLER 0.00%).
3. **Queue Overflow**: The UE logs report thousands of `[RLC] E SDU rejected, SDU buffer full` errors and `[RLC] W ack_sn not valid, discard control PDU` warnings.
4. **TCP Discrepancy**: Under the same radio conditions, TCP easily sustains **112–124 Mbps**, while raw UDP remains degraded.

---

## 2. Root Cause Analysis

The throughput collapse is caused by a multi-layer interaction between **5G RLC protocol timers**, **asymmetric TDD airtime**, and **uncontrolled UDP socket injection**:

```
[ 1. iperf3 UDP Client ] ─────► Injects 100–150 Mbps blindly into Linux socket (oaitun_ue1)
                                               │
[ 2. TDD Airtime Limit ] ─────► 7D:2U TDD radio link capacity is physically ~55 Mbps
                                               │
[ 3. RLC Queue Overload ] ────► UE RLC buffer fills up with thousands of packets in flight
                                               │
[ 4. Sliding Window Stall ] ──► tx_next - tx_next_ack >= Window_Size (2,048 packets)
                                               │
[ 5. Timer Deadlock ] ────────► gNodeB DL ACKs delayed by t_status_prohibit (15ms);
                                Stalled UE waits for t_poll_retransmit (45ms) before re-polling
                                               │
[ 6. MAC Padding / Discards ] ► UE sends MAC Padding (dummy 0x00s) during paused slots;
                                Average grant collapses from 133 PRBs ──► ~81 PRBs (~35–40 Mbps)
```

### A. 5G RLC Acknowledged Mode (AM) Sliding Window
- 5G Data Radio Bearers (DRB 1) run in **RLC Acknowledged Mode (AM)** (3GPP TS 38.322).
- The UE transmitter maintains a sliding sequence window. By default, 12-bit sequence numbers (`size12`) limit the window to **2,048 packets in flight**.
- When UDP packets arrive faster than the radio can transmit them, the sequence window exhausts. 3GPP rules **strictly forbid the UE from transmitting new data** until earlier packets are acknowledged by a Downlink RLC STATUS PDU from the gNodeB.

### B. The Out-of-Window ACK Discard Mechanism
- Under rapid UDP generation, sequence numbers advance by hundreds per millisecond.
- By the time the gNodeB decodes an Uplink block, constructs a STATUS PDU, and delivers it over a Downlink slot, the UE has already advanced.
- When the UE receives the ACK:
  ```text
  [RLC] W ack_sn (248262) not valid (tx_next_ack 248339 tx_next 248381), discard control PDU
  ```
- Because the ACK is discarded, the UE transmission window **freezes**. The UE is forced to wait for its **`t_poll_retransmit` timer (default 45 ms)** to expire before it can request a new ACK.

### C. MAC Layer Padding (Dummy Zeros)
- The physical radio layer schedules a fixed Transport Block size for 133 PRBs (e.g., ~3,500–14,000 bytes per slot).
- During the 45 ms timer freezes, the UE MAC layer is forbidden from packing new data. To maintain valid 3GPP waveforms over the scheduled slot, the UE fills the unused space with **MAC Padding** (`LCID 63`, dummy `0x00` bytes).
- Over the air, the channel appears 100% occupied (133 PRBs active), but the application only extracts ~35–40 Mbps of real payload, with the remainder discarded as padding.

### D. Why TCP Reaches 112–124 Mbps Without Degradation
- **Bidirectional Piggybacking**: TCP generates continuous Downlink TCP ACKs (over 130 MB during a 10s test). Whenever the gNodeB transmits a Downlink packet, it **piggybacks the 5G RLC radio ACK inside the same Transport Block with zero latency**.
- **End-to-End Flow Control**: TCP's Congestion Window (CWND) self-regulates to match the radio channel, never overfilling the RLC queues.

---

## 3. Theoretical Uplink Speed (3GPP TS 38.306)

For the standard INA-Infra edge radio configuration:
- **Bandwidth**: 133 PRBs (50 MHz, Band n78, 3.5 GHz)
- **Numerology**: $\mu = 1$ (30 kHz SCS $\rightarrow 0.5\text{ ms/slot}$, 2000 slots/s)
- **MIMO**: 1x1 SISO (`nb_tx = 1, nb_rx = 1`)
- **Max Modulation**: 64-QAM ($Q_m = 6$, MCS 28, Code Rate $R \approx 0.9257$)
- **TDD Pattern**: 5 ms period, 7 DL slots, 2 UL slots, 1 Special slot (6 UL symbols) $\rightarrow \mathbf{24.28\%}$ UL duty cycle ($34 / 140\text{ symbols}$).

### 3GPP Standard Formula

$$\text{Throughput} = 10^{-6} \times \left( v \times Q_m \times f \times R_{\max} \times \frac{N_{\text{PRB}} \times 12}{T_s^\mu} \times (1 - OH) \right) \times D_{\text{UL}}$$

* **Continuous 100% FDD UL capacity**: $\approx \mathbf{228.6\text{ Mbps}}$
* **TDD 7D:2U Physical Peak (PHY)**: $228.6\text{ Mbps} \times 24.28\% \approx \mathbf{55.5\text{ Mbps}}$
* **Application Goodput (IP Layer)**: $\approx \mathbf{52.0\text{ – }53.5\text{ Mbps}}$ (on physical hardware crystal clock).
* **RFSimulator Realtime Pacing**: Runs at $\approx \mathbf{75\text{ – }80\text{ Mbps}}$ due to user-space Linux kernel timer granularity ($\pm 100\mu\text{s}$ CFS scheduling).

---

## 4. The Optimized RLC Configuration

To eliminate the ACK deadlock and maximize UDP throughput, add the following `rlc` configuration block into the **DU `gnb.conf`**:

```text
    # Realtime standalone mode for 1:1 real-world clock pacing.
    time_management = {
      time_source = "realtime";
      mode = "standalone";
    };

    rlc = {
      srb = {
        t_poll_retransmit = "ms5";
        t_reassembly = "ms10";
        t_status_prohibit = "ms0";
        poll_pdu = "p16";
        poll_byte = "kB25";
        max_retx_threshold = "t8";
        sn_field_length = "size12";
      };
      drb_am = {
        t_poll_retransmit = "ms5";        # 5ms poll retransmit (down from 45ms)
        t_reassembly = "ms10";
        t_status_prohibit = "ms0";        # 0ms status prohibit (down from 15ms)
        poll_pdu = "p16";                 # Poll every 16 PDUs (down from 64)
        poll_byte = "kB25";
        max_retx_threshold = "t8";
        sn_field_length = "size18";       # 18-bit sequence numbers (window: 131,072)
      };
    };
```

### Parameter Breakdown

| Parameter | Default Value | Optimized Value | Technical Benefit |
| :--- | :--- | :--- | :--- |
| **`t_status_prohibit`** | `ms15` (15 ms) | **`ms0` (0 ms)** | **Unlocks Downlink ACKs**: gNodeB can immediately transmit RLC STATUS reports without mandatory wait intervals. |
| **`t_poll_retransmit`** | `ms45` (45 ms) | **`ms5` (5 ms)** | **Fast Recovery**: If an ACK is delayed or missed, the UE re-polls after 5 ms instead of stalling for 45 ms. |
| **`poll_pdu`** | `p64` (64 PDUs) | **`p16` (16 PDUs)** | Polls more frequently so the transmitter sliding window advances smoothly. |
| **`sn_field_length`** | `size12` (12-bit) | **`size18` (18-bit)** | **Expands Window from 2,048 $\rightarrow$ 131,072 packets**, eliminating sliding window exhaustion under high UDP packet rates. |

> [!IMPORTANT]
> **F1 Split Placement Rule**: In OAI split architecture, the `rlc = { ... }` block must be placed in **DU `gnb.conf`** ([`44-configmap-oai-du-configmap.yaml`](file:///home/fcp/INA-Infra/repos/edge-repo/namespaces/ina-infra/44-configmap-oai-du-configmap.yaml)). CU-CP does not host the RLC layer and will throw `Assertion (pl.numelt == 0) failed! Section rlc not allowed in this config` if `rlc` is present in CU-CP's `gnb.conf`.

---

## 5. Measured Benchmark Results

### A. Live UDP Performance (`iperf3 -u -b 80M -P 1 -t 10`)

Receiver report at the UPF N6 iperf3 server (`10.1.137.177`):

```text
[ ID] Interval        Transfer     Bitrate        Jitter    Lost/Total Datagrams
[  5] 0.00-10.00 sec  95.4 MBytes  80.0 Mbits/sec 0.000 ms  0/69066 (0%)       sender
[  5] 0.00-10.05 sec  95.3 MBytes  79.6 Mbits/sec 0.138 ms  24/69066 (0.035%)  receiver
```

- **Packet Delivery**: **99.965%** (only 24 packets lost out of 69,066).
- **Goodput**: **79.6 Mbps sustained**.
- **Jitter**: **0.138 ms**.

### B. Before vs. After Optimization Summary

| Metric | Default Configuration | Optimized Configuration |
| :--- | :--- | :--- |
| **Average Granted PRBs / TB** | 81.1 PRBs (~60%) | **120.5 PRBs (~91%)** |
| **Sustained UDP Goodput** | ~35 – 40 Mbps | **79.6 Mbps** |
| **UDP Packet Loss Rate** | > 40% (Buffer overflow) | **0.035%** |
| **Sustained TCP Goodput** | 112 – 124 Mbps | **112 – 124 Mbps** |
| **RLC Window Exhaustion** | Frequent (`SDU rejected`) | **Zero stalls** |

---

## 6. How to Deploy via GitOps

1. Ensure the `rlc` block is present in `repos/edge-repo/namespaces/ina-infra/44-configmap-oai-du-configmap.yaml`.
2. Push changes using the GitOps sync script:
   ```bash
   /home/fcp/INA-Infra/bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(ran): optimize RLC AM timers and window size" edge
   ```
3. Verify Config Sync reconciliation:
   ```bash
   /home/fcp/INA-Infra/scripts/check-configsync.sh edge
   ```
4. Rollout restart the edge RAN deployments:
   ```bash
   kubectl --kubeconfig ~/.kube/config-edge -n ina-infra rollout restart deployment oai-cu-cp oai-du oai-ue-slice-3-client-1
   ```
