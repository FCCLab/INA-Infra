# IP addressing (10.1.132.0/24)

MetalLB LoadBalancer pool **`10.1.132.10`–`10.1.132.99`**: defined in [scripts/cluster_lib.sh](scripts/cluster_lib.sh), applied with [scripts/install_ip_pool.sh](scripts/install_ip_pool.sh). DHCP leases start at `.100` ([services/.env](services/.env)).

Related docs: [testbed/readme.md](testbed/readme.md) (topology), [services/etc-dnsmasq.d/99-nephio-static.conf](services/etc-dnsmasq.d/99-nephio-static.conf) (Pi-hole static DNS on mgmt).

## MetalLB VIPs

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.132.30 | mgmt | Docker Registry | 5000 | [http://10.1.132.30:5000](http://10.1.132.30:5000) | `registry.nephio.lab` |
| 10.1.132.40 | mgmt | Kubernetes Dashboard | 443 | [https://10.1.132.40](https://10.1.132.40) | `dashboard-mgmt.nephio.lab` |
| 10.1.132.41 | central | Kubernetes Dashboard | 443 | [https://10.1.132.41](https://10.1.132.41) | `dashboard-central.nephio.lab` |
| 10.1.132.42 | regional | Kubernetes Dashboard | 443 | [https://10.1.132.42](https://10.1.132.42) | — |
| 10.1.132.43 | edge | Kubernetes Dashboard | 443 | [https://10.1.132.43](https://10.1.132.43) | — |
| 10.1.132.44 | ue | Kubernetes Dashboard | 443 | [https://10.1.132.44](https://10.1.132.44) | — |
| 10.1.132.50 | mgmt | OpenSpeedTest | 80 | [http://10.1.132.50](http://10.1.132.50) | `openspeedtest-mgmt.nephio.lab` |
| 10.1.132.51 | mgmt | Gitea | 22, 80, 3000 | [http://10.1.132.51:3000](http://10.1.132.51:3000) · [http://10.1.132.51](http://10.1.132.51) | `gitea.nephio.lab` |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 | [http://10.1.132.52](http://10.1.132.52) | `webui.nephio.lab` |
| 10.1.132.60 | central | OpenSpeedTest | 80 | [http://10.1.132.60](http://10.1.132.60) | `openspeedtest-central.nephio.lab` |
| 10.1.132.61 | central | Open5GS 5GC | 9999, 38412/sctp, 2152/udp | — | `open5gs.nephio.lab` |
| 10.1.132.70 | regional | OpenSpeedTest | 80 | [http://10.1.132.70](http://10.1.132.70) | — |
| 10.1.132.80 | edge | OpenSpeedTest | 80 | [http://10.1.132.80](http://10.1.132.80) | — |
| 10.1.132.90 | ue | OpenSpeedTest | 80 | [http://10.1.132.90](http://10.1.132.90) | — |

Dashboard login token: [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh).

## Node IPs (SSH / Kubernetes API)

| Cluster | Control plane | Worker | API (`:6443`) |
|---------|---------------|--------|---------------|
| mgmt | [10.1.132.200](https://10.1.132.200:6443) (`mgmt-0`) | 10.1.132.201 (`mgmt-1`) | [https://10.1.132.200:6443](https://10.1.132.200:6443) |
| central | [10.1.132.210](https://10.1.132.210:6443) (`central-0`) | 10.1.132.211 (`central-1`) | [https://10.1.132.210:6443](https://10.1.132.210:6443) |
| regional | [10.1.132.220](https://10.1.132.220:6443) (`regional-0`) | 10.1.132.221 (`regional-1`) | [https://10.1.132.220:6443](https://10.1.132.220:6443) |
| edge | [10.1.132.230](https://10.1.132.230:6443) (`edge-0`) | 10.1.132.231 (`edge-1`) | [https://10.1.132.230:6443](https://10.1.132.230:6443) |
| ue | [10.1.132.240](https://10.1.132.240:6443) (`ue-0`) | 10.1.132.241 (`ue-1`) | [https://10.1.132.240:6443](https://10.1.132.240:6443) |

SSH aliases: [utils/ssh_config/config](utils/ssh_config/config). Default route on all nodes: `via 10.1.132.1`; DNS: `10.1.132.200` (Pi-hole on mgmt).

## Install scripts

| Component | Script |
|-----------|--------|
| Cluster bootstrap | [scripts/bringup_cluster.sh](scripts/bringup_cluster.sh) |
| MetalLB IP pool | [scripts/install_ip_pool.sh](scripts/install_ip_pool.sh) |
| Kubernetes Dashboard | [scripts/install_dashboard.sh](scripts/install_dashboard.sh) |
| OpenSpeedTest | [scripts/install_open_speed_test.sh](scripts/install_open_speed_test.sh) |
| Dashboard bearer token | [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh) |
| Site / mgmt netplan | [scripts/setup_ip.sh](scripts/setup_ip.sh) |
| Docker registry (push/list) | [scripts/push-image-to-registry.sh](scripts/push-image-to-registry.sh) · [scripts/list-registry-images.sh](scripts/list-registry-images.sh) |
