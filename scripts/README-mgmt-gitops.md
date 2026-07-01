# Mgmt GitOps from live cluster (skip messy `bringup/`)

Export **what is running now** on mgmt into **`initial_mgmt/`**, push to Gitea **`nephio/mgmt`**, then use git as source of truth.

This uses **Config Sync unstructured** format (plain YAML), not Porch kpt packages.

## One-time setup

```bash
cd ~/nephio-network-slicing
chmod +x initial_mgmt/scripts/export-mgmt-live.sh initial_mgmt/scripts/push-mgmt-to-gitea.sh

kubectl config use-context mgmt@mgmt

curl -fsS -u nephio:secret http://10.1.132.200:3000/api/v1/repos/nephio/mgmt
```

Config Sync must already run on mgmt (`config-management-system` namespace). RootSync is applied by the push script.

## Export + push current config

```bash
./initial_mgmt/scripts/export-mgmt-live.sh          # writes initial_mgmt/
./initial_mgmt/scripts/push-mgmt-to-gitea.sh       # pushes initial_mgmt/ → Gitea
```

Browse: http://10.1.132.200:3000/nephio/mgmt

## Day-2 workflow

1. Edit YAML under `initial_mgmt/` (or clone Gitea repo and push).
2. `./initial_mgmt/scripts/push-mgmt-to-gitea.sh`
3. Config Sync reconciles within ~15s.

Check sync status:

```bash
./scripts/check-configsync.sh
./scripts/check-configsync.sh -w 15   # watch after a push
```

Re-export after manual hotfixes:

```bash
./initial_mgmt/scripts/export-mgmt-live.sh && ./initial_mgmt/scripts/push-mgmt-to-gitea.sh
```

Custom output dir:

```bash
OUT_DIR=initial_mgmt ./initial_mgmt/scripts/export-mgmt-live.sh
./initial_mgmt/scripts/push-mgmt-to-gitea.sh -s initial_mgmt
```

## Layout

```
initial_mgmt/
  README.md
  namespaces/
    gitea/
    porch-system/
    nephio-system/
    ...
  cluster/
    storageclass-local-path.yaml
```

## What gets exported

| Included | Excluded |
|----------|----------|
| Gitea, Porch, Nephio, WebUI, MetalLB, dashboard (NodePort), … | `kube-system` |
| Deployments, StatefulSets, Services, ConfigMaps, Secrets | `config-management-system` |
| `local-path` StorageClass | Pods, ReplicaSets, Porch PackageRevisions |
| **`default` namespace** | **OpenSpeedTest only** (`openspeedtest` + LB `10.1.132.11`) — not porch secrets |

OpenSpeedTest URL after sync: **http://10.1.132.11**

```bash
SKIP_SECRETS=1 ./initial_mgmt/scripts/export-mgmt-live.sh   # omit Secrets
```

**`central-repo`** for the workload cluster is unchanged — still use Porch Approve for OAI on central.
