---
name: deploy-gitops-service
description: >-
  Deploys or updates services on the Nephio multi-cluster testbed via Config Sync
  GitOps (render → repos/ → Gitea → RootSync). Use when the user asks to deploy,
  add, update, or roll out a service, workload, app, operator, OAI NF, Prometheus,
  MetalLB, Multus, dashboard, or any GitOps manifest to mgmt/central/regional/edge/ue.
---

# Deploy GitOps Service

Day-2 deployment in this lab is **scripted Config Sync**, not Porch `PackageVariant` approve.
Do not invent a Porch flow unless the user explicitly asks for it.

## Checklist

Copy and track:

```
Deploy progress:
- [ ] 1. Target cluster(s) + namespace chosen
- [ ] 2. IPs / placement checked (cluster_lib.sh + docs)
- [ ] 3. Render path chosen (extend existing script vs new render_*)
- [ ] 4. Rendered into repos/<gitea-repo>/
- [ ] 5. Pushed to Gitea
- [ ] 6. Config Sync healthy
- [ ] 7. Workload verified on cluster
```

## Step 1 — Choose target

| Cluster | Local tree (submodule) | Gitea | Context |
|---------|------------------------|-------|---------|
| mgmt | `repos/mgmt/` | Gitea `nephio/mgmt` · GitHub `FCCLab/INA-Infra-mgmt` | `mgmt@mgmt` |
| central | `repos/central-repo/` | Gitea `nephio/central-repo` · GitHub `FCCLab/INA-Infra-central-repo` | `central@central` |
| regional | `repos/regional-repo/` | Gitea `nephio/regional-repo` · GitHub `FCCLab/INA-Infra-regional-repo` | `regional@regional` |
| edge | `repos/edge-repo/` | Gitea `nephio/edge-repo` · GitHub `FCCLab/INA-Infra-edge-repo` | `edge@edge` |
| ue | `repos/ue-repo/` | Gitea `nephio/ue-repo` · GitHub `FCCLab/INA-Infra-ue-repo` | `ue@ue` |

`repos/*` are Gitea submodules (`.gitmodules`). Init: `git submodule update --init --recursive`.

Unstructured layout:

```text
repos/<name>/
  cluster/              # cluster-scoped
  namespaces/<ns>/      # one YAML file per resource
```

**Placement defaults**

- Platform (Flannel, MetalLB, dashboard, local-path): all needed clusters
- Multus: workload clusters; **before** OAI NADs
- OAI core / operators: **central** only
- Slice UPF+CU-UP: slice1→central, slice2→regional, slices 3–5→edge
- CU-CP, DU, UEs, FlexRIC: **edge** (UEs on `usrp`)
- Prefer `ue` cluster only when the user explicitly wants something there

IPs / VIPs: `scripts/cluster_lib.sh`, `nephio/docs/ip.md`, `docs/oai.md`.

## Step 2 — Encode intent in a render script

**Always** change or add a `scripts/render_*_gitops.sh`. Never hand-edit generated YAML under `repos/` without updating the matching script.

1. Prefer extending an existing `render_*` that already owns that namespace.
2. New service family → new `render_<service>_gitops.sh` that:
   - uses `set -euo pipefail`
   - sources `scripts/cluster_lib.sh`
   - writes under `repos/$(cluster_gitea_repo_name "$cluster")/...`
   - prints push/verify hints at the end
3. Match style of nearby render scripts (purge + rewrite, numbered filenames).

Script catalog and dependencies: [reference.md](reference.md).

## Step 3 — Render

```bash
cd /home/fcp/nephio-network-slicing
./scripts/render_<service>_gitops.sh [cluster ...]
```

Respect order when stacking platform + OAI (see [reference.md](reference.md)).

## Step 4 — Push

```bash
./bringup/03_push_to_git_repos/push_git_repos.sh [cluster ...]
# optional: -m "message" | -p pull-only | -n dry-run
```

Pulls each Gitea submodule first, then commits and pushes (never pushes before a successful pull/merge). Default with no args: all of mgmt, central, regional, edge, ue.

Gitea lab: `http://10.1.132.200:3000` (`nephio` / `secret`).

## Step 5 — Verify sync

```bash
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge:~/.kube/config-ue
./scripts/check-configsync.sh [cluster ...]
# optional watch: ./scripts/check-configsync.sh -w 15
```

RootSync period is ~15s. Fix render/push issues before debugging app pods if sync is failing.

## Step 6 — Verify workload

```bash
kubectl --context <ctx> -n <ns> get deploy,sts,svc,pods
# OAI CN/UPF intent:
kubectl --context central@central -n oai-cn get nfdeployment,nfconfig
kubectl --context <ctx> -n oai-upf get nfdeployment
```

For OAI, operators reconcile `NFDeployment` / `NFConfig` into pods. RAN pieces are often plain Deployments in `oai-slice-deployment`.

## Do / don't

| Do | Don't |
|----|--------|
| Extend `render_*` then push | Hand-edit only under `repos/` |
| Source `cluster_lib.sh` for IPs | Hardcode IPs that already have helpers |
| Push only affected clusters when possible | Assume Porch Approve is required |
| Check Config Sync before app logs | Skip Multus when adding OAI NADs |
| Commit only if the user asks | Commit secrets / Gitea passwords |

## Docs (read when needed)

- `nephio/docs/config_sync.md` — GitOps bringup + render order
- `nephio/docs/testbed.md` — topology / contexts
- `nephio/docs/ip.md` — VIP/pools
- `docs/oai.md`, `docs/oai-deployment.md` — OAI placement
- `.cursor/rules/nephio-testbed.mdc`, `.cursor/rules/nephio-gitops-scripts.mdc` — standing constraints
