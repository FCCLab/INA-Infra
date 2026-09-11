# Exp4 test report — PL / PM / PS ablation

| Field | Value |
| :--- | :--- |
| DUT | Nephio 5G slicing testbed, one shared DU, five concurrent DL UEs |
| Date | 2026-09-11 |
| Capture | 300 s, 1 Hz, after settle |
| Evaluator | `compare_schemes.py` + `exp_start.SLICES` |
| Plots | [`plots/s0_vs_s1_vs_s2_vs_s3/`](plots/s0_vs_s1_vs_s2_vs_s3/) |

---

## 1. Test conditions

### 1.1 Clusters and RAN

| Cluster | Context | Role |
| :--- | :--- | :--- |
| mgmt | `mgmt@mgmt` | GitOps |
| central | `central@central` | 5G core; S1–S3 UPF/APP slices 1, 4, 5 |
| regional | `regional@regional` | S1–S3 UPF/APP slice 3 (OTT) |
| edge | `edge@edge` | CU-CP, DU, UEs; S1–S3 UPF/APP slice 2 (YOLO GPU) |

| Item | Value |
| :--- | :--- |
| RAN | OAI rfsim, one DU, five NR-UEs |
| N6 | Multus macvlan `10.1.137.0/24` |
| UE rfsim | `10.1.140.141`–`145` |
| Duration | 300 s |
| Sample rate | 1 Hz |
| Load | five DL slices concurrent (no diurnal / fade replay) |

### 1.2 Slices (same workloads in every scheme)

| Slice | App | DNN | IMSI | APP IP | UE console | Delay metric |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | FTP (iperf3 `-R` + SFTP 5 MB) | oai1 | 001010000000101 | 10.1.137.211 | 10.1.137.221 | last-byte − first-byte |
| 2 | YOLO CCTV DL | oai2 | 001010000000102 | 10.1.137.212 | 10.1.137.222 | compose ts → first RTP/GST |
| 3 | OTT gstreamer watch | oai3 | 001010000000103 | 10.1.137.213 | 10.1.137.223 | \(t_{\mathrm{rx}}-t_{\mathrm{tx}}\) |
| 4 | CPU-OFF (encrypt→zip→scan→LUT→5 MB) | oai4 | 001010000000104 | 10.1.137.214 | 10.1.137.224 | complete − request-start |
| 5 | MQTT Get | oai5 | 001010000000105 | 10.1.137.215 | 10.1.137.225 | PUBLISH ts → UE delivery |

### 1.3 Schemes

Schemes are **control configurations**, not slices. The same five DL
workloads (§1.2) run under every scheme on one shared DU.

| Layer | Timescale | Decision | What is configured |
| :--- | :--- | :--- | :--- |
| **PL** | long-term | *where* CU-UP, UPF, and APP sit | site per slice (§1.4) |
| **PM** | medium-term | *how much* CPU / RAM / GPU is reserved | K8s requests (§1.5) |
| **PS** | short-term | *how* DL PRBs are shared | scheduler + min-PRB (§1.6) |

| Scheme | NS | PL | PM | PS | What this run is | Capture |
| :---: | :--- | :---: | :---: | :---: | :--- | :--- |
| **S0** | `exp4-s0` | ✗ | ✗ | ✗ | Static baseline: all APP on edge, UPF on central (N6 hairpin), peak compute, equal PRB | `20260909-224603_300s` |
| **S1** | `exp4-s1` | ✓ | ✗ | ✗ | S0 + **placement only**: CU-UP / UPF / APP co-located per slice; same peak requests and equal PRB | `20260910-124216_300s` |
| **S2** | `exp4-s2` | ✓ | ✓ | ✗ | S1 + **right-size compute**: requests = S1 measured usage × 1.25; still equal PRB | `20260910-125308_300s` |
| **S3** | `exp4-s3` | ✓ | ✓ | ✓ | S2 + **radio slicing**: NSDL, DL min-PRB 20/20/20/20/10 %, dedicated 0, `nws-xapp` on | `20260911-232242_300s` |

Each step adds one layer and holds the others fixed (ablation). S1 vs S0 isolates PL; S2 vs S1 isolates PM; S3 vs S2 isolates PS.

### 1.4 Placement (CU-UP / UPF / APP)

C = central, R = regional, E = edge.

| Slice | S0 | S1 / S2 / S3 |
| :---: | :---: | :---: |
| 1 FTP | C / C / **E** | C / C / **C** |
| 2 YOLO | C / C / **E** | **E / E / E** |
| 3 OTT | C / C / **E** | **R / R / R** |
| 4 CPU-OFF | C / C / **E** | C / C / **C** |
| 5 MQTT | C / C / **E** | C / C / **C** |

### 1.5 Compute requests

S0/S1: frozen peak. S2/S3: PM = S1 measured usage × 1.25 (run `20260909-110400_300s`). Limits 8 CPU / 8 GiB. GPU not fractional.

| Slice | S0 / S1 CPU | S0 / S1 RAM | S0 / S1 GPU | S2 / S3 CPU | S2 / S3 RAM | S2 / S3 GPU |
| :---: | ---: | :--- | ---: | ---: | :--- | ---: |
| 1 FTP | 2.0 | 1 Gi | 0 | 0.30 | 320 Mi | 0 |
| 2 YOLO | 4.0 | 12 Gi | 1 | 7.1 | 2 Gi | 1 |
| 3 OTT | 2.0 | 2 Gi | 0 | 0.10 | 128 Mi | 0 |
| 4 CPU-OFF | 2.0 | 1 Gi | 0 | 0.50 | 320 Mi | 0 |
| 5 MQTT | 0.5 (S0) / 1.0 (S1) | 512 Mi | 0 | 1.05 | 192 Mi | 0 |

### 1.6 Radio (PS)

| Item | S0 / S1 / S2 | S3 |
| :--- | :--- | :--- |
| DL scheduler | equal PRB | NSDL `dl_scheduler_type=1` |
| UL scheduler | PF | PF `ul_scheduler_type=0` |
| `dedicated_prb_ratio` | — | 0 |
| `dl_min_prb_ratio` % | equal share | 20 / 20 / 20 / 20 / 10 (slices 1–5) |
| `nws-xapp` | idle | replicas = 1 |

### 1.7 Isolated baseline (SX, one UE at a time)

| Slice | Mean delay (ms) | Mean DL (Mbps) | SX run |
| :---: | ---: | ---: | :--- |
| 1 FTP | 88.5 | 20.2 | `slice1_20260909-205106_300` |
| 2 YOLO | 130.5 | 16.8 | `slice2_20260909-205713_300` |
| 3 OTT | 66.0 | 56.9 | `slice3_20260909-210323_300` |
| 4 CPU-OFF | 309.7 | 18.3 | `slice4_20260909-210939_300` |
| 5 MQTT | 75.2 | 3.67 | `slice5_20260909-211600_300` |

---

## 2. Coefficients

### 2.1 SLA

Source: paper eqs. **(6-9)–(6-10)** — absolute slack degree of SLA miss.
Lab bars in `exp_start.SLICES` (\(D^s\equiv\bar D\), \(T^s\equiv\bar T\)).

| Symbol | Slice | Value |
| :--- | :---: | ---: |
| \(D^s\) (\(\bar D\)) | 1 FTP | 150 ms |
| \(D^s\) | 2 YOLO | 150 ms |
| \(D^s\) | 3 OTT | 150 ms |
| \(D^s\) | 4 CPU-OFF | 350 ms |
| \(D^s\) | 5 MQTT | 1000 ms |
| \(T^s\) (\(\bar T\)) | 1 FTP | 10 Mbps |
| \(T^s\) | 2 YOLO | 25 Mbps |
| \(T^s\) | 3 OTT | 25 Mbps |
| \(T^s\) | 4 CPU-OFF | 10 Mbps |
| \(T^s\) | 5 MQTT | 3 Mbps |
| — | rate pass | \(T_s > T^s\) |
| — | \(w_D / w_T\) (tunable) | 0.1 / 10 |
| — | slice \(w\) | YOLO 1, OTT 1, MQTT 1 |
| — | strict index | slices **2, 3, 5** |

Paper absolute slacks (optimizer; used as the Exp4 score):

\[
\xi_s^{D}=\max(0,\,D_s-D^s)\quad[\mathrm{ms}],\qquad
\xi_s^{C}=\max(0,\,T^s-T_s)\quad[\mathrm{Mbps}]
\]

Per-sample score (paper \(W^P\) split into tunable \(w_D,w_T\); units differ):

\[
s = w_D\,\xi_s^{D} + w_T\,\xi_s^{C}
\]

\[
s = 0.1\,\max(0,\,D_s-D^s) + 10\,\max(0,\,T^s-T_s)
\]

Headline / journal Fig 4A: strict mean of \(s\) over slices \(\{2,3,5\}\),
as residual \(R_{\mathrm{sch}}=100\times s_{\mathrm{sch}}/s_{S0}\).
Binary miss (\(D_s>D^s\) or \(T_s\le T^s\)) is in §3.1 only.
S1 ≈ S2 on SLA (PM is OPEX-only); S0→S1 (PL) and S2→S3 (PS) move residual.

### 2.2 OPEX

Source: `paper/exp4/cost_model.py`.

\[
C = \rho_{\mathrm{site}}\bigl(p_{\mathrm{cpu}}\,vCPU + p_{\mathrm{ram}}\,GiB + p_{\mathrm{gpu}}\,GPU + p_{\mathrm{vram}}\,GiB_{\mathrm{vram}}\bigr)
\quad [\$/h]
\]

| Coefficient | Symbol | Value |
| :--- | :--- | ---: |
| Site price, central | \(\rho_C\) | 1 |
| Site price, regional | \(\rho_R\) | 2 |
| Site price, edge | \(\rho_E\) | 4 |
| CPU | \(p_{\mathrm{cpu}}\) | 0.80 \$/core·h |
| RAM | \(p_{\mathrm{ram}}\) | 0.08 \$/GiB·h |
| GPU | \(p_{\mathrm{gpu}}\) | 1.20 \$/GPU·h |
| VRAM | \(p_{\mathrm{vram}}\) | 0.00625 \$/GiB·h |
| A40 VRAM | \(U_{\mathrm{vram}}\) | 48 GiB |

Billing: S0/S1 = peak requests. S2/S3 = measured CPU/RAM (capped at S1 peak); GPU/VRAM = S1 allocation.

### 2.3 PM / PS / slice model

| Coefficient | Value |
| :--- | :--- |
| PM request factor | 1.25 × S1 mean usage |
| PS min-PRB (S3) | 20, 20, 20, 20, 10 % |
| PS dedicated (S3) | 0 |
| Slice \(\eta_{t0}\) | FTP 2.4, YOLO 2.2, OTT 2.5, CPU-OFF 2.3, MQTT 2.6 |
| Slice \(h_s\) | YOLO 1, others 0 |

---

## 3. Results

### 3.1 Strict SLA (slices 2, 3, 5)

| | S0 | S1 | S2 | S3 |
| :--- | ---: | ---: | ---: | ---: |
| Binary violation (%) | 92.1 | 77.8 | 74.9 | 69.2 |
| Mean score | 126.37 | 45.04 | 39.31 | 25.33 |
| Score residual (% of S0) | 100 | 35.6 | 31.1 | 20.0 |
| OPEX (\$/h) | 44.88 | 30.36 | 20.71 | 20.75 |
| OPEX residual (% of S0) | 100 | 67.6 | 46.1 | 46.2 |

Fig 4A / 6-panel last panel use **score residual** (S0 = 100%): S0 > S1 ≈ S2 > S3
(S1→S2 is OPEX-only on SLA). S3 capture: `20260911-232242_300s` (latest).
OPEX S2≈S3 (±0.1 pp).

### 3.2 Per-slice means

| Slice | Metric | S0 | S1 | S2 | S3 |
| :---: | :--- | ---: | ---: | ---: | ---: |
| 1 FTP | delay (ms) | 169 | 149 | 147 | 132 |
| | DL (Mbps) | 11.2 | 10.2 | 9.83 | 9.78 |
| | viol. % | 76.2 | 75.7 | 79.0 | 73.0 |
| | score | 9.24 | 9.27 | 11.0 | 10.0 |
| 2 YOLO* | delay (ms) | 211 | 159 | 159 | 162 |
| | DL (Mbps) | 4.72 | 16.8 | 16.7 | 26.4 |
| | viol. % | 100 | 99.6 | 99.2 | 67.6 |
| | score | 209 | 84.2 | 84.4 | 9.95 |
| 3 OTT* | delay (ms) | 205 | 121 | 128 | 104 |
| | DL (Mbps) | 34.2 | 33.1 | 32.4 | 26.2 |
| | viol. % | 82.8 | 61.4 | 61.6 | 66.8 |
| | score | 24.9 | 33.3 | 21.8 | 52.8 |
| 4 CPU-OFF | delay (ms) | 376 | 369 | 367 | 344 |
| | DL (Mbps) | 11.1 | 9.20 | 8.93 | 9.90 |
| | viol. % | 86.0 | 87.8 | 88.9 | 75.8 |
| | score | 10.2 | 15.7 | 17.2 | 9.78 |
| 5 MQTT* | delay (ms) | 2359 | 823 | 725 | 797 |
| | DL (Mbps) | 1.99 | 2.56 | 2.76 | 2.55 |
| | viol. % | 94.0 | 71.3 | 61.9 | 73.0 |
| | score | 150 | 16.0 | 8.66 | 13.1 |

\* strict SLA.

### 3.3 Server usage (measured)

| Slice | CPU (m) | RAM (MiB) | GPU % | VRAM (MiB) |
| :---: | ---: | ---: | ---: | ---: |
| FTP | 200–260 | ~240 | 0 | 0 |
| YOLO | 5600–5650 | 1450–1760 | ~8.9 | ~665 |
| OTT | ~64 | ~94 | 0 | 0 |
| CPU-OFF | 360–480 | ~240 | 0 | 0 |
| MQTT | 820–840 | 106–118 | 0 | 0 |

### 3.4 Allocated OPEX (\$/h)

| Slice | \(\rho\) S0→S1 | S0 | S1 | S2 | S3 |
| :---: | :--- | ---: | ---: | ---: | ---: |
| 1 FTP | 4→1 | 6.72 | 1.68 | 0.23 | 0.22 |
| 2 YOLO | 4→4 | 22.64 | 22.64 | 19.31 | 19.32 |
| 3 OTT | 4→2 | 7.04 | 3.52 | 0.12 | 0.11 |
| 4 CPU-OFF | 4→1 | 6.72 | 1.68 | 0.39 | 0.41 |
| 5 MQTT | 4→1 | 1.76 | 0.84 | 0.67 | 0.68 |
| **Total** | | **44.88** | **30.36** | **20.71** | **20.75** |

CSV: [`plots/s0_vs_s1_vs_s2_vs_s3/means.csv`](plots/s0_vs_s1_vs_s2_vs_s3/means.csv).

---

## 4. Figures

Each figure is one 300 s capture (or the four-scheme overlay). Bars/lines are the measured series; dashed marks are the SLA coefficients in §2.1.

### 4.1 Per scheme

**S0** — static (PL ✗ PM ✗ PS ✗), run `20260909-224603_300s`. Five DL UEs concurrent. Placement: CU-UP/UPF central, APP edge. Frozen peak requests. Equal PRB.

![S0 means vs SLA](s0/data/20260909-224603_300s/plots/means_vs_sla.png)

*Mean E2E delay (ms) and mean DL goodput (Mbps) per slice vs \(\bar D\) and \(\bar T\).*

![S0 timeseries](s0/data/20260909-224603_300s/plots/timeseries_sla.png)

*1 Hz traces of E2E delay and DL goodput for slices 1–5 over the 300 s window. Dashed: \(\bar D\); dashed/dotted: \(\bar T\) and \(0.95\,\bar T\).*

![S0 violation rates](s0/data/20260909-224603_300s/plots/violation_rates.png)

*Per-slice binary violation rate (% of samples with \(d>\bar D\) or \(r<0.95\,\bar T\)). Slices 2, 3, 5 are the strict index.*

**S1** — +PL only, run `20260910-124216_300s`. Same workloads, peak requests, and equal PRB as S0. Placement: PL sites (§1.4).

![S1 means vs SLA](s1/data/20260910-124216_300s/plots/means_vs_sla.png)

*Mean E2E delay and mean DL goodput per slice vs \(\bar D\) and \(\bar T\) (S1).*

![S1 timeseries](s1/data/20260910-124216_300s/plots/timeseries_sla.png)

*1 Hz delay and DL goodput vs SLA bars, slices 1–5 (S1).*

![S1 violation rates](s1/data/20260910-124216_300s/plots/violation_rates.png)

*Per-slice binary violation rate (S1).*

**S2** — +PL +PM, run `20260910-125308_300s`. Same placement as S1. Compute requests = S1 usage × 1.25. Equal PRB.

![S2 means vs SLA](s2/data/20260910-125308_300s/plots/means_vs_sla.png)

*Mean E2E delay and mean DL goodput per slice vs \(\bar D\) and \(\bar T\) (S2).*

![S2 timeseries](s2/data/20260910-125308_300s/plots/timeseries_sla.png)

*1 Hz delay and DL goodput vs SLA bars, slices 1–5 (S2).*

**S3** — +PL +PM +PS, run `20260911-232242_300s`. Same placement and PM requests as S2. DL min-PRB 20/20/20/20/10 %, dedicated 0, `nws-xapp` on.

![S3 means vs SLA](s3/data/20260911-232242_300s/plots/means_vs_sla.png)

*Mean E2E delay and mean DL goodput per slice vs \(\bar D\) and \(\bar T\) (S3).*

![S3 timeseries](s3/data/20260911-232242_300s/plots/timeseries_sla.png)

*1 Hz delay and DL goodput vs SLA bars, slices 1–5 (S3).*

![S3 violation rates](s3/data/20260911-232242_300s/plots/violation_rates.png)

*Per-slice binary violation rate (S3).*

### 4.2 Cross-scheme (S0 vs S1 vs S2 vs S3)

Same five slices and SLA coefficients. Overlay of the four captures in §1.3.

![journal summary](plots/s0_vs_s1_vs_s2_vs_s3/fig4_journal_summary.png)

*Four-panel journal board (S0 = 100%). A: SLA score residual waterfall (strict slices 2/3/5). B: OPEX waterfall. C: useful throughput and Mbps per \$ vs S0. D: SLA–OPEX Pareto walk S0→S3.*

![SLA waterfall](plots/s0_vs_s1_vs_s2_vs_s3/fig4a_sla_waterfall.png)

*McKinsey waterfall of the paper SLA index: bars are S0 residual, then PL / PM / PS layer deltas, then S3 residual. Y-axis is strict mean violation score as % of S0.*

![OPEX waterfall](plots/s0_vs_s1_vs_s2_vs_s3/fig4b_opex_waterfall.png)

*Same waterfall layout for total allocated OPEX (\$/h) as % of S0.*

![throughput efficiency](plots/s0_vs_s1_vs_s2_vs_s3/fig4c_throughput_efficiency.png)

*Grouped bars: sum of per-slice mean DL goodput, and goodput / OPEX, each normalized to S0 = 100%.*

![layer contribution](plots/s0_vs_s1_vs_s2_vs_s3/fig4d_layer_contribution.png)

*Layer table: SLA and OPEX change in percentage points of S0 for PL (S0→S1), PM (S1→S2), PS (S2→S3), and the full stack.*

![Pareto](plots/s0_vs_s1_vs_s2_vs_s3/fig4e_sla_opex_pareto.png)

*Four points S0, S1, S2, S3 on the plane OPEX (% of S0) vs SLA score residual (% of S0).*

![evaluation](plots/s0_vs_s1_vs_s2_vs_s3/evaluation.png)

*One-page board: per-slice mean delay, DL goodput, binary violation rate, SLA residual (% of S0), CPU, RAM, GPU, VRAM, and OPEX; last panel is total OPEX per scheme. Marks on delay/throughput panels are \(\bar D\) / \(\bar T\).*

![delay throughput violation OPEX](plots/s0_vs_s1_vs_s2_vs_s3/means_delay_throughput_violation_opex.png)

*Grouped bars per slice: mean E2E delay, mean DL goodput, per-slice OPEX, binary violation rate, violation score; last panel is score residual as % of S0 (strict slices 2/3/5). One bar group per scheme.*

![timeseries](plots/s0_vs_s1_vs_s2_vs_s3/timeseries_throughput_latency.png)

*Aligned 300 s traces: E2E delay (left) and DL goodput (right) for slices 1–5, four schemes overlaid. Horizontal lines: \(\bar D\), \(\bar T\).*

![usage](plots/s0_vs_s1_vs_s2_vs_s3/means_usage.png)

*Mean application-server usage per slice and scheme: CPU (millicores), RAM (MiB), GPU (%), VRAM (MiB). S0/S1 show allocated requests; S2/S3 show measured usage.*

![OPEX](plots/s0_vs_s1_vs_s2_vs_s3/means_cost.png)

*Allocated OPEX (\$/h): per-slice total (left) and stack of CPU / RAM / GPU / VRAM shares (right), using \(\rho\) and prices in §2.2.*

![OPEX timeseries](plots/s0_vs_s1_vs_s2_vs_s3/timeseries_cost.png)

*OPEX vs time (\$/h) for each slice. Flat because cost is requests × \(\rho_{\mathrm{site}}\), not live utilization.*

---

## 5. Reproduce

```bash
# Latest complete run per scheme (summary.json mtime); omit --*-run-id to auto-pick.
python3 paper/exp4/exp_plot.py --scheme s0
python3 paper/exp4/exp_plot.py --scheme s1
python3 paper/exp4/exp_plot.py --scheme s2
python3 paper/exp4/exp_plot.py --scheme s3

python3 paper/exp4/compare_schemes.py --schemes s0 s1 s2 s3
```
