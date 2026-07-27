# INA-Infra slice SLAs (4 slices)

Delay budgets are **RTT / ping** (ms). Planning-layer E2E model:

`delay ≈ d_rf + d_f1[CU] + d_n3[CU,UPF] + d_n6[UPF,APP]`

with default `d_rf = 20` (UE→DU). F1/N3 hop RTT: adjacent 20, Edge↔Central 40.
N6 cross-site is slightly higher (25/35/50) so UPF and APP prefer to co-locate.

| Slice Name | Workload | Deployment | Traffic Profile & Characteristics | SLA Requirements (SLOs) | UE Allocation |
|---|---|---|---|---|---|
| **CCTV** | RTSP camera + YOLO analytics | CU @ Edge; UPF+APP @ Regional | Continuous UL H.264 RTSP | T̄ ≥ 10 Mbps; D̄ ≤ 150 ms (RTT) | UE-1 / slice 1 |
| **Physical AI** | Closed-loop control / on-path inference | All @ Edge | Periodic UL/DL control traffic | T̄ ≥ 20 Mbps; D̄ ≤ 20 ms (RTT); hard isolation (`h_s=1`) | UE-2 / slice 2 |
| **OTT** | Best-effort streaming / bulk transfer | CU @ Regional; UPF+APP @ Central | **iperf3** UL/DL | T̄ ≥ 40 Mbps; D̄ ≤ 50 ms (RTT) | UE-3 / slice 3 |
| **IoT** | MQTT telemetry + actuation | All @ Central | Small-packet MQTT pub/sub | T̄ ≥ 5 Mbps; D̄ ≤ 150 ms (RTT) | UE-4 / slice 4 |

## PL inputs (`t_bar` / `d_bar` / `h_s` / `η₀`)

| id | slice_type | t_bar (Mbps) | d_bar (ms RTT) | h_s | eta_t0 |
|---:|---|---:|---:|---:|---:|
| 1 | CCTV | 10 | 150 | 0 | 2.0 |
| 2 | Physical AI | 20 | 20 | 1 | 2.0 |
| 3 | OTT | 40 | 50 | 0 | 2.5 |
| 4 | IoT | 5 | 150 | 0 | 1.5 |

## Default substrate (capacity + latency)

| Site | NF CPU | APP CPU | Role in demo placement |
|---|---:|---:|---|
| Edge | 55 | 41 | Physical AI (all) + CCTV CU |
| Regional | 52 | 25 | CCTV UPF+APP + OTT CU |
| Central | 61 | 90 | OTT UPF+APP + IoT (all) |

Typical E2E under defaults: CCTV ≈ 44 ms, Physical AI ≈ 26 ms, OTT ≈ 62 ms, IoT ≈ 64 ms.
