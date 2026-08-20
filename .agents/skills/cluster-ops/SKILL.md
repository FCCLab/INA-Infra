---
name: cluster-ops
description: >-
  Multi-cluster Kubernetes operations, node layout, contexts (mgmt, central, regional, edge),
  networking (Multus N6, MetalLB, Flannel, 5G Core, OAI-RAN), and diagnostics.
---

# Multi-Cluster Operations Skill

Comprehensive guide for operating and troubleshooting the 4-cluster Nephio testbed in `INA-Infra`.

---

## 1. Cluster Topography & Contexts

| Cluster Name | K8s Context | Control Plane IP | Primary Roles |
| :--- | :--- | :--- | :--- |
| **`mgmt`** | `mgmt@mgmt` | `10.1.132.200` | Management, Gitea (`:3000`), Dashboard (`:30574`), INA Backend (`:8000`), Local Docker Registry (`:5000`) |
| **`central`** | `central@central` | `10.1.100.1` | 5G Core (AMF, SMF, UDR, UDM, AUSF, NRF), UPF Slice 3 & 4, OTT Server, IoT Server |
| **`regional`** | `regional@regional` | `10.1.100.2` | UPF Slice 1, CU-UP 1 & 2, CCTV Server |
| **`edge`** | `edge@edge` | `10.1.100.3` | OAI DU, CU-CP, CU-UP, FlexRIC xApps, OAI UEs (Slices 1-4), SMO / Non-RT-RIC, Physical-AI |

---

## 2. Fast Diagnostic Commands

```bash
# Check overall node health across all 4 clusters:
kubectl --context=mgmt@mgmt get nodes -o wide
kubectl --context=central@central get nodes -o wide
kubectl --context=regional@regional get nodes -o wide
kubectl --context=edge@edge get nodes -o wide

# Check ina-infra workload pods:
kubectl --context=edge@edge get pods -n ina-infra -o wide
kubectl --context=central@central get pods -n ina-infra -o wide
kubectl --context=regional@regional get pods -n ina-infra -o wide

# Check OAI 5G Core registration and PDU session state:
kubectl --context=central@central logs -n ina-infra -l app.kubernetes.io/name=amf-core -c amf --tail=30
kubectl --context=central@central logs -n ina-infra -l app.kubernetes.io/name=smf-core -c smf --tail=30

# Check OAI RAN / DU status:
kubectl --context=edge@edge logs -n ina-infra -l app.kubernetes.io/name=oai-du -c du --tail=30
```

---

## 3. Network Architecture & Routing

- **OAM Network**: `10.1.132.0/24` (Management & cluster interconnects).
- **Core / RAN Subnets**: `10.1.100.0/24` (Kubernetes control planes), `10.1.104.0/24` (N2/N3 interfaces).
- **N6 Data Network (Multus)**: `10.1.137.0/24` (MetalLB VIPs & application server endpoints).
- **UE PDU Subnets**:
  - Slice 1: `10.140.1.0/24`
  - Slice 2: `10.140.2.0/24`
  - Slice 3: `10.140.3.0/24`
  - Slice 4: `10.140.4.0/24`
