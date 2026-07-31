#!/usr/bin/env bash
# Register lab Gitea remotes on repos/* GitOps submodules after a GitHub clone.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

ALL_SETUP_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

After cloning FCCLab/INA-Infra from GitHub, run on the testbed to add Gitea remotes:
  origin → GitHub (FCCLab/INA-Infra-*)
  gitea  → lab Gitea (nephio/*, Config Sync source)

Default (no args): mgmt central regional edge
EOF
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt|central|regional|edge) return 0 ;;
    *) echo "error: unknown cluster '${cluster}'" >&2; exit 1 ;;
  esac
}

setup_cluster_repo() {
  local cluster="$1"
  local repo_name rel_path gitea_url github_url src_dir

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  rel_path="repos/${repo_name}"
  src_dir="${REPO_ROOT}/${rel_path}"
  gitea_url="$(gitea_repo_url "$repo_name")"
  github_url="$(github_repo_url "$(github_gitops_repo_name "$cluster")")"

  echo "==> [${cluster}] ${rel_path}"
  if [[ ! -d "$src_dir" ]]; then
    echo "    initializing submodule ..."
    (
      cd "$REPO_ROOT"
      git submodule update --init -- "$rel_path"
    )
  fi

  cd "$src_dir"

  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$github_url"
  else
    git remote add origin "$github_url"
  fi

  if git remote get-url gitea >/dev/null 2>&1; then
    git remote set-url gitea "$gitea_url"
  else
    git remote add gitea "$gitea_url"
  fi

  echo "    origin: ${github_url}"
  echo "    gitea:  ${gitea_url}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    mgmt|central|regional|edge) break ;;
    *) echo "error: unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_SETUP_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster"
    clusters+=("$cluster")
  done
fi

for cluster in "${clusters[@]}"; do
  setup_cluster_repo "$cluster"
done

echo "Done. Push GitOps with: ./bringup/03_push_to_git_repos/push_git_repos.sh"
