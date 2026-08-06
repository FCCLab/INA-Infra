# iperf3 on UPF N6 + InfluxDB

Python wrappers around `iperf3` for the oai-benchmark lab.

## Server (UPF sidecar)

- Binds to UPF **N3** Multus address (`n3` iface)
- Listens on **126 ports** (`5201`–`5326` by default)
- One-off per connection (`iperf3 -1`) + idle timeout (30s)
- Writes aggregate `server_agg` to InfluxDB every 1s

## Client (UE)

5 processes, DL UDP (`-u -R`), **50M** each → N3 server ports `5201`–`5205`.

Influx reports **aggregate** DL only (`client_agg` + `server_agg`), not per-stream.

```bash
# Publish N3 → UE (or discover below)
./scripts/sync_iperf3_n6_server_ip.sh

N3=$(kubectl --context edge@edge -n oai-benchmark exec deploy/upf-benchmark \
  -c upf-benchmark -- ip -4 -o addr show n3 | awk '{print $4}' | cut -d/ -f1)

kubectl --context edge@edge -n oai-benchmark exec -it deploy/oai-ue -c iperf3-client -- \
  python3 client.py --server "$N3" --processes 5 --bandwidth 50M --bind-dev oaitun_ue1
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
./scripts/build_iperf3_n6_image.sh
./scripts/render_oai_benchmark_gitops.sh
./bringup/03_push_to_git_repos/push_git_repos.sh edge
```
