# IP addressing

Two subnets split **operator/mgmt** from **cluster/data** traffic:

| Subnet | NIC | Purpose |
|--------|-----|---------|
| **10.1.132.0/24** | `enp1s0` | SSH, default route, DNS, mgmt cluster, mgmt MetalLB VIPs |
| **10.1.137.0/24** | `enp7s0` | Kubernetes API, node traffic, Flannel |
| **10.1.138.0/24** | `enp7s0` | MetalLB LoadBalancer VIPs (same L2 as `.137`) |
| **10.1.139.0/24** | `enp7s0` | OAI macvlan only (Multus NADs; no host IP) — see [oai.md](../../docs/oai.md) |
| **10.1.140.0/24** | `enp7s0` | INA-Infra profile Multus (default profile `ina-infra`; host=`base[role]+n`) — see [ina-infra/README.md](../../ina-infra/README.md) |

Definitions: [scripts/cluster_lib.sh](../../scripts/cluster_lib.sh). Netplan: [scripts/setup_ip.sh](../../scripts/setup_ip.sh). Topology: [bringup/00_testbed/readme.md](../../bringup/00_testbed/readme.md).

**Hypervisor VLAN uplinks** (`eno1` trunk) join clusters to external L2:

| VLAN | Interface | Bridge | Cluster |
|------|-----------|--------|---------|
| 132 | `eno1.132` | `br-mgmt` | all (mgmt `enp1s0`) |
| 135 | `eno1.135` | `br-int-central` | central (`enp7s0`) |
| 136 | `eno1.136` | `br-int-regional` | regional |
| 137 | `eno1.137` | `br-int-edge` | edge |
| — | — | `br-int-ue` | ue (no external VLAN) |

Bringup: [`bringup/00_testbed/bringup_switches.sh`](../../bringup/00_testbed/bringup_switches.sh) · [`scripts/setup_eno1_vlan_uplinks.sh`](../../scripts/setup_eno1_vlan_uplinks.sh)

**MetalLB pools** (deploy via `./scripts/render_metallb_gitops.sh`; uninstall imperative install with [scripts/uninstall_metallb.sh](../../scripts/uninstall_metallb.sh))

| Cluster type | Pool name | Range | Interface |
|--------------|-----------|-------|-----------|
| mgmt | `mgmt-pool` | `10.1.132.10`–`10.1.132.99` | `enp1s0` |
| central | `site-pool` | `10.1.138.100`–`10.1.138.124` | `enp7s0` |
| regional | `site-pool` | `10.1.138.125`–`10.1.138.149` | `enp7s0` |
| edge | `site-pool` | `10.1.138.150`–`10.1.138.174` | `enp7s0` |
| ue | `site-pool` | `10.1.138.175`–`10.1.138.199` | `enp7s0` |

Cross-cluster publish range: **`10.1.138.100`–`10.1.138.199`** (25 IPs per cluster on shared site L2). Per slice: **1st** reserved, **2nd** = OpenSpeedTest. **OAI macvlan** uses **`10.1.139.0/24`** ([oai.md](../../docs/oai.md)). **Dashboard**, **NRF**, and **UDR** are **cluster-local** (NodePort / ClusterIP).

DHCP leases on mgmt start at `.100` ([services/.env](../../services/.env)). Pi-hole static DNS: [services/etc-dnsmasq.d/99-nephio-static.conf](../../services/etc-dnsmasq.d/99-nephio-static.conf).

## Mgmt MetalLB VIPs (`10.1.132.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.132.30 | mgmt | Docker Registry | 5000 | [https://10.1.132.30:5000](https://10.1.132.30:5000) | `registry.nephio.lab` |
| 10.1.132.11 | mgmt | OpenSpeedTest | 80 | [http://10.1.132.11](http://10.1.132.11) | `openspeedtest-mgmt.nephio.lab` |
| 10.1.132.200 | mgmt | Gitea | 80, 3000 | [http://10.1.132.200:3000](http://10.1.132.200:3000) · [http://10.1.132.200](http://10.1.132.200) | `gitea.nephio.lab` |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 | [http://10.1.132.52](http://10.1.132.52) | `webui.nephio.lab` |
| 10.1.132.230 | edge | xApp Swagger (externalIP) | 18080 | [http://10.1.132.230:18080/docs](http://10.1.132.230:18080/docs) | — |

## Workload MetalLB VIPs (`10.1.138.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.138.101 | central | OpenSpeedTest | 80 | [http://10.1.138.101](http://10.1.138.101) | `openspeedtest-central.nephio.lab` |
| 10.1.138.126 | regional | OpenSpeedTest | 80 | [http://10.1.138.126](http://10.1.138.126) | `openspeedtest-regional.nephio.lab` |
| 10.1.138.151 | edge | OpenSpeedTest | 80 | [http://10.1.138.151](http://10.1.138.151) | `openspeedtest-edge.nephio.lab` |
| 10.1.138.176 | ue | OpenSpeedTest | 80 | [http://10.1.138.176](http://10.1.138.176) | `openspeedtest-ue.nephio.lab` |

OAI 5GC CP on **central**; **co-located UPF + CU-UP** per slice (1→central, 2→regional, 3–5→edge); **CU-CP + DU + 5 nrUEs** on **edge `usrp`** — namespace `oai-slice-deployment`, macvlan **`10.1.139.0/24`** ([oai.md](../../docs/oai.md)). Render: `./scripts/render_oai_slice_deployment_gitops.sh`.

## Cluster-local services (no MetalLB VIP)

These are **not** on `10.1.138.0/24`. Dashboard uses **NodePort 30443** on the control-plane **mgmt** IP (`10.1.132.x`, SSH subnet). OAI NRF/UDR are ClusterIP inside `oai-cn`.

| Access | Cluster | Service | Ports | URL | DNS |
|--------|---------|---------|-------|-----|-----|
| NodePort | mgmt | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.200:30443](https://10.1.132.200:30443) | `dashboard-mgmt.nephio.lab:30443` |
| NodePort | central | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.210:30443](https://10.1.132.210:30443) | — |
| NodePort | regional | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.220:30443](https://10.1.132.220:30443) | — |
| NodePort | edge | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.230:30443](https://10.1.132.230:30443) | — |
| NodePort | ue | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.240:30443](https://10.1.132.240:30443) | — |
| ClusterIP | central | OAI NRF / UDR | 80/tcp (SBI) | `oai-nrf.oai-cn.svc` / `oai-udr.oai-cn.svc` | — |

Port-forward fallback (`:8443` on the same mgmt IP): [scripts/kubectl_forward.sh](../../scripts/kubectl_forward.sh). Login token: [scripts/get_dashboard_key.sh](../../scripts/get_dashboard_key.sh). GitOps: [scripts/render_dashboard_gitops.sh](../../scripts/render_dashboard_gitops.sh).

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

Dashboard is deployed via GitOps ([scripts/render_dashboard_gitops.sh](../../scripts/render_dashboard_gitops.sh)); remove legacy Helm/LB installs with [scripts/uninstall_dashboard.sh](../../scripts/uninstall_dashboard.sh).

Dashboard login token: [scripts/get_dashboard_key.sh](../../scripts/get_dashboard_key.sh).

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

**Physical edge workers** (bare metal; site NIC ≠ `enp7s0`; SSH not on `.132`):

| Cluster | Host | SSH | Site NIC | Site / K8s (`.137`) | Netplan |
|---------|------|-----|----------|---------------------|---------|
| edge | `edge-2` | 10.1.101.18 | `eno1` | 10.1.137.132 | [workloads/netplan/edge-2/55-k8s.yaml](../../workloads/netplan/edge-2/55-k8s.yaml) |
| edge | `edge-3` | 172.27.2.22 | `ens12f0` | 10.1.137.133 | [workloads/netplan/edge-3/55-k8s.yaml](../../workloads/netplan/edge-3/55-k8s.yaml) |
| edge | `usrp` | 10.1.101.19 | `enp4s0f0` | 10.1.137.134 | [workloads/netplan/usrp/55-k8s.yaml](../../workloads/netplan/usrp/55-k8s.yaml) |

`usrp` site IP is **`10.1.137.134`** (not `10.1.137.13` — that is `br-int-ue`). Macvlan for OAI still uses `enp4s0f0` on `10.1.139.0/24` ([oai.md](../../docs/oai.md)).

SSH aliases: [utils/ssh_config/config](../../utils/ssh_config/config) (mgmt `.132` addresses; physical hosts use their SSH IPs above). Default route (VMs): `via 10.1.132.1`; DNS: `10.1.132.200`.

Reach workload APIs on `10.1.137.0/24`, MetalLB VIPs on `10.1.138.0/24`, from the operator network via routing, or use dashboard port-forward on `10.1.132.x` ([scripts/kubectl_forward.sh](../../scripts/kubectl_forward.sh)).

## Install scripts

| Component | Script |
|-----------|--------|
| Cluster bootstrap (workload) | [scripts/bringup_cluster.sh](../../scripts/bringup_cluster.sh) (`--join` for worker only) |
| Cluster bootstrap (mgmt, 132 only) | [scripts/bringup_mgmt_cluster.sh](../../scripts/bringup_mgmt_cluster.sh) (alias for `bringup_cluster.sh mgmt`) |
| MetalLB (remove imperative install) | [scripts/uninstall_metallb.sh](../../scripts/uninstall_metallb.sh) |
| MetalLB (GitOps render) | [scripts/render_metallb_gitops.sh](../../scripts/render_metallb_gitops.sh) |
| Flannel CNI (GitOps render) | [scripts/render_flannel_gitops.sh](../../scripts/render_flannel_gitops.sh) |
| Flannel CNI (remove imperative install) | [scripts/uninstall_flannel.sh](../../scripts/uninstall_flannel.sh) |
| Multus CNI (GitOps render) | [scripts/render_multus_gitops.sh](../../scripts/render_multus_gitops.sh) |
| Kubernetes Dashboard (GitOps render) | [scripts/render_dashboard_gitops.sh](../../scripts/render_dashboard_gitops.sh) |
| Kubernetes Dashboard (remove imperative install) | [scripts/uninstall_dashboard.sh](../../scripts/uninstall_dashboard.sh) |
| OAI CN operators (GitOps, central) | [scripts/render_oai_operators_gitops.sh](../../scripts/render_oai_operators_gitops.sh) |
| OAI core NFs (GitOps, central) | [scripts/render_oai_core_gitops.sh](../../scripts/render_oai_core_gitops.sh) |
| OAI RAN CU-CP (GitOps, regional) | [scripts/render_oai_ran_gitops.sh](../../scripts/render_oai_ran_gitops.sh) |
| OAI RAN DU + rfsim RU (GitOps, edge) | [scripts/render_oai_ran_du_gitops.sh](../../scripts/render_oai_ran_du_gitops.sh) |
| OAI RAN CU-UP (GitOps, edge) | [scripts/render_oai_ran_cuup_gitops.sh](../../scripts/render_oai_ran_cuup_gitops.sh) |
| OAI 5-slice split RAN + UPFs | [scripts/render_oai_slice_deployment_gitops.sh](../../scripts/render_oai_slice_deployment_gitops.sh) |
| local-path StorageClass (GitOps render) | [scripts/render_local_path_gitops.sh](../../scripts/render_local_path_gitops.sh) |
| Push GitOps to Gitea | [bringup/03_push_to_git_repos/push_git_repos.sh](../../bringup/03_push_to_git_repos/push_git_repos.sh) |
| Config Sync status | [scripts/check-configsync.sh](../../scripts/check-configsync.sh) |
| Dashboard on mgmt (.132) | [scripts/kubectl_forward.sh](../../scripts/kubectl_forward.sh) |
| Gitea repos (Config Sync) | [bringup/02_configsync/configsync.sh](../../bringup/02_configsync/configsync.sh) |
| Nephio WorkloadCluster registration | [configsync/setup_api_of_clusters.sh](../../configsync/setup_api_of_clusters.sh) |
| OpenSpeedTest | [scripts/install_open_speed_test.sh](../../scripts/install_open_speed_test.sh) |
| Dashboard bearer token | [scripts/get_dashboard_key.sh](../../scripts/get_dashboard_key.sh) |
| Site / mgmt netplan (VMs) | [scripts/setup_ip.sh](../../scripts/setup_ip.sh) |
| Physical worker netplan + SSH | [workloads/wl_setup_ssh_mgmt_ip.sh](../../workloads/wl_setup_ssh_mgmt_ip.sh) · [workloads/netplan/](../../workloads/netplan/) |
| Reset clusters | [scripts/reset_clusters.sh](../../scripts/reset_clusters.sh) |
| Passwordless sudo | [scripts/set_passwordless.sh](../../scripts/set_passwordless.sh) |
| Docker registry (push/list) | [scripts/push-image-to-registry.sh](../../scripts/push-image-to-registry.sh) · [scripts/list-registry-images.sh](../../scripts/list-registry-images.sh) |
