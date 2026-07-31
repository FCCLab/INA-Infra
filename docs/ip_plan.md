# IP plan

Authoritative addressing for the Nephio testbed: subnets, octet allocation, node IPs, MetalLB VIPs, and services. Topology: [topology.md](topology.md). Script constants: [scripts/cluster_lib.sh](../scripts/cluster_lib.sh), [bringup/00_testbed/bringup_switches.sh](../bringup/00_testbed/bringup_switches.sh). Netplan: [scripts/setup_ip.sh](../scripts/setup_ip.sh) (VMs), [workloads/netplan/](../workloads/netplan/) (bare metal).

Two planes split **operator/mgmt** from **cluster/data** traffic.

## Subnets

| Subnet | NIC | Purpose |
|--------|-----|---------|
| `10.1.132.0/24` | `enp1s0` | SSH, default route, DNS, mgmt cluster, mgmt MetalLB VIPs |
| `10.1.137.0/24` | site NIC | Kubernetes API, node traffic, Flannel, hypervisor bridges |
| `10.1.138.0/24` | `enp7s0` (VMs) | MetalLB LoadBalancer VIPs (same L2 as `.137`) |
| `10.1.139.0/24` | macvlan | OAI Multus only — no host IP ([oai.md](oai.md)) |
| `10.1.140.0/24` | macvlan | INA-Infra profile Multus (`ina-infra`; host = `base[role]+n`) |
| `10.1.101.0/24` | WAN / external | Bootstrap SSH for bare-metal workers |

Pi-hole on `mgmt-0` is **DNS-only** (`10.1.132.200`); static records: [services/etc-dnsmasq.d/99-nephio-static.conf](../services/etc-dnsmasq.d/99-nephio-static.conf). Site DHCP is **Glass / ISC** on **central** Kubernetes (`hostNetwork` on `central-0` `enp7s0`), pool `10.1.137.160–199`, gateway `10.1.137.1` — used by ina-infra **UPF N6** (Multus macvlan + dhclient). Build/push: [services/glass-dhcp/build_push.sh](../services/glass-dhcp/build_push.sh), UI `http://10.1.132.210:3000`.

## VLAN uplinks (`eno1` trunk)

| VLAN | Interface | Bridge | Cluster |
|------|-----------|--------|---------|
| 132 | `eno1.132` | `br-mgmt` | all (mgmt `enp1s0`) |
| 135 | `eno1.135` | `br-int-central` | central (`enp7s0`) |
| 136 | `eno1.136` | `br-int-regional` | regional |
| 137 | `eno1.137` | `br-int-edge` | edge |
| — | — | `br-int-ue` | ue (no external VLAN) |

Bringup: [bringup/00_testbed/bringup_switches.sh](../bringup/00_testbed/bringup_switches.sh) · [scripts/setup_eno1_vlan_uplinks.sh](../scripts/setup_eno1_vlan_uplinks.sh)

## `10.1.137.0/24` allocation

Single site L2 stretched across clusters via `vm-sw-*` switches. Do **not** assign K8s node IPs in the bridge ranges.

| Range / address | Owner | Notes |
|-----------------|-------|-------|
| `.1` | Site internet gateway | Upstream L3 on `10.1.137.0/24` (outside lab IP plan hosts) |
| `.10` | `br-int-central` | Hypervisor bridge (`BR_INT_CENTRAL_IP`) |
| `.11` | `br-int-regional` | Hypervisor bridge |
| `.12` | `br-int-edge` | Hypervisor bridge |
| `.13` | `br-int-ue` | Hypervisor bridge (not `usrp`) |
| `.20` | `br-ext-cr` | Site-chain bridge (central ↔ regional) |
| `.21` | `br-ext-re` | Site-chain bridge (regional ↔ edge) |
| `.22` | `br-ext-eu` | Site-chain bridge (edge ↔ ue) |
| `.110`–`.111` | `central-0`, `central-1` | K8s VMs |
| `.120`–`.121` | `regional-0`, `regional-1` | K8s VMs |
| `.130`–`.131` | `edge-0`, `edge-1` | K8s VMs |
| `.132`–`.134` | `edge-2`, `edge-3`, `usrp` | Physical edge workers |
| `.140`–`.141` | *(retired ue cluster)* | formerly `ue-0`/`ue-1` |
| **`.150`–`.159`** | **GPU bare-metal workers** | `GPU_WORKER_IP_FIRST` … `LAST` |
| `.150` | — | Spare |
| `.151` | `gh81` | edge cluster, GH200 arm64 |
| `.152` | `gh82` | central cluster, GH200 arm64 |
| `.153`–`.159` | — | Spare |
| **`.160`–`.199`** | **Site DHCP pool** | Glass/ISC on `central-0`; ina-infra UPF N6 dhclient |

## `10.1.132.0/24` allocation (mgmt)

| Range / address | Owner | Notes |
|-----------------|-------|-------|
| `.1` | Gateway | Default route for VMs |
| `.10` | `br-mgmt` | Hypervisor bridge |
| `.10`–`.99` | MetalLB `mgmt-pool` | Registry, OpenSpeedTest, etc. |
| `.100`–`.199` | *(formerly Pi-hole DHCP)* | unused — DHCP moved to site `.137` |
| `.200` | `mgmt-0` | Gitea, Pi-hole DNS |
| `.201` | `mgmt-1` | mgmt worker |
| `.210`–`.211` | `central-0`, `central-1` | |
| `.220`–`.221` | `regional-0`, `regional-1` | |
| `.230`–`.231` | `edge-0`, `edge-1` | |
| `.240`–`.241` | *(retired ue cluster)* | |

Bare-metal **SSH is mgmt/WAN only** (`.101.x` or off-LAN addresses below). Site `.137` is kubelet/node-ip only — do not use for SSH.

| Host | Mgmt SSH |
|------|----------|
| `edge-2` | `10.1.101.18` |
| `edge-3` | `172.27.2.22` |
| `usrp` | `10.1.101.19` |
| `gh81` | `10.1.101.211` |
| `gh82` | `10.1.101.212` |

## MetalLB pools

Deploy via `./scripts/render_metallb_gitops.sh`; remove imperative installs with [scripts/uninstall_metallb.sh](../scripts/uninstall_metallb.sh).

| Cluster | Pool name | Range | Interface |
|---------|-----------|-------|-----------|
| mgmt | `mgmt-pool` | `10.1.132.10`–`10.1.132.99` | `enp1s0` |
| central | `site-pool` | `10.1.138.100`–`10.1.138.124` | `enp7s0` |
| regional | `site-pool` | `10.1.138.125`–`10.1.138.149` | `enp7s0` |
| edge | `site-pool` | `10.1.138.150`–`10.1.138.199` | `enp7s0` |

Cross-cluster publish range: **`10.1.138.100`–`10.1.138.199`**. Per slice: 1st reserved, 2nd = OpenSpeedTest. VMs also hold a host `.138` address matching their `.137` node IP (e.g. `central-0` → `.138.110`).

### Mgmt MetalLB VIPs (`10.1.132.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.132.30 | mgmt | Docker Registry | 5000 | [https://10.1.132.30:5000](https://10.1.132.30:5000) | `registry.nephio.lab` |
| 10.1.132.11 | mgmt | OpenSpeedTest | 80 | [http://10.1.132.11](http://10.1.132.11) | `openspeedtest-mgmt.nephio.lab` |
| 10.1.132.200 | mgmt | Gitea | 80, 3000 | [http://10.1.132.200:3000](http://10.1.132.200:3000) | `gitea.nephio.lab` |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 | [http://10.1.132.52](http://10.1.132.52) | `webui.nephio.lab` |
| 10.1.132.230 | edge | xApp Swagger (externalIP) | 18080 | [http://10.1.132.230:18080/docs](http://10.1.132.230:18080/docs) | — |

### Workload MetalLB VIPs (`10.1.138.0/24`)

| VIP | Cluster | Service | Ports | URL | DNS |
|-----|---------|---------|-------|-----|-----|
| 10.1.138.101 | central | OpenSpeedTest | 80 | [http://10.1.138.101](http://10.1.138.101) | `openspeedtest-central.nephio.lab` |
| 10.1.138.126 | regional | OpenSpeedTest | 80 | [http://10.1.138.126](http://10.1.138.126) | `openspeedtest-regional.nephio.lab` |
| 10.1.138.151 | edge | OpenSpeedTest | 80 | [http://10.1.138.151](http://10.1.138.151) | `openspeedtest-edge.nephio.lab` |

OAI 5GC CP on **central**; co-located **UPF + CU-UP** per slice (1→central, 2→regional, 3–5→edge); **CU-CP + DU + 5 nrUEs** on **edge `usrp`** — namespace `oai-slice-deployment`, macvlan **`10.1.139.0/24`** ([oai.md](oai.md)). Render: `./scripts/render_oai_slice_deployment_gitops.sh`.

## Cluster-local services (no MetalLB VIP)

Dashboard uses **NodePort 30443** on the control-plane **mgmt** IP (`10.1.132.x`). OAI NRF/UDR are ClusterIP inside `oai-cn`.

| Access | Cluster | Service | Ports | URL | DNS |
|--------|---------|---------|-------|-----|-----|
| NodePort | mgmt | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.200:30443](https://10.1.132.200:30443) | `dashboard-mgmt.nephio.lab:30443` |
| NodePort | central | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.210:30443](https://10.1.132.210:30443) | — |
| NodePort | regional | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.220:30443](https://10.1.132.220:30443) | — |
| NodePort | edge | Kubernetes Dashboard | 30443/tcp | [https://10.1.132.230:30443](https://10.1.132.230:30443) | — |
| ClusterIP | central | OAI NRF / UDR | 80/tcp (SBI) | `oai-nrf.oai-cn.svc` / `oai-udr.oai-cn.svc` | — |

Port-forward fallback (`:8443` on mgmt IP): [scripts/kubectl_forward.sh](../scripts/kubectl_forward.sh). Login token: [scripts/get_dashboard_key.sh](../scripts/get_dashboard_key.sh). GitOps: [scripts/render_dashboard_gitops.sh](../scripts/render_dashboard_gitops.sh).

```bash
./scripts/kubectl_forward.sh              # all clusters (background)
./scripts/kubectl_forward.sh central      # one cluster (foreground)
./scripts/get_dashboard_key.sh            # login tokens
```

## Node IPs

### Kubernetes VMs

| Cluster | Host | SSH (`enp1s0`) | Site K8s (`.137`) | MetalLB host (`.138`) | API (`:6443`) |
|---------|------|----------------|-------------------|------------------------|---------------|
| mgmt | `mgmt-0` | 10.1.132.200 | — | — | [https://10.1.132.200:6443](https://10.1.132.200:6443) |
| mgmt | `mgmt-1` | 10.1.132.201 | — | — | — |
| central | `central-0` | 10.1.132.210 | 10.1.137.110 | 10.1.138.110 | [https://10.1.137.110:6443](https://10.1.137.110:6443) |
| central | `central-1` | 10.1.132.211 | 10.1.137.111 | 10.1.138.111 | — |
| regional | `regional-0` | 10.1.132.220 | 10.1.137.120 | 10.1.138.120 | [https://10.1.137.120:6443](https://10.1.137.120:6443) |
| regional | `regional-1` | 10.1.132.221 | 10.1.137.121 | 10.1.138.121 | — |
| edge | `edge-0` | 10.1.132.230 | 10.1.137.130 | 10.1.138.130 | [https://10.1.137.130:6443](https://10.1.137.130:6443) |
| edge | `edge-1` | 10.1.132.231 | 10.1.137.131 | 10.1.138.131 | — |

### Physical / external workers

Bootstrap: [workloads/wl_setup_ssh_mgmt_ip.sh](../workloads/wl_setup_ssh_mgmt_ip.sh). Join: [workloads/wl_bringup_k8s_up.sh](../workloads/wl_bringup_k8s_up.sh).

**SSH: mgmt/WAN only.** Site `.137` is for Kubernetes (`kubelet --node-ip`) only.

| Cluster | Host | SSH (mgmt) | Mgmt NIC | Site NIC | Site K8s (`.137`) | Netplan |
|---------|------|------------|----------|----------|-------------------|---------|
| edge | `edge-2` | 10.1.101.18 | — | `eno1` | 10.1.137.132 | [55-k8s.yaml](../workloads/netplan/edge-2/55-k8s.yaml) |
| edge | `edge-3` | 172.27.2.22 | — | `ens12f0` | 10.1.137.133 | [55-k8s.yaml](../workloads/netplan/edge-3/55-k8s.yaml) |
| edge | `usrp` | 10.1.101.19 | — | `enp4s0f0` | 10.1.137.134 | [55-k8s.yaml](../workloads/netplan/usrp/55-k8s.yaml) |
| central | `gh82` | 10.1.101.212 | `enP2s2f0np0` | `enP2s2f1np1` | 10.1.137.152 | [55-k8s.yaml](../workloads/netplan/gh82/55-k8s.yaml) |
| edge | `gh81` | 10.1.101.211 | `aerial02` | `aerial03` | 10.1.137.151 | [55-k8s.yaml](../workloads/netplan/gh81/55-k8s.yaml) |

`usrp` site IP **`10.1.137.134`** is not hypervisor bridge **`10.1.137.13`** (`br-int-ue`). GPU workers use **`.150`–`.159`**, not bridge **`.10`–`.22`**.

## SSH

Aliases: [utils/ssh_config/config](../utils/ssh_config/config). VMs use mgmt `10.1.132.x`; bare metal uses mgmt/WAN only (see table above — never site `.137`).

```bash
ssh -F utils/ssh_config/config central-0   # VM mgmt
ssh -F utils/ssh_config/config gh82        # bare-metal mgmt 10.1.101.212
ssh -F utils/ssh_config/config gh81        # bare-metal mgmt 10.1.101.211
```

VM default route: `via 10.1.132.1` · DNS: `10.1.132.200`. Reach workload APIs on `10.1.137.0/24` and MetalLB VIPs on `10.1.138.0/24` from the operator network via routing, or use dashboard port-forward on `10.1.132.x`.

## Install scripts

| Component | Script |
|-----------|--------|
| Cluster bootstrap (workload) | [scripts/bringup_cluster.sh](../scripts/bringup_cluster.sh) (`--join` for worker only) |
| Cluster bootstrap (mgmt) | [scripts/bringup_mgmt_cluster.sh](../scripts/bringup_mgmt_cluster.sh) |
| MetalLB (GitOps render) | [scripts/render_metallb_gitops.sh](../scripts/render_metallb_gitops.sh) |
| Flannel / Multus / Dashboard (GitOps) | [scripts/render_flannel_gitops.sh](../scripts/render_flannel_gitops.sh) · [render_multus_gitops.sh](../scripts/render_multus_gitops.sh) · [render_dashboard_gitops.sh](../scripts/render_dashboard_gitops.sh) |
| OAI (GitOps) | [render_oai_operators_gitops.sh](../scripts/render_oai_operators_gitops.sh) · [render_oai_core_gitops.sh](../scripts/render_oai_core_gitops.sh) · [render_oai_slice_deployment_gitops.sh](../scripts/render_oai_slice_deployment_gitops.sh) |
| Push GitOps to Gitea | [bringup/03_push_to_git_repos/push_git_repos.sh](../bringup/03_push_to_git_repos/push_git_repos.sh) |
| Config Sync status | [scripts/check-configsync.sh](../scripts/check-configsync.sh) |
| Site / mgmt netplan (VMs) | [scripts/setup_ip.sh](../scripts/setup_ip.sh) |
| Physical worker netplan + SSH | [workloads/wl_setup_ssh_mgmt_ip.sh](../workloads/wl_setup_ssh_mgmt_ip.sh) |
