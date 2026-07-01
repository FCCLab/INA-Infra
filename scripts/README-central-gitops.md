# Central GitOps from live cluster

Export **what is running now** on **central** into **`initial_central/`**, push to Gitea **`nephio/central-repo`**, then use git as source of truth (Config Sync unstructured format).

## Prerequisites

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
kubectl config get-contexts   # need central@central

# Gitea repo exists
curl -fsS -u nephio:secret http://10.1.132.200:3000/api/v1/repos/nephio/central-repo

# Config Sync running on central (central/configsync kpt apply)
kubectl --context=central@central get pods -n config-management-system

# Token CR applied on mgmt (central/central-repo kpt package)
kubectl --context=mgmt@mgmt get token central-repo-access-token-configsync -n default
```

## Export + push

```bash
chmod +x initial_central/scripts/export-central-live.sh initial_central/scripts/push-central-to-gitea.sh

./initial_central/scripts/export-central-live.sh          # writes initial_central/
./initial_central/scripts/push-central-to-gitea.sh        # pushes → Gitea, bootstraps RootSync if needed
```

Browse: http://10.1.132.200:3000/nephio/central-repo

## Day-2 workflow

1. Edit YAML under `initial_central/` (or clone Gitea repo and push).
2. `./initial_central/scripts/push-central-to-gitea.sh`
3. Config Sync reconciles within ~15s.

```bash
./scripts/check-configsync.sh -c central@central -n central-repo
./scripts/check-configsync.sh -c central@central -w 15
```

## Layout

```
initial_central/
  README.md
  namespaces/
    local-path-storage/
    ...
  cluster/
    storageclass-local-path.yaml
```

## What gets exported

| Included | Excluded |
|----------|----------|
| `local-path-storage`, app namespaces you list | `kube-system`, `kube-flannel` |
| Deployments, Services, ConfigMaps, Secrets, … | `config-management-system` |
| `local-path` StorageClass | `resource-group-system`, Pods |

Extend namespaces when OAI workloads are deployed:

```bash
EXPORT_NAMESPACES="local-path-storage oai5g oai-cp" ./initial_central/scripts/export-central-live.sh
```

**Note:** Porch **Approve** also writes OAI packages into `nephio/central-repo`. This export path is for live cluster baseline GitOps (like `initial_mgmt/` on mgmt).

## RootSync bootstrap

- Token secret is created on **mgmt**; copied to **central** `config-management-system` by `initial_central/scripts/push-central-to-gitea.sh` or `scripts/setup-central-rootsync-token.sh`.
- RootSync name: **`central-repo`**
- Git repo: **`http://10.1.132.200:3000/nephio/central-repo.git`**

If RootSync exists with a stale Gitea URL, `initial_central/scripts/push-central-to-gitea.sh` patches it to the current `GITEA_HOST`/`GITEA_PORT`.
