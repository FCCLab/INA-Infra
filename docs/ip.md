# IP addressing

Two subnets split **operator/mgmt** from **cluster/data** traffic:

| Subnet | NIC | Purpose |
|--------|-----|---------|
| **10.1.132.0/24** | `enp1s0` | SSH, default route, DNS, mgmt cluster, mgmt MetalLB VIPs |
| **10.1.137.0/24** | `enp7s0` | Kubernetes API, node traffic, Flannel |
| **10.1.138.0/24** | `enp7s0` | MetalLB LoadBalancer VIPs (same L2 as `.137`) |

Definitions: [scripts/cluster_lib.sh](scripts/cluster_lib.sh). Netplan: [scripts/setup_ip.sh](scripts/setup_ip.sh). Topology: [bringup/00_testbed/readme.md](../bringup/00_testbed/readme.md).

**MetalLB pools** (deploy via `./scripts/render_metallb_gitops.sh`; uninstall imperative install with [scripts/uninstall_metallb.sh](scripts/uninstall_metallb.sh))

| Cluster type | Pool name | Range | Interface |
|--------------|-----------|-------|-----------|
| mgmt | `mgmt-pool` | `10.1.132.10`–`10.1.132.99` | `enp1s0` |
| central | `site-pool` | `10.1.138.100`–`10.1.138.124` | `enp7s0` |
| regional | `site-pool` | `10.1.138.125`–`10.1.138.149` | `enp7s0` |
| edge | `site-pool` | `10.1.138.150`–`10.1.138.174` | `enp7s0` |
| ue | `site-pool` | `10.1.138.175`–`10.1.138.199` | `enp7s0` |

Cross-cluster publish range: **`10.1.138.100`–`10.1.138.199`** (25 IPs per cluster on shared site L2). Per slice: **1st** reserved, **2nd** = OpenSpeedTest. On **central** only: **3rd** = OAI AMF N2, **4th** = OAI UPF N3 (OAI operators/core run on central). **Dashboard**, **NRF**, and **UDR** are **cluster-local** (NodePort / ClusterIP).

DHCP leases on mgmt start at `.100` ([services/.env](services/.env)). Pi-hole static DNS: [services/etc-dnsmasq.d/99-nephio-static.conf](services/etc-dnsmasq.d/99-nephio-static.conf).

## Mgmt MetalLB VIPs (`10.1.132.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.132.30 | mgmt | Docker Registry | 5000 | [http://10.1.132.30:5000](http://10.1.132.30:5000) | `registry.nephio.lab` |
| 10.1.132.11 | mgmt | OpenSpeedTest | 80 | [http://10.1.132.11](http://10.1.132.11) | `openspeedtest-mgmt.nephio.lab` |
| 10.1.132.200 | mgmt | Gitea | 80, 3000 | [http://10.1.132.200:3000](http://10.1.132.200:3000) · [http://10.1.132.200](http://10.1.132.200) | `gitea.nephio.lab` |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 | [http://10.1.132.52](http://10.1.132.52) | `webui.nephio.lab` |

## Workload MetalLB VIPs (`10.1.138.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.138.101 | central | OpenSpeedTest | 80 | [http://10.1.138.101](http://10.1.138.101) | `openspeedtest-central.nephio.lab` |
| 10.1.138.102 | central | OAI AMF N2 | 38412/sctp | — | `amf-n2-central.nephio.lab` |
| 10.1.138.103 | central | OAI UPF N3 | 2152/udp | — | `upf-n3-central.nephio.lab` |
| 10.1.137.61 | central | Open5GS 5GC | 9999, 38412/sctp, 2152/udp | — | `open5gs.nephio.lab` |
| 10.1.138.126 | regional | OpenSpeedTest | 80 | [http://10.1.138.126](http://10.1.138.126) | `openspeedtest-regional.nephio.lab` |
| 10.1.138.151 | edge | OpenSpeedTest | 80 | [http://10.1.138.151](http://10.1.138.151) | `openspeedtest-edge.nephio.lab` |
| 10.1.138.176 | ue | OpenSpeedTest | 80 | [http://10.1.138.176](http://10.1.138.176) | `openspeedtest-ue.nephio.lab` |

OAI 5GC (operators + NFs) is deployed on **central** only; regional/edge/ue have OpenSpeedTest MetalLB VIPs but no OAI CN operators.

**Cluster-local (no MetalLB):** Kubernetes Dashboard (GitOps **NodePort 30443** on control-plane mgmt IP), OAI **NRF** / **UDR** (operator `SVC_TYPE=ClusterIP` → `oai-nrf` / `oai-udr` in `oai-cn`). Other NFs use `*.oai-cn.svc.cluster.local`.

Dashboard access from the operator network (`10.1.132.0/24`):

| Cluster | Dashboard URL (NodePort or port-forward) |
|---------|------------------------------------------|
| mgmt | [https://10.1.132.200:30443](https://10.1.132.200:30443) · forward [https://10.1.132.200:8443](https://10.1.132.200:8443) |
| central | [https://10.1.132.210:30443](https://10.1.132.210:30443) · forward [https://10.1.132.210:8443](https://10.1.132.210:8443) |
| regional | [https://10.1.132.220:30443](https://10.1.132.220:30443) · forward [https://10.1.132.220:8443](https://10.1.132.220:8443) |
| edge | [https://10.1.132.230:30443](https://10.1.132.230:30443) · forward [https://10.1.132.230:8443](https://10.1.132.230:8443) |
| ue | [https://10.1.132.240:30443](https://10.1.132.240:30443) · forward [https://10.1.132.240:8443](https://10.1.132.240:8443) |

```bash
./scripts/kubectl_forward.sh              # all clusters (background)
./scripts/kubectl_forward.sh central      # one cluster (foreground)
./scripts/get_dashboard_key.sh            # login tokens
```

Dashboard is deployed via GitOps ([scripts/render_dashboard_gitops.sh](scripts/render_dashboard_gitops.sh)); remove legacy Helm/LB installs with [scripts/uninstall_dashboard.sh](scripts/uninstall_dashboard.sh).

Dashboard login token: [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh).

## Node IPs

| Cluster | Host | SSH (`enp1s0`) | Site / K8s (`enp7s0`, `.137`) | MetalLB host (`enp7s0`, `.138`) | API (`:6443`) |
|---------|------|----------------|--------------------------------|----------------------------------|---------------|
| mgmt | `mgmt-0` | 10.1.132.200 | — | — | [https://10.1.132.200:6443](https://10.1.132.200:6443) |
| mgmt | `mgmt-1` | 10.1.132.201 | — | — | — |
| central | `central-0` | 10.1.132.210 | 10.1.137.110 | 10.1.138.110 | [https://10.1.137.110:6443](https://10.1.137.110:6443) |
| central | `central-1` | 10.1.132.211 | 10.1.137.111 | 10.1.138.111 | — |
| regional | `regional-0` | 10.1.132.220 | 10.1.137.120 | 10.1.138.120 | [https://10.1.137.120:6443](https://10.1.137.120:6443) |
| regional | `regional-1` | 10.1.132.221 | 10.1.137.121 | 10.1.138.121 | — |
| edge | `edge-0` | 10.1.132.230 | 10.1.137.130 | 10.1.138.130 | [https://10.1.137.130:6443](https://10.1.137.130:6443) |
| edge | `edge-1` | 10.1.132.231 | 10.1.137.131 | 10.1.138.131 | — |
| ue | `ue-0` | 10.1.132.240 | 10.1.137.140 | 10.1.138.140 | [https://10.1.137.140:6443](https://10.1.137.140:6443) |
| ue | `ue-1` | 10.1.132.241 | 10.1.137.141 | 10.1.138.141 | — |

SSH aliases: [utils/ssh_config/config](utils/ssh_config/config) (mgmt `.132` addresses). Default route: `via 10.1.132.1`; DNS: `10.1.132.200`.

Reach workload APIs on `10.1.137.0/24`, MetalLB VIPs on `10.1.138.0/24`, from the operator network via routing, or use dashboard port-forward on `10.1.132.x` ([scripts/kubectl_forward.sh](scripts/kubectl_forward.sh)).

## Install scripts

| Component | Script |
|-----------|--------|
| Cluster bootstrap (workload) | [scripts/bringup_cluster.sh](scripts/bringup_cluster.sh) (`--join` for worker only) |
| Cluster bootstrap (mgmt, 132 only) | [scripts/bringup_mgmt_cluster.sh](scripts/bringup_mgmt_cluster.sh) (alias for `bringup_cluster.sh mgmt`) |
| MetalLB (remove imperative install) | [scripts/uninstall_metallb.sh](scripts/uninstall_metallb.sh) |
| MetalLB (GitOps render) | [scripts/render_metallb_gitops.sh](scripts/render_metallb_gitops.sh) |
| Flannel CNI (GitOps render) | [scripts/render_flannel_gitops.sh](scripts/render_flannel_gitops.sh) |
| Flannel CNI (remove imperative install) | [scripts/uninstall_flannel.sh](scripts/uninstall_flannel.sh) |
| Multus CNI (GitOps render) | [scripts/render_multus_gitops.sh](scripts/render_multus_gitops.sh) |
| Kubernetes Dashboard (GitOps render) | [scripts/render_dashboard_gitops.sh](scripts/render_dashboard_gitops.sh) |
| Kubernetes Dashboard (remove imperative install) | [scripts/uninstall_dashboard.sh](scripts/uninstall_dashboard.sh) |
| OAI CN operators (GitOps render, central only) | [scripts/render_oai_operators_gitops.sh](scripts/render_oai_operators_gitops.sh) |
| OAI core NFs (GitOps render, central only) | [scripts/render_oai_core_gitops.sh](scripts/render_oai_core_gitops.sh) |
| local-path StorageClass (GitOps render) | [scripts/render_local_path_gitops.sh](scripts/render_local_path_gitops.sh) |
| Push GitOps to Gitea | [bringup/03_push_to_git_repos/push_git_repos.sh](bringup/03_push_to_git_repos/push_git_repos.sh) |
| Config Sync status | [scripts/check-configsync.sh](scripts/check-configsync.sh) |
| Dashboard on mgmt (.132) | [scripts/kubectl_forward.sh](scripts/kubectl_forward.sh) |
| Gitea repos (Config Sync) | [bringup/02_configsync/configsync.sh](bringup/02_configsync/configsync.sh) |
| Nephio WorkloadCluster registration | [configsync/setup_api_of_clusters.sh](configsync/setup_api_of_clusters.sh) |
| OpenSpeedTest | [scripts/install_open_speed_test.sh](scripts/install_open_speed_test.sh) |
| Dashboard bearer token | [scripts/get_dashboard_key.sh](scripts/get_dashboard_key.sh) |
| Site / mgmt netplan | [scripts/setup_ip.sh](scripts/setup_ip.sh) |
| Reset clusters | [scripts/reset_clusters.sh](scripts/reset_clusters.sh) |
| Passwordless sudo | [scripts/set_passwordless.sh](scripts/set_passwordless.sh) |
| Docker registry (push/list) | [scripts/push-image-to-registry.sh](scripts/push-image-to-registry.sh) · [scripts/list-registry-images.sh](scripts/list-registry-images.sh) |
