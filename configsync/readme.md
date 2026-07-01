# Config Sync

Shared scripts and docs for [Config Sync](https://github.com/GoogleContainerTools/kpt-config-sync) GitOps on mgmt and workload clusters.

## Register workload clusters on mgmt (Nephio API access)

After `./scripts/bringup_cluster.sh`, register each cluster so Nephio on mgmt can reach its API:

```bash
./configsync/setup_api_of_clusters.sh --fetch
# WorkloadCluster CR + {cluster}-kubeconfig secret for central, regional, edge, ue

./configsync/setup_api_of_clusters.sh central   # one cluster
```

| Cluster | Site type label | Kubeconfig secret | Local kubeconfig |
|---------|-----------------|-------------------|------------------|
| central | `core` | `central-kubeconfig` | `~/.kube/config-central` |
| regional | `regional` | `regional-kubeconfig` | `~/.kube/config-regional` |
| edge | `edge` | `edge-kubeconfig` | `~/.kube/config-edge` |
| ue | `ue` | `ue-kubeconfig` | `~/.kube/config-ue` |

Use `--fetch` to copy kubeconfig from each control plane over SSH (recommended when the API is on `10.1.137.x` only).

Manifests: [api/workloadclusters.yaml](api/workloadclusters.yaml) (applied by the script above).

## Register deployment repos on mgmt (Porch + tokens)

After step E, create Gitea repos and apply `{cluster}-repo` kpt packages on **mgmt** (step F):

```bash
./configsync/setup_cluster_repos.sh
# Gitea repos + kpt live apply central-repo, regional-repo, edge-repo, ue-repo on mgmt

./configsync/setup_cluster_repos.sh central
./configsync/setup_cluster_repos.sh --skip-gitea regional   # repos already exist
```

Creates Porch + infra `Repository` CRs and access tokens (`{cluster}-repo-access-token-configsync`, `…-porch`).

## Config Sync + RootSync on workload clusters

After step F, install the Config Sync operator and RootSync on each workload cluster (steps G–H):

```bash
./configsync/setup_workload_configsync.sh --remote central
# default --remote: render locally, kpt live apply via SSH to control plane

./configsync/setup_workload_configsync.sh --remote
./configsync/setup_workload_configsync.sh --local --fetch central   # laptop + port-forward
```

Verify:

```bash
./scripts/check-configsync.sh -c central@central -n central-repo
```

Run `./configsync/setup_cluster_repos.sh` before this script (step F). If a cluster is down or unreachable via SSH, run one cluster at a time, e.g. `./configsync/setup_workload_configsync.sh central`.

## Gitea repos only

To create empty Gitea repos without applying kpt packages:

```bash
./configsync/add-gitea-repos.sh
# central-repo, regional-repo, edge-repo, ue-repo

./configsync/add-gitea-repos.sh --include-mgmt
# also mgmt, mgmt-staging
```

| Cluster | Gitea repo | Config Sync RootSync name |
|---------|------------|---------------------------|
| central | `nephio/central-repo` | `central-repo` |
| regional | `nephio/regional-repo` | `regional-repo` |
| edge | `nephio/edge-repo` | `edge-repo` |
| ue | `nephio/ue-repo` | `ue-repo` |

Gitea UI: [http://10.1.132.51:3000](http://10.1.132.51:3000) (`nephio` / `secret`).

## Install Config Sync operator

Kpt packages (same upstream catalog):

- Workload: [central/configsync](../central/configsync)
- Mgmt: [bringup/configsync](../bringup/configsync)

```bash
cd central/configsync   # or bringup/configsync on mgmt
kpt fn render .
kpt live init .         # first time only
kpt live apply . --reconcile-timeout=15m
```

Full walkthrough: [central/readme.md](../central/readme.md) steps **C**, **F–H**.

## Related scripts

| Script | Purpose |
|--------|---------|
| [setup_api_of_clusters.sh](setup_api_of_clusters.sh) | WorkloadCluster CRs + kubeconfig secrets on mgmt (step E) |
| [setup_cluster_repos.sh](setup_cluster_repos.sh) | Gitea repos + {cluster}-repo kpt on mgmt (step F) |
| [setup_workload_configsync.sh](setup_workload_configsync.sh) | Config Sync operator + RootSync on workload (steps G–H) |
| [add-gitea-repos.sh](add-gitea-repos.sh) | Create Gitea repos only |
| [scripts/check-configsync.sh](../scripts/check-configsync.sh) | RootSync status |
| [scripts/setup-central-rootsync-token.sh](../scripts/setup-central-rootsync-token.sh) | Copy git token mgmt → central |
| [initial_central/scripts/push-central-to-gitea.sh](../initial_central/scripts/push-central-to-gitea.sh) | Push export + bootstrap RootSync |
