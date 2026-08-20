---
name: smo-gitops
description: >-
  Workflow for rendering, maintaining, and deploying the O-RAN SC Non-RT-RIC,
  Base SMO (TEIV, Kafka, PostgreSQL), and ONAP Infrastructure (ChartMuseum,
  ACM Runtime, K8s Participant) stack and rApps on edge clusters via GitOps.
---

# SMO & Non-RT-RIC GitOps Deployment Skill

This skill defines the canonical workflow for managing, rendering, and deploying the O-RAN SC SMO, Non-RT-RIC, and ONAP Infrastructure stack under `repos/edge-repo/namespaces/neuro-ran-smo/` on the edge cluster (`edge@edge`).

---

## 1. Core Principles & Architecture

1. **Upstream Read-Only Submodule**:
   - Upstream code is located in [`smo/osc-non-rt-ric`](file:///home/fcp/INA-Infra/smo/osc-non-rt-ric).
   - **Rule**: Never edit, modify, or add untracked build artifacts inside `smo/osc-non-rt-ric`.
   - All chart dependency packaging and Helm rendering take place in isolated `/tmp` workspaces and clean up automatically upon exit.

2. **Hierarchical Namespace Organization**:
   Manifests are organized under [`repos/edge-repo/namespaces/neuro-ran-smo/`](file:///home/fcp/INA-Infra/repos/edge-repo/namespaces/neuro-ran-smo/):
   - [`onap/`](file:///home/fcp/INA-Infra/repos/edge-repo/namespaces/neuro-ran-smo/onap/): ChartMuseum (`:8080`), Policy Clamp ACM Runtime (`:6969`), K8s Participant (`:8083`).
   - [`nonrtric/`](file:///home/fcp/INA-Infra/repos/edge-repo/namespaces/neuro-ran-smo/nonrtric/): A1-PMS (`:8081`), ICS / DME (`:8083`), DMaaP Adapter (`:9087`), rApp Manager (`:8080`), CAPIF Core (`:8090`), Service Manager (`:8095`), A1 Simulators (`:8085`).
   - [`smo/`](file:///home/fcp/INA-Infra/repos/edge-repo/namespaces/neuro-ran-smo/smo/): Topology Exposure & Inventory (TEIV), Kafka (KRaft), PostgreSQL (TIES schemas).

3. **Node Placement**:
   - Every workload spec must have `nodeAffinity` targeting `cpu-edge-0` and `cpu-edge-1` (leaving SDR `usrp` and GPU `gpu-a40` free).

---

## 2. Automated Manifest Rendering

Execute the dedicated render scripts to regenerate GitOps manifests:

```bash
cd /home/fcp/INA-Infra

# 1. Base SMO (TEIV, Kafka, Postgres with local-path and affinity)
./scripts/render_smo_gitops.sh

# 2. ONAP Infrastructure (ChartMuseum, ACM runtime, K8s participant)
./scripts/render_onap_infra_gitops.sh

# 3. Non-RT-RIC (A1-PMS, ICS, rApp Manager, CAPIF, ServiceManager, A1-Sims)
./scripts/render_nonrtric_gitops.sh
```

---

## 3. GitOps Synchronization & Verification

1. **Push to Local Gitea (GitOps) & GitHub**:
   ```bash
   ./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(smo): update stack manifests" edge
   ```

2. **Verify Config Sync Reconciliation**:
   ```bash
   ./scripts/check-configsync.sh edge
   ```
   *Expected: `state: Sync Completed`, `result: OK`, `SYNCERRORCOUNT: 0`.*

3. **Verify Pod Statuses**:
   ```bash
   kubectl --context=edge@edge get pods -n onap -o wide
   kubectl --context=edge@edge get pods -n nonrtric -o wide
   kubectl --context=edge@edge get pods -n smo -o wide
   ```

---

## 4. rApp Deployment Procedure

Follow [`smo/osc-non-rt-ric/docs/build_and_deploy_rapp.md`](file:///home/fcp/INA-Infra/smo/osc-non-rt-ric/docs/build_and_deploy_rapp.md):

1. **Build & Package Container Image**:
   ```bash
   ./build.sh --repo localhost:5000 --name oran-sc/nonrtric-rapp-<name>-image --tag 0.0.1
   ```

2. **Package Chart into CSAR Structure**:
   ```bash
   helm package <chart-dir>/ -d <rapp-dir>/Artifacts/Deployment/HELM/
   ```

3. **Generate CSAR Archive**:
   ```bash
   smo/osc-non-rt-ric/rappmanager/sample-rapp-generator/generate.sh <rapp-dir>/
   ```

4. **Lifecycle Execution via `rappmanager`**:
   - **Commission**: `bash it-dep/rapp-deployment/deploy-rapp.sh <rapp-name> <csar-path>`
   - **Prime**: `bash it-dep/rapp-deployment/prime-rapp.sh <rapp-name>`
   - **Create Instance**: `bash it-dep/rapp-instance-deployment/create-instance.sh <rapp-name>`
   - **Deploy Instance**: `bash it-dep/rapp-instance-deployment/deploy-instance.sh <rapp-name> <instance-id>`
   - **Verify Workload**: `kubectl get pods -n nonrtric`
