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

| Cluster  | Gitea repo            | Local submodule       | RootSync name  | Token secret on mgmt                    |
|----------|-----------------------|-----------------------|----------------|-----------------------------------------|
| mgmt     | `nephio/mgmt`         | `repos/mgmt`          | `mgmt`         | `mgmt-access-token-configsync`          |
| central  | `nephio/central-repo` | `repos/central-repo`  | `central-repo` | `central-repo-access-token-configsync`  |
| regional | `nephio/regional-repo`| `repos/regional-repo` | `regional-repo`| `regional-repo-access-token-configsync` |
| edge     | `nephio/edge-repo`    | `repos/edge-repo`     | `edge-repo`    | `edge-repo-access-token-configsync`     |
| ue       | `nephio/ue-repo`      | `repos/ue-repo`       | `ue-repo`      | `ue-repo-access-token-configsync`       |

**Clone (GitHub):** submodules use `https://github.com/FCCLab/INA-Infra-*.git` in [`.gitmodules`](../../.gitmodules). Clusters still reconcile from **Gitea** `nephio/*` (unchanged RootSync URLs).

```bash
git clone --recurse-submodules https://github.com/FCCLab/INA-Infra.git
cd INA-Infra
git submodule update --init --recursive
./scripts/setup_lab_git_remotes.sh   # testbed only: adds gitea remote on repos/*
```

Render scripts write into the submodule working trees; `push_git_repos.sh` pulls from **Gitea**, pushes Gitea, then mirrors **GitHub** `origin`.

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
# Render GitOps manifests into repos/ (order matters — Multus/OAI before MetalLB; dashboard last):
./scripts/render_flannel_gitops.sh
./scripts/render_local_path_gitops.sh
./scripts/render_multus_gitops.sh          # workload clusters; before OAI NADs
./scripts/render_oai_operators_gitops.sh central
./scripts/render_oai_core_gitops.sh central
./scripts/render_oai_ran_gitops.sh regional   # CU-CP operator + executor (needs Multus)
./scripts/render_oai_ran_du_gitops.sh edge  # DU operator + rfsim RU (needs Multus + regional CU-CP)
./scripts/render_oai_ran_cuup_gitops.sh edge  # CU-UP (needs Multus + regional CU-CP + central UPF)
./scripts/render_metallb_gitops.sh mgmt central regional edge ue
./scripts/render_dashboard_gitops.sh mgmt central regional edge ue
./bringup/03_push_to_git_repos/push_git_repos.sh
./scripts/check-configsync.sh
```

OAI operators and core NFs run on **central** only (`render_oai_*` scripts reject other clusters). NRF/UDR use `ClusterIP`; AMF/UPF use fixed MetalLB VIPs on central (see [ip.md](ip.md)).

IP pools, VIPs, and node addresses: [ip.md](ip.md).
