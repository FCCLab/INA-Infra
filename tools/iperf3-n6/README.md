# iperf3 on UPF N6 + InfluxDB

Python wrappers around `iperf3` for the oai-benchmark lab.

## Server (UPF sidecar)

- Binds to UPF **N3** Multus address (`n3` iface)
- Listens on **126 ports** (`5201`–`5326` by default)
- One-off per connection (`iperf3 -1`) + idle/`--rcv-timeout` (30s) and a
  post-accept watchdog that kills silent ESTAB control sockets (common after UE path loss)
- Writes aggregate `server_agg` to InfluxDB every 1s

## Client (UE)

One iperf3 process: DL (`-R`), **`-P 5`** parallel streams on a **single**
N3 port (`PORT_START`, default `5210`).

**Control plane:** connects to ina-infra WebSocket
`ws://10.1.132.200:8082/api/v1/ues/ws` (`INA_INFRA_API_URL`).
Benchmark UI lists UEs; **desired config is per UE** (`id` required).
Changes hot-restart iperf only (no pod restart).

`desired` fields: `protocol`, `action` (`start|stop|set`), `bandwidth`,
`parallel`, `tcp_bandwidth`, `server`, `port`, `reverse` (DL `-R`),
`duration`, `interval`, `generation`.

Initial env (`PROTOCOL`, `BANDWIDTH`, …) is the fallback before the first
`desired`. TCP is unlimited unless `tcp_bandwidth` / `TCP_BANDWIDTH` is set.

Influx reports **aggregate** DL only (`client_agg` + `server_agg`).

```bash
./scripts/sync_iperf3_n6_server_ip.sh

# Per-UE config (id from GET /ues)
curl -sS http://10.1.132.200:8082/api/v1/ues
curl -sS -X POST http://10.1.132.200:8082/api/v1/ues/desired \
  -H 'Content-Type: application/json' \
  -d '{"id":"edge-oai-benchmark-oai-ue-…","protocol":"udp","action":"start","bandwidth":"80M","parallel":4}'
```

## InfluxDB

| | |
|---|---|
| URL | `http://10.1.137.104:8086` |
| measurement | `iperf3` |
| tags | `role=client_agg\|server_agg` (throughput), `role=server` (heartbeat) |
| fields | `bits_per_second`, `mbits_per_second`, `streams` (agg); `up`, `port_count` (heartbeat) |

## Build

```bash
IPERF3_N6_TAG=ws-ctrl ./scripts/build_iperf3_n6_image.sh
./scripts/render_oai_benchmark_gitops.sh
./bringup/03_push_to_git_repos/push_git_repos.sh edge
```
