# GitOps render reference

Read from the deploy skill when choosing a script or stacking multiple renders.

## Recommended order

From `nephio/docs/config_sync.md` (trim to what you need):

```bash
./scripts/render_flannel_gitops.sh
./scripts/render_local_path_gitops.sh
./scripts/render_multus_gitops.sh                    # before OAI NADs
./scripts/render_oai_operators_gitops.sh central
./scripts/render_oai_core_gitops.sh central
./scripts/render_oai_slice_deployment_gitops.sh      # multi-slice UPF/CU-UP/CU-CP/DU/UE
./scripts/render_metallb_gitops.sh mgmt central regional edge ue
./scripts/render_dashboard_gitops.sh mgmt central regional edge ue
./scripts/render_prometheus_gitops.sh central        # optional
./scripts/render_gpu_operator_gitops.sh              # where needed
./bringup/03_push_to_git_repos/push_git_repos.sh
./scripts/check-configsync.sh
```

Legacy / alternate OAI renders (prefer `render_oai_slice_deployment_gitops.sh` for the current multi-slice design):

- `render_oai_ran_gitops.sh`, `render_oai_ran_du_gitops.sh`, `render_oai_ran_cuup_gitops.sh`
- `render_oai_gnb_mono_gitops.sh`, `render_oai_ue_gitops.sh`, `render_oai_nws_1ue_gitops.sh`, `render_oai_gnb_ns_1ue_gitops.sh`

## Script → typical destination

| Script | Clusters | Notes |
|--------|----------|-------|
| `render_flannel_gitops.sh` | all | CNI |
| `render_local_path_gitops.sh` | all | StorageClass |
| `render_multus_gitops.sh` | workload | Required before OAI NADs |
| `render_metallb_gitops.sh` | all | Site VIP pools in `cluster_lib.sh` |
| `render_dashboard_gitops.sh` | all | NodePort 30443 |
| `render_prometheus_gitops.sh` | default central | Annotation-based scrape |
| `render_gpu_operator_gitops.sh` | central/edge as used | NVIDIA |
| `render_oai_operators_gitops.sh` | **central** | CN/RAN operators |
| `render_oai_core_gitops.sh` | **central** | `oai-cn` NFDeployments |
| `render_oai_slice_deployment_gitops.sh` | central+regional+edge | Current slice design |

## OAI namespace map

| Namespace | Role |
|-----------|------|
| `oai-cn-operators` | Operators |
| `oai-cn` | Shared 5GC (AMF, SMF, NRF, …) |
| `oai-upf` | Per-slice UPFs |
| `oai-slice-deployment` | CU-CP/CU-UP/DU/UE/FlexRIC |

Slice site mapping: **1→central, 2→regional, 3–5→edge** (see header of `render_oai_slice_deployment_gitops.sh`).

## New render script skeleton

```bash
#!/usr/bin/env bash
# Render <service> into repos/ for Config Sync GitOps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
NS="${MY_NS:-my-service}"

write_cluster() {
  local cluster="$1"
  local repo_name dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${NS}"
  mkdir -p "$dest_ns"
  # purge old generated files, then write fresh YAML into dest_ns /
  # ${REPOS_DIR}/${repo_name}/cluster as needed
  echo "==> [${cluster}] ${dest_ns}"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi
  for cluster in "${clusters[@]}"; do
    write_cluster "$cluster"
  done
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

main "$@"
```

Use `cluster_gitea_repo_name`, VIP helpers, and site iface constants from `cluster_lib.sh` instead of copying literals.

## Prerequisites (once per lab)

```bash
./bringup/01_gitea/gitea.sh
./bringup/02_configsync/configsync.sh   # repos → tokens → RootSync
```

Porch/Nephio on mgmt (`readme.md` kpt steps) is optional for this GitOps path.
