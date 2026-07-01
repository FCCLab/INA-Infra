#!/usr/bin/env bash
# Step F: Gitea repos + {cluster}-repo kpt packages on mgmt (Porch + Nephio Repository + tokens).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

MGMT_CTX="${MGMT_CTX:-mgmt@mgmt}"
MGMT_KUBECONFIG="${MGMT_KUBECONFIG:-${KUBECONFIG:-$HOME/.kube/config}}"
CLUSTER_REPOS_DIR="${CLUSTER_REPOS_DIR:-$SCRIPT_DIR/cluster-repos}"
KPT_CATALOG_URL="${KPT_CATALOG_URL:-https://github.com/nephio-project/catalog.git/distros/sandbox/repository@v6}"
RECONCILE_TIMEOUT="${RECONCILE_TIMEOUT:-15m}"
SKIP_GITEA=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Register workload deployment repos on mgmt (step F in central/readme.md):

  1. Create empty Gitea repos ({cluster}-repo) if missing
  2. kpt render + live apply {cluster}-repo package on mgmt

Creates Porch + infra Repository CRs and git access tokens on mgmt.
Do not apply these packages on workload clusters.

Default (no args): central, regional, edge, ue.

Prerequisites:
  - Step E done: ./configsync/setup_api_of_clusters.sh --fetch
  - kpt, kubectl; docker (or kpt function runner) for kpt fn render
  - mgmt context ${MGMT_CTX}

Options:
  --skip-gitea    Skip Gitea repo creation (repos must already exist)
  -n, --dry-run   Print actions only
  -h, --help      Show this help

Environment:
  MGMT_CTX            kubectl context for mgmt (default: mgmt@mgmt)
  MGMT_KUBECONFIG       kubeconfig file for mgmt (default: \$KUBECONFIG or ~/.kube/config)
  KPT_CATALOG_URL       upstream repository package (default: catalog @v6)
  RECONCILE_TIMEOUT     kpt live apply timeout (default: 15m)
  CLUSTER_REPOS_DIR     generated packages for regional/edge/ue (default: configsync/cluster-repos)

Examples:
  $(basename "$0")
  $(basename "$0") central
  $(basename "$0") --skip-gitea regional edge

Verify:
  kubectl --context=${MGMT_CTX} get repositories.config.porch.kpt.dev
  kubectl --context=${MGMT_CTX} get repositories.infra.nephio.org
  kubectl --context=${MGMT_CTX} get tokens.infra.nephio.org | grep repo

Next: ./configsync/setup_workload_configsync.sh
EOF
}

kubectl_mgmt() {
  kubectl --context="$MGMT_CTX" "$@"
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    central|regional|edge|ue) return 0 ;;
    *)
      echo "error: unknown cluster '${cluster}' (expected central, regional, edge, or ue)" >&2
      return 1
      ;;
  esac
}

repo_package_path() {
  local cluster="$1"
  local repo_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  if [[ "$cluster" == "central" && -d "$REPO_ROOT/central/central-repo" ]]; then
    printf '%s' "$REPO_ROOT/central/central-repo"
  else
    printf '%s/%s' "$CLUSTER_REPOS_DIR" "$repo_name"
  fi
}

patch_gitea_urls() {
  local pkg_dir="$1"
  local f
  for f in "$pkg_dir/set-values.yaml" "$pkg_dir/repository/set-values.yaml"; do
    [[ -f "$f" ]] || continue
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "    dry-run: patch Gitea URL in ${f}"
      continue
    fi
    sed -i \
      's|http://172.18.0.200:3000/nephio/|http://gitea.gitea.svc.cluster.local:3000/nephio/|g' \
      "$f"
  done
}

patch_repo_package_context() {
  local pkg_dir="$1"
  local repo_name="$2"
  local f
  for f in "$pkg_dir/package-context.yaml" "$pkg_dir/repository/package-context.yaml"; do
    [[ -f "$f" ]] || continue
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "    dry-run: patch ${f} name=${repo_name}"
      continue
    fi
    python3 - "$f" "$repo_name" <<'PY'
import sys
from pathlib import Path

path, repo = sys.argv[1:3]
text = Path(path).read_text()
lines = text.splitlines()
out = []
in_data = False
for line in lines:
    if line.strip() == "data:":
        in_data = True
        out.append(line)
        continue
    if in_data and line and not line.startswith(" "):
        in_data = False
    if in_data:
        key = line.split(":", 1)[0].strip()
        if key in ("name", "clusterName"):
            continue
    out.append(line)
if "data:" not in out:
    out.append("data:")
idx = out.index("data:") + 1
out[idx:idx] = [f"  name: {repo}", f"  clusterName: {repo}"]
Path(path).write_text("\n".join(out) + "\n")
PY
  done
}

ensure_repo_package() {
  local cluster="$1"
  local repo_name pkg_dir
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  pkg_dir="$(repo_package_path "$cluster")"

  if [[ -d "$pkg_dir" && -f "$pkg_dir/Kptfile" ]]; then
    echo "==> [${cluster}] package ${pkg_dir}"
    return 0
  fi

  if [[ "$cluster" == "central" ]]; then
    echo "error: [${cluster}] missing ${pkg_dir} — expected checked-in central/central-repo" >&2
    return 1
  fi

  echo "==> [${cluster}] kpt pkg get ${repo_name}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: mkdir -p ${CLUSTER_REPOS_DIR} && kpt pkg get --for-deployment ${KPT_CATALOG_URL} ${repo_name}"
    return 0
  fi

  mkdir -p "$CLUSTER_REPOS_DIR"
  (
    cd "$CLUSTER_REPOS_DIR"
    kpt pkg get --for-deployment "$KPT_CATALOG_URL" "$repo_name"
  )
}

apply_repo_package() {
  local cluster="$1"
  local repo_name pkg_dir
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  pkg_dir="$(repo_package_path "$cluster")"

  ensure_repo_package "$cluster" || return 1
  patch_gitea_urls "$pkg_dir"
  patch_repo_package_context "$pkg_dir" "$repo_name"

  echo "==> [${cluster}] kpt fn render ${repo_name}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: (cd ${pkg_dir} && kpt fn render .)"
    echo "    dry-run: kpt live init ${pkg_dir}  # if inventory-template.yaml missing"
    echo "    dry-run: kpt live apply ${pkg_dir} --reconcile-timeout=${RECONCILE_TIMEOUT}"
    return 0
  fi

  (
    cd "$pkg_dir"
    kpt fn render .
    if [[ ! -f inventory-template.yaml ]]; then
      KUBECONFIG="$MGMT_KUBECONFIG" kpt live init . 2>/dev/null || \
        KUBECONFIG="$MGMT_KUBECONFIG" kpt live init --force .
    fi
    KUBECONFIG="$MGMT_KUBECONFIG" kubectl config use-context "$MGMT_CTX" >/dev/null
    kpt live apply . --reconcile-timeout="$RECONCILE_TIMEOUT" --output=table
  )
}

create_gitea_repos() {
  local clusters=("$@")
  local args=()
  for cluster in "${clusters[@]}"; do
    args+=("$cluster")
  done
  echo "==> create Gitea repos for: ${clusters[*]}"
  if [[ "$DRY_RUN" == "1" ]]; then
    "$SCRIPT_DIR/add-gitea-repos.sh" -n "${args[@]}"
  else
    "$SCRIPT_DIR/add-gitea-repos.sh" "${args[@]}"
  fi
  echo
}

print_summary() {
  local clusters=("$@")
  local cluster repo_name
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi
  echo "Repositories on mgmt (${MGMT_CTX}):"
  for cluster in "${clusters[@]}"; do
    repo_name="$(cluster_gitea_repo_name "$cluster")"
    kubectl_mgmt get "repositories.config.porch.kpt.dev/${repo_name}" -n default 2>/dev/null || true
    kubectl_mgmt get "repositories.infra.nephio.org/${repo_name}" -n default 2>/dev/null || true
  done
  echo
  kubectl_mgmt get tokens.infra.nephio.org -A 2>/dev/null | grep -E 'repo|NAME' || true
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-gitea)
      SKIP_GITEA=1
      shift
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    clusters+=("$cluster")
  done
fi

for cmd in kubectl kpt; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: ${cmd} not found in PATH" >&2
    exit 1
  fi
done

if [[ "$DRY_RUN" != "1" ]]; then
  if ! kubectl --kubeconfig="$MGMT_KUBECONFIG" config get-contexts "$MGMT_CTX" >/dev/null 2>&1; then
    echo "error: context ${MGMT_CTX} not found in ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
fi

echo "Setup cluster repos on mgmt (${MGMT_CTX}): ${clusters[*]}"
echo

if [[ "$SKIP_GITEA" != "1" ]]; then
  create_gitea_repos "${clusters[@]}"
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! apply_repo_package "$cluster"; then
    failed=1
  fi
  echo
done

print_summary "${clusters[@]}"
exit "$failed"
