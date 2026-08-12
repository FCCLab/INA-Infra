# OAI benchmark (`oai-benchmark`)

Dedicated non-slice stack for **throughput / CU-UP CPU** and **PRB** measurements.
Render: [`scripts/render_oai_benchmark_gitops.sh`](../scripts/render_oai_benchmark_gitops.sh).
UI: ina-infra **Benchmark** tab. Results helpers: [`results/cuup-benchmark/`](../results/cuup-benchmark/), [`results/prb-benchmark/`](../results/prb-benchmark/).

## Topology

| Role | Cluster / node | Notes |
|------|----------------|--------|
| 5GC CP (AMF/SMF/…) | **central** `oai-benchmark` | Executor manifests (not slice `oai-cn`) |
| UPF + iperf3 server | **edge** | N3/N4/N6 macvlan; N6 iperf `10.1.139.35` |
| CU-CP, CU-UP, FlexRIC, RAN operator | **edge** workers | Live CPU via Operators agent `edge-oai-benchmark` |
| DU + nrUE + iperf3 client | **edge `usrp`** | RF on `enp4s0f0`; PDU `oaitun_ue1` |

Traffic path for estimation: **UE PDU → UPF N6 iperf3** (client binds `--bind-dev oaitun_ue1`).

## 5G radio config (throughput bound)

Live values from `oai-du-configmap` / `oai-ue-configmap` in `oai-benchmark` (USRP RF, not RFsim).

| Parameter | Value |
|-----------|--------|
| Band | **n78** |
| Channel | **40 MHz** (`dl/ul_carrierBandwidth = 133` PRB) |
| SCS | **30 kHz** (`subcarrierSpacing = 1`) |
| SSB / Point A | `620640` / `620112` |
| Antennas | **1T1R** (`nb_tx` / `nb_rx = 1`) |
| TDD | period **ms5** (`dl_UL_TransmissionPeriodicity = 6`); **7 DL / 2 UL** slots (+ 6 DL / 6 UL symbols in special slots) |
| BWP | full (`initial*BWPlocationAndBandwidth = 36300`) |
| RF | `local_rf = yes`; `att_tx/rx = 0`; `max_rxgain = 75` |

### Slice / PDU

| Field | Value |
|-------|--------|
| SST / SD | `1` / `0x000001` |
| DNN | `internet` |
| UE IMSI | `001010000000100` |
| PDU pool (logical) | `10.1.0.0/24` (e.g. UE `10.1.0.2` on `oaitun_ue1`) |

### What this implies for peak rate

- Air interface is **single-layer**, **~40 MHz**, TDD **~7:2 DL:UL**.
- Rough bound: DL on the order of a **few hundred Mbps**; UL much lower.
- Observed CU-UP sweeps (see `results/cuup-benchmark/plots/`): DL UDP plateaus near **~220 Mbps** client / **~250 Mbps** offered; UL saturates earlier (tens of Mbps at the UPF under a fixed offer).

Re-check after render:

```bash
kubectl --context edge@edge -n oai-benchmark get cm oai-du-configmap -o jsonpath='{.data.gnb\.conf}' \
  | grep -E 'carrierBandwidth|subcarrierSpacing|frequencyBand|nrofDownlinkSlots|nrofUplinkSlots|nb_tx|nb_rx'
```

## NF CPU (default / sweep)

| NF | Default req=lim | Sweep |
|----|-----------------|--------|
| DU | `2` | fixed |
| CU-CP | `1` | fixed |
| CU-UP | `1` (varies) | Benchmark UI: **20m → 600m** (request=limit, Operators agent) |
| iperf3-client (UE) | `20m` / lim `500m` | — |

## Traffic load (iperf)

| Item | Default / typical |
|------|-------------------|
| Server | UPF N6 `10.1.139.35` (`iperf3-n6-endpoint`) |
| Streams | `-P 5` |
| UDP offer | pod env `BANDWIDTH=50M`; UI/WS desired often **25M** (UL) or higher for DL saturate |
| Direction | UL = no `-R`; DL = `-R` (reverse) |
| Protocol | `udp` or `tcp` via WS desired (no pod restart) |
| Measure window | ~**120 s** per step (`timestamps_*.csv` Start/Stop) |
| Control plane | UE → `INA_INFRA_API_URL` (`ws://…/api/v1/ues/ws`); CU-UP CPU → Operators WS |

## Results pipeline

```text
timestamps_<tag>.csv  →  ./data_download.py  →  data_<tag>/step_*.npz
                      →  ./data_plot.py      →  plots/throughput_vs_*.png
```

- Tags: `dl_udp`, `dl_tcp`, `ul_udp` (CU-UP); PRB runs under `results/prb-benchmark/`.
- `data_download.py` **skips existing** `step_*.npz` / `summary.npz` unless `--force`.
- Influx: edge `http://10.1.137.104:8086`, measurement `iperf3`, roles `client_agg` / `server_agg`.

## Related

- GitOps / operator: [third_party/ran_operator/docs/operations.md](../third_party/ran_operator/docs/operations.md)
- Operators agent: [third_party/ran_operator/docs/operator-agent.md](../third_party/ran_operator/docs/operator-agent.md)
- Macvlan IP plan: [oai.md](oai.md), [ip_plan.md](ip_plan.md)
- iperf helpers: [tools/iperf3-n6/README.md](../tools/iperf3-n6/README.md)
