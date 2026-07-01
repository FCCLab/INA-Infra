# Config Sync

Three-step GitOps bringup for **mgmt** and workload clusters. No Nephio/Porch required.

Manifest-based install (like `bringup/01_gitea/`): `configsync.conf` + `configsync.sh`.

## Layout

```
bringup/02_configsync/
├── configsync.conf       # Gitea URL, credentials, cluster list
├── configsync.sh         # repos → tokens → install
└── manifests/
    ├── operator/         # shared on every cluster
    └── {cluster}/rootsync.yaml
```

## Steps

| Step | Command | What it does |
|------|---------|--------------|
| 1 | `configsync.sh repos` | Create empty Gitea repos (`mgmt`, `central-repo`, …) |
| 2 | `configsync.sh tokens` | Create `{repo}-access-token-configsync` secrets on mgmt |
| 3 | `configsync.sh install` | Operator + RootSync on each cluster |

Run all three:

```bash
./bringup/02_configsync/configsync.sh
./bringup/02_configsync/configsync.sh -n          # dry-run
./bringup/02_configsync/configsync.sh tokens central
./bringup/02_configsync/configsync.sh install --local --fetch mgmt
```

## Prerequisites

- Gitea: `./bringup/01_gitea/gitea.sh`
- CNI on all clusters (for install step)

## Gitea repos

| Cluster  | Gitea repo            | RootSync name  | Token secret on mgmt                    |
|----------|-----------------------|----------------|-----------------------------------------|
| mgmt     | `nephio/mgmt`         | `mgmt`         | `mgmt-access-token-configsync`          |
| central  | `nephio/central-repo` | `central-repo` | `central-repo-access-token-configsync`  |
| regional | `nephio/regional-repo`| `regional-repo`| `regional-repo-access-token-configsync` |
| edge     | `nephio/edge-repo`    | `edge-repo`    | `edge-repo-access-token-configsync`     |
| ue       | `nephio/ue-repo`      | `ue-repo`      | `ue-repo-access-token-configsync`       |

Gitea: [http://10.1.132.200:3000](http://10.1.132.200:3000) (`nephio` / `secret`).

Step 2 uses the Gitea user password as the git token (lab default in `configsync.conf`).

## Install step

Per cluster:

1. Apply `manifests/operator/`
2. Copy token secret mgmt → `config-management-system`
3. Apply `manifests/{cluster}/rootsync.yaml`

Verify:

```bash
./scripts/check-configsync.sh
```

## Typical order

```bash
./bringup/01_gitea/gitea.sh
./bringup/02_configsync/configsync.sh
./bringup/03_push_to_git_repos/push_git_repos.sh
```
