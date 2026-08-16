# Documentation

## Platform (Kubernetes / Nephio testbed)

| Doc | Topic |
|-----|--------|
| [testbed.md](testbed.md) | Topology, sites, interconnect |
| [topology.md](topology.md) | Live lab layout (bridges, vm-sw, nodes) |
| [ip_plan.md](ip_plan.md) | Subnets, octet allocation, MetalLB VIPs, node IPs |
| [influxdb-grafana.md](influxdb-grafana.md) | Edge InfluxDB + Grafana (`.104`/`.105`) |
| [ddns.md](ddns.md) | Central DynDNS ([docker-ddns-server](https://github.com/benjaminbear/docker-ddns-server)) |
| [config_sync.md](config_sync.md) | GitOps (Gitea + Config Sync) |
| [mgmt.md](mgmt.md) | Mgmt bridge / `10.1.132.0/24` |
| [gitea.md](gitea.md) | Gitea on mgmt |
| [new_cluster.md](new_cluster.md) | Legacy central kubeadm notes |

## Applications

| Doc | Topic |
|-----|--------|
| [cctv.md](cctv.md) | Slice-1 CCTV: MediaMTX pub/sub, FastAPI Swagger, dashboard, GitOps vs UE clients |

## OAI / network slicing

| Doc | Topic |
|-----|--------|
| [oai.md](oai.md) | OAI macvlan IP plan, split RAN + slice UPFs |
| [oai-deployment.md](oai-deployment.md) | Split RAN deployment notes |
| [oai-benchmark.md](oai-benchmark.md) | `oai-benchmark` stack, RF config, CU-UP/PRB throughput sweeps |

Nephio kpt packages: [`bringup/nephio/`](../bringup/nephio/).
