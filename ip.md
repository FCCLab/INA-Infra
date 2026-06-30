# IP addressing

Two subnets split **operator/mgmt** from **cluster/data** traffic:

| Subnet | NIC | Purpose |
|--------|-----|---------|
| **10.1.132.0/24** | `enp1s0` | SSH, default route, DNS, mgmt cluster, mgmt MetalLB VIPs |
| **10.1.137.0/24** | `enp7s0` | Kubernetes API, node↔node, workload MetalLB VIPs, inter-site L2 |

Definitions: [scripts/cluster_lib.sh](scripts/cluster_lib.sh). Netplan: [scripts/setup_ip.sh](scripts/setup_ip.sh). Topology: [testbed/readme.md](testbed/readme.md).

**MetalLB pools**

| Cluster type | Pool | Script |
|--------------|------|--------|
| mgmt | `10.1.132.10`–`10.1.132.99` | [scripts/install_ip_pool.sh](scripts/install_ip_pool.sh) |
| workload (central, regional, edge, ue) | `10.1.137.40`–`10.1.137.99` | [scripts/install_ip_pool.sh](scripts/install_ip_pool.sh) |

DHCP leases on mgmt start at `.100` ([services/.env](services/.env)). Pi-hole static DNS: [services/etc-dnsmasq.d/99-nephio-static.conf](services/etc-dnsmasq.d/99-nephio-static.conf).

## Mgmt MetalLB VIPs (`10.1.132.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.132.30 | mgmt | Docker Registry | 5000 | [http://10.1.132.30:5000](http://10.1.132.30:5000) | `registry.nephio.lab` |
| 10.1.132.40 | mgmt | Kubernetes Dashboard | 443 | [https://10.1.132.40](https://10.1.132.40) | `dashboard-mgmt.nephio.lab` |
| 10.1.132.50 | mgmt | OpenSpeedTest | 80 | [http://10.1.132.50](http://10.1.132.50) | `openspeedtest-mgmt.nephio.lab` |
| 10.1.132.51 | mgmt | Gitea | 22, 80, 3000 | [http://10.1.132.51:3000](http://10.1.132.51:3000) · [http://10.1.132.51](http://10.1.132.51) | `gitea.nephio.lab` |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 | [http://10.1.132.52](http://10.1.132.52) | `webui.nephio.lab` |

## Workload MetalLB VIPs (`10.1.137.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.137.41 | central | Kubernetes Dashboard | 443 | [https://10.1.137.41](https://10.1.137.41) | `dashboard-central.nephio.lab` |
| 10.1.137.42 | regional | Kubernetes Dashboard | 443 | [https://10.1.137.42](https://10.1.137.42) | `dashboard-regional.nephio.lab` |
| 10.1.137.43 | edge | Kubernetes Dashboard | 443 | [https://10.1.137.43](https://10.1.137.43) | `dashboard-edge.nephio.lab` |
| 10.1.137.44 | ue | Kubernetes Dashboard | 443 | [https://10.1.137.44](https://10.1.137.44) | `dashboard-ue.nephio.lab` |
| 10.1.137.60 | central | OpenSpeedTest | 80 | [http://10.1.137.60](http://10.1.137.60) | `openspeedtest-central.nephio.lab` |
| 10.1.137.61 | central | Open5GS 5GC | 9999, 38412/sctp, 2152/udp | — | `open5gs.nephio.lab` |
| 10.1.137.70 | regional | OpenSpeedTest | 80 | [http://10.1.137.70](http://10.1.137.70) | `openspeedtest-regional.nephio.lab` |
| 10.1.137.80 | edge | OpenSpeedTest | 80 | [http://10.1.137.80](http://10.1.137.80) | `openspeedtest-edge.nephio.lab` |
| 10.1.137.90 | ue | OpenSpeedTest | 80 | [http://10.1.137.90](http://10.1.137.90) | `openspeedtest-ue.nephio.lab` |

Dashboard login token: [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh).

## Node IPs

| Cluster | Host | SSH (`enp1s0`) | Site / K8s (`enp7s0`) | API (`:6443`) |
|---------|------|----------------|------------------------|---------------|
| mgmt | `mgmt-0` | 10.1.132.200 | — | [https://10.1.132.200:6443](https://10.1.132.200:6443) |
| mgmt | `mgmt-1` | 10.1.132.201 | — | — |
| central | `central-0` | 10.1.132.210 | 10.1.137.110 | [https://10.1.137.110:6443](https://10.1.137.110:6443) |
| central | `central-1` | 10.1.132.211 | 10.1.137.111 | — |
| regional | `regional-0` | 10.1.132.220 | 10.1.137.120 | [https://10.1.137.120:6443](https://10.1.137.120:6443) |
| regional | `regional-1` | 10.1.132.221 | 10.1.137.121 | — |
| edge | `edge-0` | 10.1.132.230 | 10.1.137.130 | [https://10.1.137.130:6443](https://10.1.137.130:6443) |
| edge | `edge-1` | 10.1.132.231 | 10.1.137.131 | — |
| ue | `ue-0` | 10.1.132.240 | 10.1.137.140 | [https://10.1.137.140:6443](https://10.1.137.140:6443) |
| ue | `ue-1` | 10.1.132.241 | 10.1.137.141 | — |

SSH aliases: [utils/ssh_config/config](utils/ssh_config/config) (mgmt `.132` addresses). Default route: `via 10.1.132.1`; DNS: `10.1.132.200`.

Reach workload APIs and VIPs from your laptop via routing to `10.1.137.0/24`, or SSH port-forward through a node.

## Install scripts

| Component | Script |
|-----------|--------|
| Cluster bootstrap (workload) | [scripts/bringup_cluster.sh](scripts/bringup_cluster.sh) (`--join` for worker only) |
| Cluster bootstrap (mgmt, 132 only) | [scripts/bringup_mgmt_cluster.sh](scripts/bringup_mgmt_cluster.sh) (alias for `bringup_cluster.sh mgmt`) |
| MetalLB IP pool | [scripts/install_ip_pool.sh](scripts/install_ip_pool.sh) |
| Kubernetes Dashboard | [scripts/install_dashboard.sh](scripts/install_dashboard.sh) |
| OpenSpeedTest | [scripts/install_open_speed_test.sh](scripts/install_open_speed_test.sh) |
| Dashboard bearer token | [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh) |
| Site / mgmt netplan | [scripts/setup_ip.sh](scripts/setup_ip.sh) |
| Reset clusters | [scripts/reset_clusters.sh](scripts/reset_clusters.sh) |
| Passwordless sudo | [scripts/set_passwordless.sh](scripts/set_passwordless.sh) |
| Docker registry (push/list) | [scripts/push-image-to-registry.sh](scripts/push-image-to-registry.sh) · [scripts/list-registry-images.sh](scripts/list-registry-images.sh) |
