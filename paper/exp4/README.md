# Experiment 4: Synergy Between PL–PM–PS (Ablation & Layer Breakdown)

Primary journal figure set. Stepwise ablation of the three control layers
instead of a single “proposed vs static” bar.

**Scope:** Exp4 only. **4 schemes** (S0–S3) × **5 downlink slices**.
Schemes are control configurations, not slices: there is no “slice 0”.
The same five DL workloads run under every scheme on **one shared**
traffic + channel trace.

| Symbol | Means |
| :--- | :--- |
| **S0, S1, S2, S3** | Schemes (which layers are on) |
| **Slice 1–5** | DL workloads present in every scheme |

---

## 1. Objective

Quantify the marginal contribution of each layer to SLA, OPEX, throughput,
and resource efficiency, with the other layers held fixed:

| Layer | Timescale | Decision | Primary effect |
| :--- | :--- | :--- | :--- |
| **PL** | Long-term (hours–days) | *Where* CU-UP, UPF, and the APP run (edge / regional / central) | Transport delay, site-price OPEX |
| **PM** | Medium-term (tens of seconds) | *How much* CPU / RAM / GPU is allocated | Queue backlog, idle-compute OPEX |
| **PS** | Short-term (TTI / sub-slot) | *How* PRBs react to the wireless channel | Fade-induced throughput and SLA |

The layers are not interchangeable: PL cannot absorb a fade, PM cannot move
a function across clusters, and PS cannot shrink a GPU that is idle at 03:00.

---

## 2. Four schemes

| Scheme | NS (testbed) | PL | PM | PS | Placement | Compute | Radio |
| :---: | :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| **S0** | `exp4-s0` | ✗ | ✗ | ✗ | Static: CU-UP + UPF **central**, APP **edge** (all slices) | Frozen peak \(\bar T\) | Equal PRB (1/5 cell) |
| **S1** | `exp4-s1` | ✓ | ✗ | ✗ | PL sites (table below) | Same frozen \(\bar T\) as S0 | Equal PRB |
| **S2** | `exp4-s2` | ✓ | ✓ | ✗ | Same as S1 | PM resizes CPU/RAM/GPU each medium-term cycle | Equal PRB |
| **S3** | `exp4-s3` | ✓ | ✓ | ✓ | Same as S1 | Same as S2 | PS re-reserves PRBs from live \(\eta\) (MCS / CQI) |

S0 is the expensive *and* high-latency anti-pattern (core-centric UPF +
MEC-everywhere apps): long N6 hairpins, edge-priced compute for best-effort
work, and delay-sensitive apps still traversing the core. That is what lets
**one** PL step improve both SLA and OPEX — the waterfall is not a
“vs all-central” or “vs all-edge” plot.

Run schemes **sequentially** on the live RAN (shared DU / UE set). Do not
mix Exp1–3 namespaces or a different load profile across S0–S3.

---

## 3. Five downlink slices

All measured flows are **DL only**. E2E latency is **application** latency:
radio + F1 + N3 + N6 **plus** application processing at the chosen site.
CPU/GPU allocation therefore appears inside the delay sample, not only in OPEX.

Clocks are NTP-synced on the management plane (`ens0`), never on the PDU.
Throughput is **application goodput**, not MAC bitrate.

### 3.1 Slice catalog

| Slice | DL application | PL target (S1–S3) CU-UP / UPF / APP | Strict SLA? | How \(d_{\mathrm{e2e}}\) is measured | How throughput is measured |
| :---: | :--- | :--- | :---: | :--- | :--- |
| **1** | `iperf` DL + SFTP download (5 MB) | central / central / **central** | No | SFTP: \(t_\text{last byte} - t_\text{first byte}\) at the UE. `iperf` RTT is diagnostic only. | SFTP: \(40\,\text{Mbit} / T_\text{transfer}\). `iperf` DL goodput on `oaitun_ue1`. |
| **2** | YOLO + bbox overlay streamed **to the UE** | **edge / edge / edge** (`gpu-a40` / USRP) | Yes | APP compose timestamp on the annotated frame → first RTP/GST buffer at the UE. GPU/CPU queue included. | Annotated-frame payload × FPS / window. |
| **3** | gstreamer / OTT video **watch** (file replay) | **regional / regional / regional** | Yes | GST/RTP timestamp; one-way \(t_\text{rx} - t_\text{tx}\). Also RFC 3550 jitter. | gstreamer payload bitrate (not MAC). |
| **4** | Secured file download: encrypt → zip → virus scan → LUT → 5 MB DL | central / central / **central** | Relaxed | \(t_\text{download complete} - t_\text{request start}\). AES/zip/scan/LUT time is inside the sample. | \(40\,\text{Mbit} / T_\text{e2e}\) plus server-side pipeline goodput. |
| **5** | MQTT **Get** (UE pulls farm telemetry) | central / central / **central** | Yes (delay) | MQTT PUBLISH timestamp → GET / subscribed delivery at the UE. | Messages × payload / window. |

Slice 1 is the **background UE**. It occupies PRBs and N6 but is excluded
from the strict SLA index.

### 3.2 Application processing model

```
d_e2e = d_rf + d_f1(CU) + d_n3(CU,UPF) + d_n6(UPF,APP) + d_proc(alloc, demand)
```

| Slice | App | \(d_\text{proc}\) at nominal alloc | Scales with |
| :---: | :--- | ---: | :--- |
| 1 | SFTP / iperf | ~2 ms stack | none (transfer time dominates) |
| 2 | YOLO bbox compose | ~18 ms inference + overlay | **GPU** (CPU queue if the worker is starved) |
| 3 | gstreamer watch | ~8 ms stamp / mux / jitter buffer | CPU |
| 4 | Encrypt + zip + scan + LUT (5 MB) | ~90 ms pipeline | **CPU** |
| 5 | MQTT Get | ~3 ms broker + LUT | CPU |

If allocated compute < offered load, \(d_\text{proc}\) stretches by the
utilization ratio (M/M/1-style queue). That is the PM term in the SLA waterfall.

### 3.3 SLA thresholds

| Slice | Delay budget \(\bar D\) | Rate budget \(\bar T\) | Counted in SLA index? |
| :---: | ---: | ---: | :---: |
| 1 Best-effort DL | 250 ms (informational) | 20 Mbps | No (best effort) |
| 2 YOLO bbox DL | 250 ms | 9 Mbps | **Yes** |
| 3 Video watch DL | 100 ms | 18 Mbps | **Yes** |
| 4 CPU offload DL | 400 ms (relaxed) | 8 Mbps | No (reported separately) |
| 5 MQTT Get | 900 ms | 2.2 Mbps | **Yes** |

A strict-slice sample (2, 3, 5) is a **violation** if
\(d_\text{e2e} > \bar D\) **or** delivered rate \(< 0.95\,\bar T_\text{offered}\).

---

## 4. Per-scheme plan for all five slices

Site = CU-UP / UPF / APP. C = central, R = regional, E = edge.

### 4.1 S0 — static (PL ✗ PM ✗ PS ✗)

Anti-pattern: UPF in the core, APP at the edge, peak compute, equal PRBs.

| Slice | CU-UP | UPF | APP | Compute | PRB |
| :---: | :---: | :---: | :---: | :--- | :--- |
| 1 | C | C | **E** | Frozen peak \(\bar T\) | 1/5 cell |
| 2 | C | C | **E** | Frozen peak + GPU | 1/5 cell |
| 3 | C | C | **E** | Frozen peak | 1/5 cell |
| 4 | C | C | **E** | Frozen peak | 1/5 cell |
| 5 | C | C | **E** | Frozen peak | 1/5 cell |

N6 hairpin on every slice. Slice 2 GPU sits on edge while UPF is central.

### 4.2 S1 — +PL only (PL ✓ PM ✗ PS ✗)

Same frozen compute and equal PRBs as S0. Only **where** functions sit changes.

| Slice | CU-UP | UPF | APP | Compute | PRB |
| :---: | :---: | :---: | :---: | :--- | :--- |
| 1 | C | C | **C** | Frozen peak | 1/5 cell |
| 2 | **E** | **E** | **E** | Frozen peak + GPU | 1/5 cell |
| 3 | **R** | **R** | **R** | Frozen peak | 1/5 cell |
| 4 | C | C | **C** | Frozen peak | 1/5 cell |
| 5 | C | C | **C** | Frozen peak | 1/5 cell |

Co-located UPF+APP. Slice 2 at edge (delay + GPU). Slice 3 regional breakout.
Slices 1/4/5 on cheap central.

### 4.3 S2 — +PL+PM (PL ✓ PM ✓ PS ✗)

Same sites as S1. Compute follows the shared diurnal load. PRBs still equal.

| Slice | Placement | Compute (PM) | PRB |
| :---: | :--- | :--- | :--- |
| 1 | C / C / C | Scale CPU to 0.2×–1.2× load; no GPU | 1/5 cell |
| 2 | E / E / E | Scale **GPU + CPU** (bbox \(d_{\mathrm{proc}}\)) | 1/5 cell |
| 3 | R / R / R | Scale CPU (GST mux / jitter buffer) | 1/5 cell |
| 4 | C / C / C | Scale **CPU** (encrypt/zip/scan/LUT) | 1/5 cell |
| 5 | C / C / C | Scale small CPU (broker + LUT) | 1/5 cell |

Night/lunch: release idle cores. Afternoon 1.2×: grow before queues stretch
Slice 2/4 delay.

### 4.4 S3 — +PL+PM+PS (PL ✓ PM ✓ PS ✓)

Same sites as S1. Same PM as S2. PRBs track live \(\eta\).

| Slice | Placement | Compute | PRB (PS) |
| :---: | :--- | :--- | :--- |
| 1 | C / C / C | PM as S2 | Leftover / low priority (no strict SLA) |
| 2 | E / E / E | PM as S2 | **Boost in fades** (strict rate + delay) |
| 3 | R / R / R | PM as S2 | **Boost in fades** (main PS victim) |
| 4 | C / C / C | PM as S2 | Leftover (relaxed) |
| 5 | C / C / C | PM as S2 | Enough PRBs for 2 Mbps; delay-bound |

Deep fades (~15 dB, 6 short windows) hit **slices 2 and 3 only**. PS steals
PRBs from slices 1 and 4.

### 4.5 One-page matrix

|  | S0 | S1 | S2 | S3 |
| :--- | :--- | :--- | :--- | :--- |
| **Slice 1** site | C/C/**E** | C/C/**C** | C/C/C | C/C/C |
| **Slice 2** site | C/C/**E** | **E/E/E** | E/E/E | E/E/E |
| **Slice 3** site | C/C/**E** | **R/R/R** | R/R/R | R/R/R |
| **Slice 4** site | C/C/**E** | C/C/**C** | C/C/C | C/C/C |
| **Slice 5** site | C/C/**E** | C/C/**C** | C/C/C | C/C/C |
| **Compute** | frozen | frozen | **PM** | **PM** |
| **PRB** | equal | equal | equal | **PS** |

---

## 5. Metrics

All headline bars are **normalized to S0 = 100%**.

| Metric | Definition | Units |
| :--- | :--- | :--- |
| **SLA violation index** | Fraction of strict-slice samples (slices **2, 3, 5**) that miss delay or rate. | % of S0 |
| **OPEX** | \(\sum_t\) (CPU + RAM + GPU) × site unit price + N3/N6/F1 transport + reserved-PRB cost. | % of S0 |
| **Throughput** | Sum of *useful application* goodput (SFTP, YOLO bbox, gstreamer, file pipeline, MQTT), not MAC bitrate. | Mbps, and % of S0 |
| **Resource efficiency** | Useful throughput / OPEX (Mbps per cost unit). | % of S0 |

Secondary traces (appendix / per-slice CSV): p50/p95/p99 \(d_\text{e2e}\),
jitter (slice 3), CPU/GPU utilization, queue stretch, PRB usage.

---

## 6. Shared trace (same for S0–S3)

Replay one 24 h profile so the waterfall is an ablation, not a traffic
difference. Compress wall time if needed; keep the same fade schedule.

| Period | Hours | Load × \(\bar T\) | Channel |
| :--- | ---: | ---: | :--- |
| Night | 00–08 | 0.20 | Pedestrian CQI |
| Morning | 08–12 | 1.00 | Mixed pedestrian / vehicular |
| Lunch | 12–14 | 0.40 | Pedestrian |
| Afternoon burst | 14–18 | 1.20 | Vehicular + periodic deep fades |
| Evening | 18–24 | 0.80 | Pedestrian / indoor |

Deep-fade bursts (≈ 15 dB, 6 short windows) hit the radio of slices 2 and 3.
PS is the only layer that can re-allocate PRBs inside those windows.

Record seed, fade timestamps, and bitrate ladders before S0. Reuse that
file for S1–S3. If offered load drifts more than ±5% or Slice 3 bitrate
ladder changes, abort and rerun that scheme.

---

## 7. Figures (journal set)

### Figure 4A — SLA violation waterfall

McKinsey-style waterfall, S0 = 100%:

```
S0 Static     ████████████████████  100%
+ PL          ████████████          residual after removing transport / N6 hairpin
+ PM          ██████                residual after removing burst-queue overflow
+ PS          ██                    residual after removing fade-induced rate misses
S3 Full       █                     leftover (irreducible radio + proc)
```

Illustrative shape in the paper draft was **100 → 65 → 35 → 10**. The
analytical ablation (`simulate_exp4.py`, same diurnal + fade trace)
currently reports **100 → 20.7 → 7.1 → 0.2**: PL removes the N6-hairpin
delay miss, PM clears the afternoon compute miss, and PS clears residual
fades. Replace these heights with testbed CSVs when S0–S3 are measured
on the cluster.

### Figure 4B — OPEX waterfall

```
S0 Static     ████████████████████  100%
+ PL          ████████████████      cheaper sites for best-effort; kill N6 hairpin
+ PM          ███████████           release idle CPU/GPU at night / lunch
+ PS          ██████████            small: fewer HARQ / retransmit tails
S3 Full       ██████████
```

Illustrative shape: **100 → 80 → 55 → 50**. Analytical run:
**100 → 58.7 → 48.3 → 48.3**. PL does the large OPEX cut (best-effort
leaves the edge; N6 hairpin dies). PM returns idle night/lunch cores.
PS is almost OPEX-neutral and spends its budget on SLA / throughput.

### Figure 4C — Throughput and resource efficiency

Grouped bars for S0–S3: mean useful throughput (Mbps) and efficiency
(Mbps / cost). PS should lift throughput under fades; PM should lift
efficiency more than raw Mbps.

### Figure 4D — Layer contribution (strongest summary table)

| Control layer | Primary benefit | Metric the reviewer should look at |
| :--- | :--- | :--- |
| **PL** | Optimal placement (where) | OPEX, E2E latency (transport + site proc) |
| **PM** | Elastic compute scaling (how much) | CPU / GPU utilization, queue backlog, compute OPEX |
| **PS** | Channel adaptation (how radio reacts) | SLA violation, throughput under fade |
| **PL+PM+PS** | End-to-end multi-timescale control | Joint SLA–OPEX Pareto (S0→S3 walk) |

### Figure 4E — SLA vs OPEX Pareto walk

Four points (S0, S1, S2, S3) on the SLA-violation vs OPEX plane. The path
should move **down and left**: each added layer improves at least one axis
without giving the other back.

---

## 8. Testbed campaign (Exp4 only)

Sequential, one scheme live at a time. Core CP (AMF/SMF/…) stays on
**central**. Only UPF, CU-UP, and APP move.

| Knob | Off | On |
| :--- | :--- | :--- |
| **PL** | Force S0 sites in `ina-pl-placement` | Write S1–S3 site map; Config Sync places UPF / CU-UP / APP |
| **PM** | CPU/GPU requests = peak \(\bar T\); INA PM loop idle | INA PM loop resizes each medium-term cycle from demand |
| **PS** | DU slice PRB = equal split; `nws-xapp` idle or static | `nws-xapp` re-reserves from live CQI/MCS |

**Per scheme (S0 → S1 → S2 → S3):**

1. Undeploy previous `exp4-*` (UEs first, then UP / APP).
2. Deploy this scheme; wait Config Sync + five UE PDUs.
3. Confirm placement: S0 must show APP on edge and UPF on central;
   S1–S3 must match §4.
4. Confirm knobs: PM frozen vs live; PRB equal vs xApp.
5. Replay the shared trace (same start offset, same fade times).
6. Tag Influx with `scheme=S#`. Checkpoint `paper/exp4/data/raw/S#.csv`.
7. Only then undeploy and start the next scheme.

**Dedicated app trees** (do not edit Exp1 `applications/servers|clients` for this run):

| Slice | Directory | Change for this DL set |
| :---: | :--- | :--- |
| 1 | [`applications/exp4/exp4_s1_iperf_sftp`](../../applications/exp4/exp4_s1_iperf_sftp/) | SFTP first/last-byte + iperf3 `-R` |
| 2 | [`applications/exp4/exp4_s2_cctv`](../../applications/exp4/exp4_s2_cctv/) | YOLO annotated stream **to the UE** (slice 2 / `.212`) |
| 3 | [`applications/exp4/exp4_s3_ott`](../../applications/exp4/exp4_s3_ott/) | Watch DL only |
| 4 | [`applications/exp4/exp4_s4_cpu_offload`](../../applications/exp4/exp4_s4_cpu_offload/) | New encrypt/zip/LUT download pod |
| 5 | [`applications/exp4/exp4_s5_iot`](../../applications/exp4/exp4_s5_iot/) | MQTT Get on `slice_5/` / `.215` |

Do not reuse Slice 4 as MQTT. Do not mix these CSVs with Exp1–3.

---

## 8.1 Testbed: one dedicated directory per scheme

Each scheme is a self-contained package. Run **one** at a time on the shared RAN.

| Dir | Scheme | NS | PL / PM / PS |
| :--- | :--- | :--- | :--- |
| [`s0/`](s0/README.md) | S0 static | `exp4-s0` | ✗ / ✗ / ✗ |
| [`s1/`](s1/README.md) | S1 +PL | `exp4-s1` | ✓ / ✗ / ✗ |
| [`s2/`](s2/README.md) | S2 +PL+PM | `exp4-s2` | ✓ / ✓ / ✗ |
| [`s3/`](s3/README.md) | S3 +PL+PM+PS | `exp4-s3` | ✓ / ✓ / ✓ |
| [`sx/`](sx/README.md) | SX isolated \(T\) | `exp4-sx` | S1 sites; one UE at a time |

```bash
# End-to-end in tmux session ``exp4`` (attaches; Ctrl-b d to detach)
python3 paper/exp4/run_experiment.py
python3 paper/exp4/run_experiment.py --schemes s0 s1 s2 s3 --duration 300 --settle 30
python3 paper/exp4/run_experiment.py --schemes s0 s1 s2 s3 --undeploy
python3 paper/exp4/run_experiment.py --undeploy          # tear down all exp4-* and exit
tmux attach -t exp4
python3 paper/exp4/run_experiment.py --no-tmux   # current terminal

# Manual (one scheme at a time):
python3 paper/exp4/s0/deploy.py          # first: static baseline
./scripts/check-configsync.sh
python3 paper/exp4/s0/deploy_ue.py

# later, after undeploy:
python3 paper/exp4/s1/deploy.py
python3 paper/exp4/s2/deploy.py
python3 paper/exp4/s3/deploy.py
```

`run_experiment.py` starts in tmux session ``exp4`` and attaches. It undeploys
a previous live scheme (or leftover application/UE pods) before the next one,
waits until RAN/apps/UEs/PDU are up, holds `--settle` seconds of healthy DL
traffic, then captures (`exp_start --no-plot`) and plots (`exp_plot`). The
last scheme stays running. Pass `--schemes … --undeploy` to tear the last one
down after capture. `--undeploy` with no `--schemes` (or `--undeploy-only`)
only removes live `exp4-*` namespaces and exits. Empty schemes are skipped.
After S0–S3 it runs `compare_schemes.py`. Default push is Gitea-only (Config Sync).

Do not run an `exp4-s*` scheme while `exp1-*` or another `exp4-s*` is live.

---

## 9. How to run (analytical preview)

```bash
# Analytical ablation (shared diurnal + fade trace)
python3 paper/exp4/simulate_exp4.py

# Publication figures → paper/exp4/plots/
python3 paper/exp4/plot_exp4.py
```

The simulator is **preview only**. Paper numbers come from the four
testbed CSVs. Do not mix sim and cluster points in one plot.

Grafana (all five slices, scheme dropdown): [`dashboard/`](dashboard/) → [ina-exp4-apps](http://10.1.137.105:3000/d/ina-exp4-apps/exp4-all-applications?orgId=1&refresh=5s).

```bash
python3 paper/exp4/dashboard/generate.py --push
```

```
paper/exp4/
├── README.md
├── dashboard/                # Grafana: all five DL apps (scheme filter)
├── common/                   # shared GitOps renderer + deploy CLI
├── s0/                       # S0 static (C/C/E hairpin)
├── s1/                       # S1 +PL
├── s2/                       # S2 +PL +PM
├── s3/                       # S3 +PL +PM +PS
├── simulate_exp4.py          # S0–S3 ablation on the INA PL/PM/PS model
├── exp_start.py              # timed live run → <scheme>/data/ + SLA violation
├── plot_exp4.py              # waterfall + contribution + Pareto
├── data/                     # simulation CSVs (committed)
│   ├── exp4_scheme_metrics.csv
│   ├── exp4_waterfall.csv
│   ├── exp4_per_slice.csv
│   └── exp4_timeseries.csv
├── s0/data/ … s3/data/       # live captures (gitignored)└── plots/
    ├── fig4a_sla_waterfall.png
    ├── fig4b_opex_waterfall.png
    ├── fig4c_throughput_efficiency.png
    ├── fig4d_layer_contribution.png
    ├── fig4e_sla_opex_pareto.png
    └── fig4_journal_summary.png
```

---

## 10. Reviewer paragraph (use in the paper)

PL decides **where** functions sit, PM decides **how much** compute is on,
and PS decides **how** radio resources track the channel. Five downlink
slices (best-effort SFTP, YOLO bbox, video watch, CPU file offload, MQTT
Get) run under four schemes. The waterfall shows that each increment
removes a *different* failure mode — core transport (PL), compute queues
(PM), and deep fades (PS) — and that only S3 reaches the joint SLA–OPEX
point. Dropping any layer re-opens that layer’s residual in the waterfall.
