# OAI topology

OpenAirInterface 5G deployment across four workload clusters: **central** (5GC + UPF), **regional** (CU-CP), **edge** (DU + rfsim RU + CU-UP), **ue** (nrUE RFsim client). All manifests are GitOps via Config Sync ([ip.md](ip.md) for addressing).

## OAI macvlan IP plan (`10.1.139.0/24`)

Untagged **macvlan** on `enp7s0` (Multus NADs). Pods-only by default; for UPF N6 → mgmt (`10.1.132.0/24`, OpenSpeedTest), run [`scripts/setup_oai_n6_gw.sh`](../scripts/setup_oai_n6_gw.sh) on **central** so the host owns gateway **`10.1.139.1`** (macvlan shim + NAT). `10.1.138.0/24` stays for MetalLB / OpenSpeedTest VIPs only.

**Range `10.1.139.10` – `10.1.139.209`:** four workload clusters × **50 IPs** each (`mgmt` is not on this network). Each cluster assigns macvlan IPs from **`base + 0`** upward. **Deployed today:** 5GC on **central**, 1 CU-CP on **regional**, 1 CU-UP + 1 DU on **edge**, 1 nrUE on **ue**.

| Cluster | Base | Range | Deployed |
|---------|------|-------|----------|
| central | `.10` | `10.1.139.10` – `10.1.139.59` | 1× AMF, 1× SMF, 1× UPF |
| regional | `.60` | `10.1.139.60` – `10.1.139.109` | **1× CU-CP** |
| edge | `.110` | `10.1.139.110` – `10.1.139.159` | **1× CU-UP + 1× DU** |
| ue | `.160` | `10.1.139.160` – `10.1.139.209` | **1× nrUE** |

### Deployed RAN (3 NFs + UE)

| NF | Cluster | IPs (macvlan on `10.1.139.0/24`) |
|----|---------|-------------------------------------|
| **CU-CP** | regional | N2 `10.1.139.60` · F1-C `10.1.139.61` · E1 `10.1.139.62` |
| **CU-UP** | edge | E1 `10.1.139.110` · F1-U `10.1.139.111` · N3 `10.1.139.112` |
| **DU** | edge | F1 `10.1.139.113` |
| **nrUE** | ue | RF `10.1.139.160` (RFsim client → edge DU `:4043`) |

### Deployed 5GC (central)

One **AMF**, one **SMF**, one **UPF** (`upf-core` — three macvlan interfaces).

| NF | Offset | Interface | IP |
|----|--------|-----------|-----|
| AMF | `+0` | N2 | `10.1.139.10` |
| UPF (`upf-core`) | `+1` | N3 | `10.1.139.11` |
| SMF | `+2` | N4 | `10.1.139.12` |
| UPF (`upf-core`) | `+3` | N4 | `10.1.139.13` |
| UPF (`upf-core`) | `+4` | N6 | `10.1.139.14` |

### Cross-cluster peer pairs

```
regional CU-CP N2   10.1.139.60   ──SCTP──►  AMF N2      10.1.139.10   (central)
edge DU F1          10.1.139.113  ──SCTP──►  CU-CP F1-C  10.1.139.61   (regional)
edge CU-UP E1       10.1.139.110  ──SCTP──►  CU-CP E1    10.1.139.62   (regional)
edge CU-UP N3       10.1.139.112  ──GTP-U──►  UPF N3      10.1.139.11   (central)
central SMF N4      10.1.139.12   ──PFCP──►  UPF N4      10.1.139.13   (central)
ue nrUE RFsim       10.1.139.160  ──RFsim──►  DU rfsim    10.1.139.113:4043 (edge)
```

NRF / UDR / SMF SBI stay **ClusterIP** inside `oai-cn` (unchanged). DNN pool behind UPF N6 stays **`10.1.0.0/24`** (UE PDU address, e.g. `10.1.0.2`).

### Slice offsets

**Rule:** `IP = cluster_base + offset`. Offsets always count from **`+0`** within each cluster slice (no gaps, no `+10` block).

**central** (base `10.1.139.10`) — 1× AMF, 1× SMF, 1× UPF (`upf-core`)

| Offset | NF | Interface | IP |
|--------|-----|-----------|-----|
| `+0` | AMF | N2 | `10.1.139.10` |
| `+1` | UPF | N3 | `10.1.139.11` |
| `+2` | SMF | N4 | `10.1.139.12` |
| `+3` | UPF | N4 | `10.1.139.13` |
| `+4` | UPF | N6 | `10.1.139.14` |
| `+5` – `+49` | — | reserved | |

Offsets `+1`, `+3`, `+4` are on the same **`upf-core`** pod (N3 / N4 / N6). SMF N4 at `+2` is a separate pod.

**regional** (base `10.1.139.60`)

| Offset | NF | Interface | IP |
|--------|-----|-----------|-----|
| `+0` | CU-CP | N2 | `10.1.139.60` |
| `+1` | CU-CP | F1-C | `10.1.139.61` |
| `+2` | CU-CP | E1 | `10.1.139.62` |
| `+3` – `+49` | — | reserved | |

**edge** (base `10.1.139.110`)

| Offset | NF | Interface | IP |
|--------|-----|-----------|-----|
| `+0` | CU-UP | E1 | `10.1.139.110` |
| `+1` | CU-UP | F1-U | `10.1.139.111` |
| `+2` | CU-UP | N3 | `10.1.139.112` |
| `+3` | DU | F1 | `10.1.139.113` |
| `+4` – `+49` | — | reserved | |

**ue** (base `10.1.139.160`) — 1× nrUE

| Offset | NF | Interface | IP |
|--------|-----|-----------|-----|
| `+0` | nrUE | RF (macvlan `rf`) | `10.1.139.160` |
| `+1` – `+49` | — | reserved | |

RFsim is **not** SCTP/GTP — the nrUE pod connects to the edge DU rfsim server over site L2 (`10.1.139.113:4043`). No additional macvlan offsets are used on ue today.

When a cluster later runs a full RAN trio (1 CU-CP + 1 CU-UP + 1 DU), continue assigning from `+0` in interface order: CU-CP N2/F1-C/E1 (`+0`–`+2`), CU-UP E1/F1-U/N3 (`+3`–`+5`), DU F1 (`+6`).

## Cluster placement

```mermaid
flowchart TB
  subgraph central["central cluster"]
    subgraph oai_cn["namespace: oai-cn"]
      NRF[NRF]
      AMF[AMF]
      SMF[SMF]
      UDM[UDM]
      UDR[UDR]
      AUSF[AUSF]
      MYSQL[(MySQL)]
    end
    subgraph oai_upf["namespace: oai-upf"]
      UPF[UPF]
    end
    OPS_CN[oai-cn-operators]
  end

  subgraph regional["regional cluster"]
    subgraph oai_cucp["namespace: oai-ran-cucp"]
      CUCP[CU-CP]
    end
    OPS_RAN_R[oai-ran-operators]
  end

  subgraph edge["edge cluster"]
    subgraph oai_du["namespace: oai-ran-du"]
      DU[DU + rfsim RU]
    end
    subgraph oai_cuup["namespace: oai-ran-cuup"]
      CUUP[CU-UP]
    end
    OPS_RAN_E[oai-ran-operators]
  end

  subgraph ue["ue cluster"]
    subgraph oai_ue["namespace: oai-ue"]
      NRUE[nrUE RFsim client]
    end
  end

  OPS_CN -.->|NFDeployment| oai_cn
  OPS_CN -.->|NFDeployment| oai_upf
  OPS_RAN_R -.->|NFDeployment| oai_cucp
  OPS_RAN_E -.->|NFDeployment| oai_du
  OPS_RAN_E -.->|NFDeployment| oai_cuup

  NRUE -->|"RFsim :4043"| DU

  NRF --- SMF
  NRF --- AMF
  NRF --- UDM
  NRF --- UDR
  NRF --- AUSF
  UDR --- MYSQL
```

| Cluster | Components | Namespaces |
|---------|------------|------------|
| **central** | NRF, AMF, SMF, UDM, UDR, AUSF, MySQL, UPF | `oai-cn`, `oai-upf`, `oai-cn-operators` |
| **regional** | CU-CP | `oai-ran-cucp`, `oai-ran-operators` |
| **edge** | DU (rfsim RU), CU-UP | `oai-ran-du`, `oai-ran-cuup`, `oai-ran-operators` |
| **ue** | nrUE (RFsim client) | `oai-ue` |

## RAN split (DU / CU-CP / CU-UP)

```mermaid
flowchart LR
  subgraph ue["ue"]
    NRUE["nrUE<br/>RF 10.1.139.160"]
  end

  subgraph edge["edge"]
    DU["DU + rfsim RU<br/>F1 10.1.139.113"]
    CUUP["CU-UP<br/>E1: 10.1.139.110<br/>F1U: 10.1.139.111<br/>N3: 10.1.139.112"]
  end

  subgraph regional["regional"]
    CUCP["CU-CP<br/>N2: 10.1.139.60<br/>F1-C: 10.1.139.61<br/>E1: 10.1.139.62"]
  end

  subgraph central["central"]
    AMF["AMF<br/>N2 10.1.139.10"]
    UPF["UPF upf-core<br/>N3: 10.1.139.11<br/>N4: 10.1.139.13<br/>N6: 10.1.139.14"]
  end

  NRUE -->|"RFsim :4043"| DU
  DU -->|"F1 SCTP :38472"| CUCP
  CUUP -->|"E1 SCTP :38462"| CUCP
  CUUP -->|"F1U GTP-U :2152"| DU
  CUCP -->|"N2 NGAP SCTP :38412"| AMF
  CUUP -->|"N3 GTP-U :2152"| UPF
```

CU-CP is the control-plane anchor: it terminates **N2** toward the core, **F1-C** toward the DU, and **E1** toward the CU-UP. CU-UP handles the user plane (**F1-U** to DU, **N3** to UPF). The **nrUE** on ue attaches over **RFsim** to the edge DU (no macvlan SCTP/GTP on ue beyond the RF NAD).

## 5GC service-based interfaces (central only)

Internal **ClusterIP** services inside `oai-cn` — no MetalLB VIP:

```mermaid
flowchart LR
  AMF --> NRF
  SMF --> NRF
  UDM --> NRF
  UDR --> NRF
  AUSF --> NRF
  AMF --> SMF
  AMF --> UDM
  AMF --> AUSF
  SMF --> UPF
  UDR --> MYSQL[(MySQL)]
```

| NF | K8s service | Access |
|----|-------------|--------|
| NRF | `oai-nrf.oai-cn.svc` | ClusterIP (SBI HTTP/2) |
| AMF | `oai-amf.oai-cn.svc` | ClusterIP headless (SBI); N2 is macvlan |
| SMF | `oai-smf.oai-cn.svc` | ClusterIP headless |
| UDM | `oai-udm.oai-cn.svc` | ClusterIP headless |
| UDR | `oai-udr.oai-cn.svc` | ClusterIP headless |
| AUSF | `oai-ausf.oai-cn.svc` | ClusterIP headless |

## Interface addressing

All OAI data-plane interfaces use Multus **macvlan** on `enp7s0` in **`10.1.139.0/24`** (see [IP plan](#oai-macvlan-ip-plan-101139024) above). Gateway in NAD config: **`10.1.139.1`**. No host netplan address on `.139`.

| Link | Protocol | Local | Remote | Cluster(s) |
|------|----------|-------|--------|------------|
| **RFsim** (nrUE ↔ DU) | OAI RFsim :4043 | nrUE `10.1.139.160` | DU `10.1.139.113` | ue → edge |
| **N2** (gNB ↔ AMF) | SCTP :38412 | CU-CP `10.1.139.60` | AMF `10.1.139.10` | regional → central |
| **F1-C** (DU ↔ CU-CP) | SCTP :38472 | DU `10.1.139.113` | CU-CP `10.1.139.61` | edge → regional |
| **E1** (CU-UP ↔ CU-CP) | SCTP :38462 | CU-UP `10.1.139.110` | CU-CP `10.1.139.62` | edge → regional |
| **F1-U** (CU-UP ↔ DU) | GTP-U :2152 | CU-UP `10.1.139.111` | DU (via CU-CP) | edge |
| **N3** (CU-UP ↔ UPF) | GTP-U :2152 | CU-UP `10.1.139.112` | UPF `10.1.139.11` | edge → central |
| **N4** (SMF ↔ UPF) | PFCP :8805 | SMF `10.1.139.12` | UPF `10.1.139.13` | central |
| **N6** (UPF ↔ data net) | IP | UPF `10.1.139.14` | DNN pool `10.1.0.0/24` | central |

Cross-cluster traffic uses the site L2 fabric (`enp7s0` → vm-sw), not Kubernetes pod networking. SBI between 5GC NFs stays **ClusterIP** inside `oai-cn`. MetalLB / OpenSpeedTest remain on **`10.1.138.0/24`** only ([ip.md](ip.md)).

## Control vs user plane

```mermaid
flowchart TB
  subgraph CP["Control plane"]
    UE_CP[nrUE] --> RF[RFsim]
    RF --> DU_CP[DU]
    DU_CP --> F1C[F1-C SCTP]
    F1C --> CUCP[CU-CP]
    CUCP --> N2[N2 NGAP SCTP]
    N2 --> AMF[AMF]
    AMF --> SMF[SMF]
    AMF --> UDM[UDM]
  end

  subgraph UP["User plane"]
    UE_UP[nrUE] --> DU_UP[DU]
    DU_UP --> F1U[F1-U GTP-U]
    F1U --> CUUP[CU-UP]
    CUUP --> N3[N3 GTP-U]
    N3 --> UPF[UPF]
    UPF --> N6[N6 / internet DNN]
  end

  CUCP -.->|E1 setup| CUUP
```

## Images

| Component | Image | Cluster |
|-----------|-------|---------|
| CU-CP | `oaisoftwarealliance/oai-gnb:v2.3.0` | regional |
| DU | `oaisoftwarealliance/oai-gnb:v2.3.0` (rfsim RU) | edge |
| CU-UP | `oaisoftwarealliance/oai-nr-cuup:v2.3.0` | edge |
| nrUE | `oaisoftwarealliance/oai-nr-ue:v2.3.0` | ue |
| AMF | `oaisoftwarealliance/oai-amf:v2.0.1` | central |
| RAN operator | `nephio/oai-ran-controller:latest` | regional, edge |
| CN operators | OAI upstream controllers | central |

PLMN: **MCC 001 / MNC 01**, SST **1**, SD **ffffff**, DNN **internet**.

## GitOps render scripts

| Scope | Script |
|-------|--------|
| CN operators (central) | [scripts/render_oai_operators_gitops.sh](../scripts/render_oai_operators_gitops.sh) |
| 5GC NFs + UPF (central) | [scripts/render_oai_core_gitops.sh](../scripts/render_oai_core_gitops.sh) |
| CU-CP (regional) | [scripts/render_oai_ran_gitops.sh](../scripts/render_oai_ran_gitops.sh) |
| DU + rfsim (edge) | [scripts/render_oai_ran_du_gitops.sh](../scripts/render_oai_ran_du_gitops.sh) |
| CU-UP (edge) | [scripts/render_oai_ran_cuup_gitops.sh](../scripts/render_oai_ran_cuup_gitops.sh) |
| UE sim (ue) | [scripts/render_oai_ue_gitops.sh](../scripts/render_oai_ue_gitops.sh) |
| Multus CNI | [scripts/render_multus_gitops.sh](../scripts/render_multus_gitops.sh) |
| PFCP reconcile (central) | [scripts/reconcile_oai_pfcp.sh](../scripts/reconcile_oai_pfcp.sh) |
| Push to Gitea | [bringup/03_push_to_git_repos/push_git_repos.sh](../bringup/03_push_to_git_repos/push_git_repos.sh) |

**Executor pattern:** RAN workloads (CU-CP, DU, CU-UP) use static executor manifests in git (Deployment, ConfigMap, NADs) because Config Sync prunes operator-created pods. Core NFs on central are reconciled by OAI CN operators from `NFDeployment` CRs. The **nrUE** on ue is a static Deployment (no operator).

## UE sim (ue cluster)

OAI **nrUE** in namespace **`oai-ue`** on the **ue** cluster. One macvlan NAD (`ue-sim-rf`, interface `rf`) at **`10.1.139.160`**. RFsim client targets edge DU at **`10.1.139.113:4043`** (same host as DU F1, rfsim server port).

| Item | Value |
|------|-------|
| Namespace | `oai-ue` |
| Deployment | `oai-ue` |
| Macvlan IP | `10.1.139.160` (base `+0`) |
| RFsim target | `10.1.139.113:4043` (edge DU) |
| IMSI | `001010000000100` |
| DNN / slice | `internet` · SST `1` · SD `ffffff` |
| RF params | `-C 3609120000 -r 51 --numerology 1 --ssb 234 --band 78` |
| PDU IPv4 pool | `10.1.0.0/24` (assigned by SMF/UPF, e.g. `10.1.0.2`) |

**Prerequisites:** Multus on ue; edge DU running with rfsim; central 5GC + PFCP association; subscriber `001010000000100` in central MySQL.

```bash
./scripts/render_multus_gitops.sh ue
./scripts/render_oai_ue_gitops.sh ue
./bringup/03_push_to_git_repos/push_git_repos.sh ue

# After central core + edge RAN are up:
./scripts/reconcile_oai_pfcp.sh central-0
ssh ue-0 kubectl rollout restart deployment/oai-ue -n oai-ue
```

## PFCP (SMF ↔ UPF)

PDU sessions require an active PFCP association on N4 (`10.1.139.12` ↔ `10.1.139.13`). On cold start, UPF may register with NRF before SMF subscribes; SMF then logs **No UPF available** and AMF cannot create SM contexts.

```bash
# Fix: restart UPF after SMF is ready (or use reconcile script)
./scripts/reconcile_oai_pfcp.sh central-0

# Verify
ssh central-0 kubectl logs -n oai-cn deploy/smf-core --since=5m | grep -i 'ASSOCIATION SETUP RESPONSE'
ssh central-0 kubectl logs -n oai-upf deploy/upf-core --since=5m | grep -i 'HEARTBEAT'
```

## User plane (N3 GTP-U)

CU-UP must send GTP-U to **UPF N3** (`10.1.139.11`), not N4 (`10.1.139.13`). Two OAI gaps caused the wrong address:

1. **UPF NRF profile** — `upf_info` lacked `interfaceUpfInfoList`; UPF registered only `ipv4Addresses: [N4]`.
2. **SMF `upfs` config** — the OAI SMF operator sets `upfs[].host` to UPF **N4** (PFCP); SMF then used that as the N3 UL FTEID unless `n3_local_ipv4` is set.

Fixes are in `configmap-oai-upf-nf-conf` (`interfaceUpfInfoList` from NFDeployment interfaces) and `configmap-oai-smf-nf-conf` (`n3_local_ipv4` injected by [render_oai_operators_gitops.sh](../scripts/render_oai_operators_gitops.sh)).

After pushing operator ConfigMaps, restart controllers and recreate NF pods:

```bash
ssh central-0 kubectl rollout restart deployment/oai-smf-controller deployment/oai-upf-controller -n oai-cn-operators
ssh central-0 kubectl delete pod -n oai-cn -l workload.nephio.org/oai=smf
ssh central-0 kubectl delete pod -n oai-upf -l workload.nephio.org/oai=upf
./scripts/reconcile_oai_pfcp.sh central-0
ssh ue-0 kubectl rollout restart deployment/oai-ue -n oai-ue
```

Verify (real user plane — not pod `eth0`):

```bash
ssh ue-0 kubectl exec -n oai-ue deploy/oai-ue -- ping -I oaitun_ue1 -c 3 8.8.8.8
ssh central-0 kubectl logs -n oai-cn deploy/smf-core --since=5m | grep -A4 'PDU SESSION'
# expect: UL FTEID ... IPv4=10.1.139.11
```

## Debug sidecar (`netshoot`)

CU-CP, CU-UP, DU, AMF, and UPF pods include a **`debug`** container (`nicolaka/netshoot`) for packet capture and shell troubleshooting. RAN executors embed it in the Deployment; AMF/UPF get it via a patched OAI operator (`DEBUG_SIDECAR=yes`).

```bash
# RAN (regional / edge)
kubectl exec -it -n oai-ran-cuup deploy/oai-cu-up -c debug -- sh
tcpdump -i f1u -n port 2152

# AMF / UPF (central) — after operator restart + NF pod recreate
kubectl exec -it -n oai-cn deploy/amf-core -c debug -- sh
kubectl exec -it -n oai-upf deploy/upf-core -c debug -- sh
```

After pushing operator changes, restart AMF/UPF controllers and delete `amf-core` / `upf-core` Deployments so the operator recreates pods with the sidecar.

## Quick health checks

```bash
# AMF gNB table (expect oai-cu-cp Connected)
ssh central-0 kubectl logs -n oai-cn -l workload.nephio.org/oai=amf --tail=30

# CU-CP NGAP + F1 + E1
ssh regional-0 kubectl logs -n oai-ran-cucp -l app.kubernetes.io/name=oai-cu-cp --tail=30

# DU rfsim + F1
ssh edge-0 kubectl logs -n oai-ran-du -l app.kubernetes.io/name=oai-du --tail=30

# CU-UP E1 / GTP-U
ssh edge-0 kubectl logs -n oai-ran-cuup -l app.kubernetes.io/name=oai-cu-up --tail=30

# UE registration + PDU (expect 10.1.0.x and ping 8.8.8.8)
ssh ue-0 kubectl logs -n oai-ue deploy/oai-ue | grep -iE 'Registration Accept|PDU Session|IPv4'
ssh ue-0 kubectl exec -n oai-ue deploy/oai-ue -- ping -c 2 8.8.8.8
```
