---
name: smo-gitops
description: >-
  Workflow for rendering, maintaining, and deploying the O-RAN SC Non-RT-RIC,
  Base SMO (TEIV, Kafka, PostgreSQL), ONAP Infrastructure (ChartMuseum, ACM Runtime,
  K8s Participant), and rApps on edge/central clusters via GitOps.
---

# SMO & Non-RT-RIC GitOps Deployment Skill

Manages the O-RAN SC Service Management and Orchestration (SMO), Non-RT-RIC, and ONAP Infrastructure stack under `repos/edge-repo/namespaces/neuro-ran-smo/` on the edge cluster (`edge@edge`).

---

## 1. Stack Components & Hierarchy

- **`smo/`**: Topology Exposure & Inventory (TEIV), Kafka (KRaft), PostgreSQL (TIES schemas).
- **`nonrtric/`**: A1-PMS (`:8081`), ICS/DME (`:8083`), DMaaP Adapter (`:9087`), rApp Manager (`:8080`), CAPIF Core (`:8090`), Service Manager (`:8095`), A1 Simulators (`:8085`).
- **`onap/`**: ChartMuseum (`:8080`), Policy Clamp ACM Runtime (`:6969`), K8s Participant (`:8083`).

> **Rule**: Never edit upstream code in `smo/osc-non-rt-ric`. All Helm chart packaging and manifest generation use temporary staging directories.

---

## 2. Rendering Manifests

```bash
cd /home/fcp/INA-Infra

# 1. Base SMO (TEIV, Kafka, Postgres with local-path & node affinities)
./scripts/render_smo_gitops.sh

# 2. ONAP Infrastructure (ChartMuseum, ACM runtime, K8s participant)
./scripts/render_onap_infra_gitops.sh

# 3. Non-RT-RIC (A1-PMS, ICS, rApp Manager, CAPIF, ServiceManager, A1-Sims)
./scripts/render_nonrtric_gitops.sh
```

---

## 3. Deployment & Verification

```bash
# Push rendered manifests to GitOps
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(smo): update manifests" edge

# Verify Config Sync
./scripts/check-configsync.sh edge

# Verify Workload Pods
kubectl --context=edge@edge get pods -n onap -o wide
kubectl --context=edge@edge get pods -n nonrtric -o wide
kubectl --context=edge@edge get pods -n smo -o wide
```

---

## 4. rApp Packaging & Deployment

```bash
# 1. Build container image
./build.sh --repo 10.1.132.30:5000 --name oran-sc/nonrtric-rapp-<name>-image --tag 0.0.1

# 2. Package Helm chart into CSAR
helm package <chart-dir>/ -d <rapp-dir>/Artifacts/Deployment/HELM/

# 3. Generate CSAR package
smo/osc-non-rt-ric/rappmanager/sample-rapp-generator/generate.sh <rapp-dir>/

# 4. Lifecycle via rApp Manager
bash it-dep/rapp-deployment/deploy-rapp.sh <rapp-name> <csar-path>
bash it-dep/rapp-deployment/prime-rapp.sh <rapp-name>
bash it-dep/rapp-instance-deployment/create-instance.sh <rapp-name>
bash it-dep/rapp-instance-deployment/deploy-instance.sh <rapp-name> <instance-id>
```
