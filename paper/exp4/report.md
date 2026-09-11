# Experiment 4 — Synergy of PL, PM, and PS

Testbed ablation of the three INA control layers on five concurrent downlink
slices. This report uses the **live cluster captures** (not the analytical
preview in `simulate_exp4.py`). Headline plots live in
[`plots/s0_vs_s1_vs_s2_vs_s3/`](plots/s0_vs_s1_vs_s2_vs_s3/).

**Takeaway.** Placement (PL) is the layer that moves the needle on this
testbed: it cuts the N6 hairpin, recovers YOLO goodput, and drops MQTT
delay by more than half while lowering OPEX. Right-sizing (PM) then cuts
allocated cost with little SLA change. Radio slicing (PS) is OPEX-neutral
and does **not** clear the remaining violations on a 300 s contended-cell
capture — binary SLA rates stay near 100 % because \(\bar T\) comes from
*uncontended* SX runs.

---

## 1. Objective

Quantify the **marginal** contribution of each layer, with the other
layers held fixed, on one shared RAN (one DU, five UEs):

| Layer | Timescale | Decision | What it can change |
| :--- | :--- | :--- | :--- |
| **PL** | long-term | *where* CU-UP, UPF, and APP sit | transport delay, site-price OPEX |
| **PM** | medium-term | *how much* CPU / RAM / GPU is reserved | queueing, idle-compute OPEX |
| **PS** | short-term | *how* DL PRBs track the channel | fade-induced rate / delay misses |

The layers are not substitutes: PL cannot absorb a fade, PM cannot move a
function across clusters, and PS cannot shrink an idle GPU.

---

## 2. Setup

### 2.1 Four schemes

Schemes are **control configurations**, not slices. The same five DL
workloads run under every scheme.

| Scheme | NS | PL | PM | PS | Placement (CU-UP / UPF / APP) | Compute | Radio |
| :---: | :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| **S0** | `exp4-s0` | ✗ | ✗ | ✗ | all slices **C / C / E** (N6 hairpin) | frozen peak requests | equal PRB |
| **S1** | `exp4-s1` | ✓ | ✗ | ✗ | PL sites (table below) | same frozen peak | equal PRB |
| **S2** | `exp4-s2` | ✓ | ✓ | ✗ | same as S1 | PM requests = S1 usage × 1.25 | equal PRB |
| **S3** | `exp4-s3` | ✓ | ✓ | ✓ | same as S1 | same as S2 | DL `dl_min_prb_ratio` 20/20/20/20/10 %, `nws-xapp` on |

S0 is the expensive *and* high-latency anti-pattern: core-centric UPF with
MEC-everywhere apps. That is why **one** PL step can improve both SLA and
OPEX.

### 2.2 Five downlink slices

All flows are DL. E2E delay is **application** latency (radio + F1 + N3 +
N6 + processing). Throughput is application goodput, not MAC bitrate.

| Slice | App | PL site (S1–S3) | Strict SLA? | How \(d_{\mathrm{e2e}}\) is measured |
| :---: | :--- | :--- | :---: | :--- |
| **1** FTP | iperf3 `-R` + SFTP 5 MB | C / C / **C** | no (background) | last-byte − first-byte at the UE |
| **2** YOLO | annotated CCTV stream **to the UE** | **E / E / E** | **yes** | compose timestamp → first RTP/GST buffer |
| **3** OTT | gstreamer file-replay watch | **R / R / R** | **yes** | one-way \(t_{\mathrm{rx}}-t_{\mathrm{tx}}\) |
| **4** CPU-OFF | encrypt → zip → scan → LUT → 5 MB DL | C / C / **C** | no (relaxed) | download-complete − request-start |
| **5** MQTT | MQTT Get (UE pulls telemetry) | C / C / **C** | **yes** (delay) | PUBLISH timestamp → delivery at the UE |

Slice 1 occupies PRBs and N6 but is excluded from the strict SLA index.

### 2.3 SLA budgets (from SX, not the paper-draft table)

\(\bar D\) and \(\bar T\) are the **uncontended** means from scheme SX
(one UE at a time, S1 placement, frozen compute, equal PRB):

| Slice | \(\bar D\) (ms) | \(\bar T\) (Mbps) | SX run | In strict index? |
| :---: | ---: | ---: | :--- | :---: |
| 1 FTP | 88.5 | 20.2 | `slice1_20260909-205106_300` | no |
| 2 YOLO | 130.5 | 16.8 | `slice2_20260909-205713_300` | **yes** |
| 3 OTT | 66.0 | 56.9 | `slice3_20260909-210323_300` | **yes** |
| 4 CPU-OFF | 309.7 | 18.3 | `slice4_20260909-210939_300` | no |
| 5 MQTT | 75.2 | 3.67 | `slice5_20260909-211600_300` | **yes** |

A sample is a **violation** if \(d_{\mathrm{e2e}}>\bar D\) **or** delivered
rate \(<0.95\,\bar T\). The **violation score** (used when the binary rate
saturates) is

\[
\max\bigl(0,\; d/\bar D - 1\bigr) + \max\bigl(0,\; 1 - r/(0.95\,\bar T)\bigr).
\]

Zero means the sample meets both budgets. These bars are **optimistic**
once five UEs share the cell: SX \(\bar T\) is isolated-slice goodput.
OTT at 56.9 Mbps in particular cannot be five-way fair-shared.

Paper-draft budgets (250 / 45 / 58 / 400 / 80 ms and 20 / 12 / 22 / 8 /
2 Mbps) appear only in the **S3** capture’s own `summary.json`. Cross-scheme
plots recompute every sample against the SX bars above.

### 2.4 OPEX model

Allocated-request cost, not measured usage:

\[
C = \rho_{\mathrm{site}}\sum_{k\in\{\mathrm{cpu,ram,gpu,vram}\}}
\tfrac14\cdot u_k / U_k
\]

with \(\rho\): central = 1, regional = 2, edge = 4. Units \(U\): 4 CPU,
12 GiB RAM, 1 GPU, 48 GiB A40 VRAM. S0/S1 bill frozen peak requests;
S2/S3 bill the PM-resized requests in `s2/scheme.py` / `s3/scheme.py`.
A reserved GPU bills the full A40 VRAM even if the process uses ~0.7 GiB.

### 2.5 Captures used in this report

Each live run is **300 s** at 1 Hz after a settle window. Two campaigns
on 9 Sep 2026:

| Campaign | Role in this report | S0 | S1 | S2 | S3 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **A** (morning–afternoon) | complete S0–S3 ablation; evaluation figures | `20260909-102816_300s` | `20260909-110400_300s` | `20260909-121245_300s` | `20260909-160900_300s` |
| **B** (evening) | latest S0–S2 with SX \(\bar D/\bar T\) baked into per-scheme plots | `20260909-224603_300s` | `20260909-231724_300s` | `20260909-233004_300s` | *not re-run* |

S3 has not been repeated after the SX calibration. Campaign **A** is the
only four-scheme comparison. Campaign **B** is the cleaner S0→S1→S2
walk against SX bars.

There is **no 24 h diurnal + fade replay** in these CSVs. Night / lunch /
afternoon-burst / 15 dB fade windows from the experiment design were not
applied. PM and PS are therefore tested as *static* request and PRB
policies, not as closed-loop traces.

---

## 3. Isolated baseline (SX)

SX measures what each app can do with the cell to itself at S1 sites.
Those means **are** \(\bar D\) and \(\bar T\).

| Slice | Mean delay (ms) | Mean DL (Mbps) | Paper-draft \(\bar T\) |
| :---: | ---: | ---: | ---: |
| 1 FTP | 88.5 | 20.2 | 20 |
| 2 YOLO | 130.5 | 16.8 | 12 |
| 3 OTT | 66.0 | 56.9 | 22 |
| 4 CPU-OFF | 309.7 | 18.3 | 8 |
| 5 MQTT | 75.2 | 3.67 | 2 |

Isolated OTT already sits at 66 ms / 57 Mbps — so any contended OTT run
will miss \(\bar D\) and \(\bar T\) unless the other four UEs are quiet.
Isolated MQTT delay is 75 ms; contended MQTT is 0.8–2.4 s, so most of
that gap is the broker / Get path, not radio.

---

## 4. Per-scheme results (Campaign B, latest S0–S2)

### 4.1 S0 — static hairpin (PL ✗ PM ✗ PS ✗)

Every APP on edge, every UPF on central. Frozen 2 CPU / 1–12 GiB (YOLO:
4 CPU + 1 GPU). Equal PRBs.

![S0 mean delay and throughput vs SX SLA](s0/data/20260909-224603_300s/plots/means_vs_sla.png)

![S0 300 s timeseries vs SX budgets](s0/data/20260909-224603_300s/plots/timeseries_sla.png)

![S0 per-slice violation rate](s0/data/20260909-224603_300s/plots/violation_rates.png)

| Slice | Delay (ms) | \(\bar D\) | DL (Mbps) | \(\bar T\) | Viol. score | Viol. % |
| :---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 FTP | 169 | 88.5 | 11.2 | 20.2 | 1.33 | 100 |
| 2 YOLO* | 211 | 130.5 | **4.7** | 16.8 | 1.32 | 100 |
| 3 OTT* | 205 | 66.0 | 34.2 | 56.9 | 2.48 | 100 |
| 4 CPU-OFF | 376 | 309.7 | 11.1 | 18.3 | 0.58 | 100 |
| 5 MQTT* | **2359** | 75.2 | 1.99 | 3.67 | **30.8** | 100 |

Strict index (slices 2, 3, 5): **100 %** of samples violate, mean score
**11.41**. YOLO is starved on the hairpin (4.7 Mbps vs 16.8 isolated).
MQTT delay is ~30× \(\bar D\).

### 4.2 S1 — +PL only

UPF+APP co-located: YOLO at edge, OTT at regional, FTP / CPU-OFF / MQTT
at central. Same frozen requests and equal PRBs as S0.

![S1 mean delay and throughput vs SX SLA](s1/data/20260909-231724_300s/plots/means_vs_sla.png)

![S1 300 s timeseries vs SX budgets](s1/data/20260909-231724_300s/plots/timeseries_sla.png)

![S1 per-slice violation rate](s1/data/20260909-231724_300s/plots/violation_rates.png)

| Slice | Delay (ms) | DL (Mbps) | Viol. score | Viol. % | vs S0 |
| :---: | ---: | ---: | ---: | ---: | :--- |
| 1 FTP | 153 | 9.31 | 1.25 | 100 | delay ↓, rate slightly ↓ |
| 2 YOLO* | **156** | **16.8** | **0.24** | **89.7** | delay −55 ms; rate restored to SX \(\bar T\) |
| 3 OTT* | 176 | **41.5** | 1.94 | 100 | delay −29 ms; +7.3 Mbps |
| 4 CPU-OFF | 356 | 8.42 | 0.67 | 100 | delay ↓; rate ↓ (central sharing) |
| 5 MQTT* | **1132** | 2.57 | **14.3** | 100 | delay −1.2 s (−52 %) |

Strict index: **96.6 %** violate, mean score **5.61** (−51 % vs S0).
YOLO is the PL exhibit: co-locating CU-UP / UPF / APP / GPU at edge
recovers isolated-slice goodput even with four other UEs on the cell.
The binary rate stays high because OTT and MQTT still miss SX \(\bar D\).

### 4.3 S2 — +PL +PM

Same sites as S1. Requests resized from S1 measured usage × 1.25
(run `20260909-110400_300s`): FTP 300m/320Mi, YOLO 7.1 CPU/2Gi/1 GPU,
OTT 100m/128Mi, CPU-OFF 500m/320Mi, MQTT 1.05 CPU/192Mi. Limits still
burst to 8 CPU / 8Gi. PRBs still equal.

![S2 mean delay and throughput vs SX SLA](s2/data/20260909-233004_300s/plots/means_vs_sla.png)

![S2 300 s timeseries vs SX budgets](s2/data/20260909-233004_300s/plots/timeseries_sla.png)

| Slice | Delay (ms) | DL (Mbps) | Viol. score | Viol. % | vs S1 |
| :---: | ---: | ---: | ---: | ---: | :--- |
| 1 FTP | 164 | 9.14 | 1.38 | 100 | ≈ same |
| 2 YOLO* | 166 | 16.5 | 0.34 | 92.0 | slightly worse delay |
| 3 OTT* | 187 | 37.4 | 2.16 | 100 | slightly worse |
| 4 CPU-OFF | 363 | 8.79 | 0.67 | 100 | ≈ same |
| 5 MQTT* | **891** | 2.71 | **11.1** | 100 | delay −241 ms vs S1 |

Strict index: **97.2 %** violate, mean score **4.46** (better than S1
on MQTT, which dominates the score). On a 300 s flat load, PM does not
have a diurnal signal to track; it is a **one-shot right-size**. YOLO
GPU stays 1.0 (cannot fraction an A40), so compute SLA barely moves.
The PM win is OPEX (next section).

### 4.4 S3 — +PL +PM +PS (Campaign A only)

Same sites and PM requests as S2. DL-only min-PRB floors summing to 95 %
of the cell, sized from the **paper-draft** \(\bar T\) (20+12+22+8+2 =
64 Mbps), *not* from SX 20.2+16.8+56.9+18.3+3.67. `nws-xapp` replicas = 1.
Capture `20260909-160900_300s` (afternoon; native plot still draws the
old 45 / 58 / 80 ms and 12 / 22 / 2 Mbps bars).

Means recomputed against SX bars (from the four-scheme table):

| Slice | Delay (ms) | DL (Mbps) | vs S2 (Campaign A) |
| :---: | ---: | ---: | :--- |
| 1 FTP | 227 | 6.58 | delay ↑, rate ↓ (leftover PRBs) |
| 2 YOLO* | 187 | 11.7 | delay ↑, rate ↓ |
| 3 OTT* | 204 | 25.3 | delay ↑ vs S2 171 ms / 23.4 Mbps |
| 4 CPU-OFF | 463 | 6.51 | leftover |
| 5 MQTT* | 824 | 2.89 | delay ↓ vs S2 905 ms; rate ↑ |

PS did **not** produce the analytical fade-recovery. OTT’s floor is 32.6 %
of the cell from draft \(\bar T=22\), while SX \(\bar T=56.9\) would
demand a much larger share. This capture also has no injected 15 dB
fades, so there is little for PS to steal PRBs *from*.

---

## 5. Cross-scheme evaluation (Campaign A, S0–S3)

Figures from `python3 paper/exp4/compare_schemes.py --schemes s0 s1 s2 s3`
on the morning/afternoon runs. Violation score and rate are recomputed
with current SX \(\bar D/\bar T\). Source table:
[`plots/s0_vs_s1_vs_s2_vs_s3/means.csv`](plots/s0_vs_s1_vs_s2_vs_s3/means.csv).

### 5.1 One-page evaluation

![Exp4 evaluation: delay, throughput, violation, resources, OPEX](plots/s0_vs_s1_vs_s2_vs_s3/evaluation.png)

### 5.2 Delay, throughput, violation, OPEX

![Means: delay, throughput, violation, OPEX](plots/s0_vs_s1_vs_s2_vs_s3/means_delay_throughput_violation_opex.png)

**Means (Campaign A)**

| Slice | | S0 | S1 | S2 | S3 | S0→S1 |
| :---: | :--- | ---: | ---: | ---: | ---: | :--- |
| 1 FTP | delay ms | 217 | 216 | 214 | 227 | ~0 |
| | Mbps | 9.37 | 6.89 | 7.20 | 6.58 | −2.5 |
| | viol. score | 1.96 | 2.08 | 2.05 | 2.22 | |
| 2 YOLO* | delay ms | 229 | **177** | 176 | 187 | **−52** |
| | Mbps | 4.23 | **12.9** | 12.8 | 11.7 | **+8.7** |
| | viol. score | 1.49 | **0.58** | 0.59 | 0.70 | |
| 3 OTT* | delay ms | 219 | **191** | **171** | 204 | −28 |
| | Mbps | 18.7 | **31.3** | 23.4 | 25.3 | **+12.5** |
| | viol. score | 2.97 | 2.32 | 2.16 | 2.65 | |
| 4 CPU-OFF | delay ms | 435 | 438 | 451 | 463 | ~0 |
| | Mbps | 9.16 | 6.69 | 6.96 | 6.51 | −2.5 |
| | viol. score | 0.88 | 1.03 | 1.06 | 1.12 | |
| 5 MQTT* | delay ms | **2085** | **758** | 905 | 824 | **−1327** |
| | Mbps | 2.14 | 2.69 | 2.63 | 2.89 | +0.54 |
| | viol. score | **27.1** | **9.33** | 11.3 | 10.2 | |

**Strict index (slices 2 / 3 / 5)**

| | S0 static | S1 +PL | S2 +PL+PM | S3 +PL+PM+PS |
| :--- | ---: | ---: | ---: | ---: |
| Binary violation | ~100 % | ~100 % | ~100 % | ~100 % |
| Mean violation score | **10.75** | **4.15** | 4.67 | 4.52 |
| Score vs S0 | 100 % | **39 %** | 43 % | 42 % |

The binary rate is the wrong headline: almost every sample misses SX
\(\bar D\) or \(0.95\bar T\) once five UEs share the cell. The **score**
is the waterfall: PL removes ~61 % of the strict miss; PM and PS then
move the residual by a few points, and not always in the designed
direction (Campaign A PM slightly *raises* the score because MQTT delay
regressed 758 → 905 ms; Campaign B PM *lowers* it 5.61 → 4.46 on the
same MQTT term).

FTP and CPU-OFF **lose** goodput under PL (9.4 → 6.9 and 9.2 → 6.7 Mbps).
That is expected: S0 parked those APPs on the edge with a dedicated N6
hairpin onto a less-loaded path; S1 puts them on central with YOLO’s
siblings, and they are best-effort under PS. They are not in the strict
index.

### 5.3 Timeseries (delay / throughput)

![300 s delay and throughput, all schemes](plots/s0_vs_s1_vs_s2_vs_s3/timeseries_throughput_latency.png)

Read slice by slice:

- **FTP** — all schemes sit above \(\bar D=88.5\) ms and below
  \(\bar T=20.2\) Mbps. S0 actually has the highest FTP rate (grey).
- **YOLO** — S0 (grey) is a flat ~4–6 Mbps band; S1/S2 (blue/orange)
  jump to the SX \(\bar T\) line. S3 (green) lands in between. Delay
  remains a noisy band around 150–250 ms, often above 130.5 ms.
- **OTT** — S1 (blue) is the throughput winner (~30 Mbps, touching 80
  in bursts). S0 and S3 stay lower. Delay is always above 66 ms.
- **CPU-OFF** — S3 delay is systematically higher (green, ~450–550 ms):
  leftover PRBs. Pipeline time (~90 ms encrypt/zip/scan/LUT) is inside
  every sample, so this slice is never radio-only.
- **MQTT** — log delay axis. S0 is a 1–4 s cloud; S1–S3 drop to ~0.5–2 s
  but never approach 75 ms. Throughput hovers around 2–4 Mbps for all
  schemes (near SX \(\bar T=3.67\)).

### 5.4 Server usage

![Measured CPU, RAM, GPU, VRAM](plots/s0_vs_s1_vs_s2_vs_s3/means_usage.png)

Measured usage is **almost scheme-invariant**:

| Slice | CPU (m) | RAM (MiB) | GPU % | VRAM (MiB) |
| :---: | ---: | ---: | ---: | ---: |
| FTP | ~200–260 | ~240 | 0 | 0 |
| YOLO | ~5600–5650 | 1450–1760 | ~8.9 | ~665 |
| OTT | ~64 | ~94 | 0 | 0 |
| CPU-OFF | ~360–480 | ~240 | 0 | 0 |
| MQTT | ~820–840 | ~106–118 | 0 | 0 |

YOLO is the only GPU user, and it uses ~9 % of the A40 / ~0.65 GiB of
48 GiB. PM cannot return that GPU (device plugin is 1-or-0), so the
dominant OPEX term is structural, not a utilization loop.

### 5.5 Allocated OPEX

![Per-slice and resource-class OPEX](plots/s0_vs_s1_vs_s2_vs_s3/means_cost.png)

![OPEX timeseries (flat: requests × site, not live usage)](plots/s0_vs_s1_vs_s2_vs_s3/timeseries_cost.png)

OPEX is a **step**, not a trace: it bills requests × \(\rho_{\mathrm{site}}\).
The timeseries is therefore a horizontal line per scheme.

| Slice | Site S0 → S1 | S0 | S1 | S2 / S3 | What changed |
| :---: | :--- | ---: | ---: | ---: | :--- |
| 1 FTP | edge→central | 0.58 | 0.15 | **0.03** | \(\rho\) 4→1, then 2 CPU→300m |
| 2 YOLO | edge (stays) | **4.00** | **4.00** | **3.94** | GPU+VRAM pinned; RAM 12Gi→2Gi |
| 3 OTT | edge→regional | 0.67 | 0.33 | **0.02** | \(\rho\) 4→2, then 2 CPU→100m |
| 4 CPU-OFF | edge→central | 0.58 | 0.15 | **0.04** | \(\rho\) 4→1, then 2 CPU→500m |
| 5 MQTT | edge→central | 0.17 | 0.07 | 0.07 | \(\rho\) 4→1; CPU actually *up* 500m→1050m |
| **Total** | | **6.00** | **4.70** | **4.09** | S0=100 %, S1=78 %, S2=S3=68 % |

YOLO is ~67 % of S0 cost and still ~96 % of S2 cost. PL cannot move it
off the edge (GPU). PM’s OPEX cut is almost entirely **right-sizing
CPU/RAM on slices 1, 3, 4** after they leave the edge. PS does not
change requests, so S3 OPEX = S2 OPEX.

---

## 6. Layer-by-layer evaluation

### 6.1 PL (S0 → S1) — the large step

Both campaigns agree.

| Effect | Campaign A | Campaign B (evening) |
| :--- | :--- | :--- |
| YOLO delay | 229 → 177 ms | 211 → 156 ms |
| YOLO goodput | 4.2 → 12.9 Mbps | 4.7 → **16.8 Mbps** (= SX \(\bar T\)) |
| OTT delay | 219 → 191 ms | 205 → 176 ms |
| OTT goodput | 18.7 → 31.3 Mbps | 34.2 → 41.5 Mbps |
| MQTT delay | 2085 → 758 ms | 2359 → 1132 ms |
| Strict score | 10.75 → 4.15 (−61 %) | 11.41 → 5.61 (−51 %) |
| Total OPEX | 6.00 → 4.70 (−22 %) | same request model |

Mechanism: kill the N6 hairpin and put delay-sensitive GPU work next to
the radio. Best-effort APPs leave the expensive edge. That is the
“one step improves SLA **and** OPEX” story, and the testbed shows it.

FTP/CPU-OFF goodput falling is the other side of the same placement:
they no longer get an edge-local hairpin, and they are not SLA-protected.

### 6.2 PM (S1 → S2) — OPEX, not SLA

On a flat 300 s load there is no night/lunch/1.2× afternoon burst, so PM
cannot demonstrate the designed queue-clearing. What it *does*
demonstrate is request hygiene:

- Total OPEX 4.70 → 4.09 (−13 % vs S1, −32 % vs S0).
- Measured CPU/RAM/GPU **do not drop** — the apps already fit in the
  smaller requests (S1 YOLO used 5.6 CPU / 1.6 GiB inside a 4 CPU / 12 GiB
  ask; S2 asks 7.1 CPU / 2 GiB).
- SLA: Campaign B MQTT delay 1132 → 891 ms (score 5.61 → 4.46);
  Campaign A MQTT 758 → 905 ms (score 4.15 → 4.67). Noise on a
  broker-dominated path, not a compute-queue story.
- YOLO GPU stays 1.0, so the dominant cost and the YOLO `d_{\mathrm{proc}}`
  term are unchanged.

PM is doing the job the cost model can see. It is not doing the job the
analytical waterfall assigned it (afternoon 1.2× queue overflow), because
that load shape was not replayed.

### 6.3 PS (S2 → S3) — not visible on this capture

Designed role: steal PRBs from slices 1 and 4 during ~15 dB fades on
slices 2 and 3. Observed on Campaign A:

- OPEX unchanged (no request change).
- Strict score 4.67 → 4.52 (noise).
- YOLO and FTP goodput **down**; CPU-OFF delay **up** (consistent with
  leftover / low-priority PRBs, but without a compensating boost on 2/3).
- PRB floors were computed from draft \(\bar T\) (OTT 22 Mbps → 32.6 %),
  not SX OTT 56.9 Mbps.
- No fade schedule in the 300 s window.

Until S3 is re-run (a) against SX \(\bar T\) floors, (b) with the fade
trace, (c) in the same session as S2, PS should not be claimed as the
last drop of the SLA waterfall.

### 6.4 Against the analytical preview

`simulate_exp4.py` on the designed 24 h + fade trace:

| | S0 | S1 (+PL) | S2 (+PM) | S3 (+PS) |
| :--- | ---: | ---: | ---: | ---: |
| SLA residual (sim) | 100 % | 20.7 % | 7.1 % | 0.2 % |
| OPEX residual (sim) | 100 % | 58.7 % | 48.3 % | 48.3 % |
| Strict score (testbed A) | 100 % | 39 % | 43 % | 42 % |
| OPEX (testbed A) | 100 % | 78 % | 68 % | 68 % |

OPEX has the same *shape* (PL large, PM medium, PS flat) but a smaller
PL cut, because YOLO’s GPU stays on the edge in every scheme. SLA does
**not** follow 100 → 21 → 7 → 0.2: the testbed PL step is real (~40 %
residual score) and the PM/PS steps are not, for the reasons above.
Do not overlay sim and cluster points on one figure.

---

## 7. What a reviewer should take

| Layer | Primary benefit on this testbed | Look at | Do not claim (yet) |
| :--- | :--- | :--- | :--- |
| **PL** | Co-locate UPF+APP; get GPU off the hairpin; move best-effort off the edge | YOLO Mbps, MQTT delay, OPEX 6.00→4.70 | “SLA index to 0” |
| **PM** | Right-size CPU/RAM to measured usage | OPEX 4.70→4.09; usage vs requests | “clears afternoon queues” (no diurnal trace) |
| **PS** | Policy is deployed (DL min-PRB + xApp) | leftover on FTP/CPU-OFF | “clears fades” (no fade trace; floors from draft \(\bar T\)) |
| **PL+PM+PS** | Pareto walk is **down-left on OPEX**, **down on score at PL only** | evaluation figure | sim waterfall heights |

**Campaign B one-slide numbers (latest S0–S2, SX bars):**

| | S0 | S1 | S2 |
| :--- | ---: | ---: | ---: |
| YOLO Mbps | 4.7 | **16.8** | 16.5 |
| MQTT delay (ms) | 2359 | 1132 | **891** |
| Strict violation score | 11.41 | 5.61 | **4.46** |
| Strict binary viol. % | 100 | 96.6 | 97.2 |
| OPEX (allocated, same model as A) | 6.00 | 4.70 | 4.09 |

---

## 8. Caveats and follow-ups

1. **Five UEs vs SX \(\bar T\).** Binary violation against isolated-slice
   goodput will stay near 100 % for OTT (\(\bar T=56.9\)) and MQTT delay
   (\(\bar D=75\) ms). Report **score**, means, and timeseries — not the
   binary index — as the SLA figure.
2. **300 s, not 24 h.** PM and PS need the shared diurnal + fade file
   from the experiment design before their waterfall drops can be
   measured.
3. **S3 not in Campaign B.** Re-run S3 in the same session as evening
   S0–S2, with `dl_min_prb_ratio` 20/20/20/20/10 % (slices 1–5).
4. **MQTT delay is application-dominated.** PL halves it (hairpin gone)
   but the residual is still 10× \(\bar D\). Fixing Get/broker timestamping
   would change the strict score more than PS.
5. **YOLO GPU is 1.0 in every scheme.** PM cannot return it; PL cannot
   place YOLO on cheap central. That caps the OPEX waterfall at ~68 % of
   S0.
6. **Do not mix** these CSVs with Exp1–3, or sim points with cluster
   points.

---

## 9. How the figures were produced

```bash
# Per-scheme plots (already written next to each run)
python3 paper/exp4/exp_plot.py --scheme s0 --run-id 20260909-224603_300s
python3 paper/exp4/exp_plot.py --scheme s1 --run-id 20260909-231724_300s
python3 paper/exp4/exp_plot.py --scheme s2 --run-id 20260909-233004_300s

# Four-scheme evaluation (Campaign A) → paper/exp4/plots/s0_vs_s1_vs_s2_vs_s3/
python3 paper/exp4/compare_schemes.py --schemes s0 s1 s2 s3 \
    --s0-run-id 20260909-102816_300s \
    --s1-run-id 20260909-110400_300s \
    --s2-run-id 20260909-121245_300s \
    --s3-run-id 20260909-160900_300s
```

Analytical preview only (not used as paper numbers):

```bash
python3 paper/exp4/simulate_exp4.py
python3 paper/exp4/plot_exp4.py
```
