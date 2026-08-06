# API and configuration CRs

The operator does **not** introduce a separate `RANDeployment` API at runtime. It reconciles Nephio **`NFDeployment`** and reads nested config from **`NFConfig`** / **`Config`** references. Local Go types under `api/v1alpha1` describe the JSON shapes embedded in those configs.

## NFDeployment (watched)

- API: `workload.nephio.org/v1alpha1` — [nf_deployment_types.go](https://github.com/nephio-project/api/blob/main/workload/v1alpha1/nf_deployment_types.go)
- Key fields used by this operator:
  - `spec.provider` — one of the three OAI RAN providers ([architecture.md](architecture.md))
  - `spec.interfaces[]` — Multus interface name + IPv4 (e.g. `n2`, `f1c`, `e1`, `f1`, `f1u`, `n3`)
  - `spec.parametersRefs[]` — pointers to config objects

Example CU-CP blueprint (upstream catalog): [pkg-example-cucp-bp](https://github.com/nephio-project/catalog/blob/main/workloads/oai/pkg-example-cucp-bp/cucpdeployment.yaml).

## ParametersRefs resolution

| `apiVersion` | Kind / usage |
|--------------|--------------|
| `workload.nephio.org/v1alpha1` | `NFConfig` — `spec.configRefs[]` raw extensions must include kinds **`PLMN`**, **`RANConfig`**, **`OAIConfig`** |
| `ref.nephio.org/v1alpha1` | `Config` — usually embeds peer `NFDeployment` specs (other RAN NFs) for IP wiring |

Missing mandatory kinds → reconcile error / status `invalidConfigInfo`.

## Embedded config shapes (`api/v1alpha1`)

### OAIConfig

Image for the OAI container.

```yaml
kind: OAIConfig
spec:
  image: oaisoftwarealliance/oai-gnb:v2.3.0   # or oai-nr-cuup for CU-UP
```

### RANConfig

Cell / RF parameters rendered into `gnb.conf`.

| Field | Notes |
|-------|--------|
| `cellIdentity` | NR cell id (string in template) |
| `physicalCellID` | 0–503 |
| `downlinkFrequencyBand` / `uplinkFrequencyBand` | NR band numbers |
| `downlinkSubCarrierSpacing` / `uplinkSubCarrierSpacing` | SCS (kHz) |
| `downlinkCarrierBandwidth` / `uplinkCarrierBandwidth` | PRBs (e.g. 106 ≈ 20 MHz, 217 ≈ 40 MHz) |

### PLMN

```yaml
kind: PLMN
spec:
  PLMNInfo:
    - plmnID: { mcc: "001", mnc: "01" }
      tac: 1
      nssai:
        - sst: 1
          sd: "ffffff"   # optional; hex 6 chars
```

Lab defaults often use MCC/MNC **001/01**, SST **1**, SD **ffffff** — see [`docs/oai.md`](../../../docs/oai.md).

## CRD YAML in-tree

Kubebuilder bases (may lag live cluster CRDs):

- `config/crd/bases/_oaiconfigs.yaml`
- `config/crd/bases/_ranconfigs.yaml`
- `config/crd/bases/_plmns.yaml`

RBAC scaffold still names `randeployments`; the running controller watches **`NFDeployment`**. Prefer catalog/GitOps ClusterRoles when deploying on the lab.
