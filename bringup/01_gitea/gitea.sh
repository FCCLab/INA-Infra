#!/usr/bin/env bash
# Install or uninstall Gitea on mgmt outside Config Sync (do not put gitea/ in repos/mgmt).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/gitea.conf}"
ACTION="install"
DRY_RUN=0
CLI_WITH_STORAGE=0
CLI_KUBECONFIG=""
CLI_CONTEXT=""

expand_path() {
  local p="$1"
  p="${p/#\~/$HOME}"
  printf '%s' "$p"
}

usage() {
  local ui_host="${GITEA_HOST:-10.1.132.51}"
  local ui_port="${GITEA_PORT:-3000}"
  local kcfg="${MGMT_KUBECONFIG:-${KUBECONFIG:-$HOME/.kube/config}}"
  local kctx="${MGMT_CTX:-mgmt@mgmt}"
  local mdir="${GITEA_MANIFESTS_DIR_ABS:-$SCRIPT_DIR/manifests}"
  cat <<EOF
Usage: $(basename "$0") [options]

Deploy Gitea on the mgmt cluster (default: install).
Gitea is platform infrastructure — keep it OUT of repos/mgmt GitOps or
Config Sync will prune it when syncing workload-only manifests.

Config:     ${CONFIG_FILE}
Manifests:  ${mdir}
  kubeconfig: ${kcfg}
  context:    ${kctx}

Edit gitea.conf and YAML under gitea/manifests/, or pass CLI flags.

Prerequisites:
  - mgmt cluster Ready; kubectl context in kubeconfig above
  - MetalLB + local-pool (./scripts/install_ip_pool.sh mgmt)
  - kubectl

Options:
  --kubeconfig FILE   kubeconfig file (overrides gitea.conf)
  --context NAME      kubectl context (overrides gitea.conf)
  --with-storage      Also apply manifests/storage (local-path StorageClass)
  --uninstall, -u     Remove Gitea workloads (keeps namespace PVCs)
  --config FILE       Config file (default: gitea/gitea.conf)
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Examples:
  $(basename "$0")                    # install (default)
  $(basename "$0") --with-storage      # install + local-path StorageClass
  $(basename "$0") --kubeconfig ~/.kube/config --context mgmt@mgmt
  $(basename "$0") --uninstall
  $(basename "$0") -u

After install (repos were lost if Gitea was pruned):
  ./bringup/02_configsync/add-gitea-repos.sh --include-mgmt
  ./bringup/02_configsync/setup_cluster_repos.sh
  ./bringup/03_push_to_git_repos/push_git_repos.sh

UI: http://${ui_host}:${ui_port}  (${GITEA_USER:-nephio} / ${GITEA_PASSWORD:-secret})
EOF
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "error: config not found: ${CONFIG_FILE}" >&2
    exit 1
  fi
  # shellcheck source=gitea.conf
  source "$CONFIG_FILE"

  GITEA_MANIFESTS_DIR_ABS="${GITEA_MANIFESTS_DIR_ABS:-$SCRIPT_DIR/${GITEA_MANIFESTS_DIR:-manifests}}"
  STORAGE_MANIFESTS_DIR_ABS="${STORAGE_MANIFESTS_DIR_ABS:-$SCRIPT_DIR/${STORAGE_MANIFESTS_DIR:-manifests/storage}}"

  MGMT_KUBECONFIG="$(expand_path "${CLI_KUBECONFIG:-${MGMT_KUBECONFIG:-${KUBECONFIG:-$(local_kubeconfig_path mgmt)}}}")"
  MGMT_CTX="${CLI_CONTEXT:-${MGMT_CTX:-${KUBECONFIG_CONTEXT:-$(kube_context mgmt)}}}"

  GITEA_NAMESPACE="${GITEA_NAMESPACE:-gitea}"
  GITEA_HOST="${GITEA_HOST:-10.1.132.51}"
  GITEA_PORT="${GITEA_PORT:-3000}"
  GITEA_LB_IP="${GITEA_LB_IP:-$GITEA_HOST}"
  GITEA_USER="${GITEA_USER:-nephio}"
  GITEA_PASSWORD="${GITEA_PASSWORD:-secret}"
  POD_READY_TIMEOUT="${POD_READY_TIMEOUT:-600s}"
}

validate_kube_access() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  if [[ ! -f "$MGMT_KUBECONFIG" ]]; then
    echo "error: kubeconfig not found: ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
  if ! kubectl --kubeconfig="$MGMT_KUBECONFIG" config get-contexts "$MGMT_CTX" >/dev/null 2>&1; then
    echo "error: context ${MGMT_CTX} not found in ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
}

kubectl_mgmt() {
  kubectl --kubeconfig="$MGMT_KUBECONFIG" --context="$MGMT_CTX" "$@"
}

apply_manifests() {
  local manifest_dir="$1"
  shift
  local -a files=("$@")
  local -a paths=()
  local f

  for f in "${files[@]}"; do
    if [[ ! -f "${manifest_dir}/${f}" ]]; then
      echo "error: manifest not found: ${manifest_dir}/${f}" >&2
      exit 1
    fi
    paths+=("-f" "${manifest_dir}/${f}")
  done

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl apply ${paths[*]}"
    return 0
  fi

  kubectl_mgmt apply "${paths[@]}"
}

delete_manifests() {
  local manifest_dir="$1"
  shift
  local -a files=("$@")
  local -a paths=()
  local f

  for ((i = ${#files[@]} - 1; i >= 0; i--)); do
    f="${files[$i]}"
    if [[ -f "${manifest_dir}/${f}" ]]; then
      paths+=("-f" "${manifest_dir}/${f}")
    fi
  done

  if [[ ${#paths[@]} -eq 0 ]]; then
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl delete ${paths[*]} --ignore-not-found"
    return 0
  fi

  kubectl_mgmt delete "${paths[@]}" --ignore-not-found
}

apply_storage() {
  if kubectl_mgmt get storageclass local-path >/dev/null 2>&1; then
    echo "==> storageclass local-path already exists"
    return 0
  fi

  echo "==> apply local-path storage from ${STORAGE_MANIFESTS_DIR_ABS}"
  apply_manifests "$STORAGE_MANIFESTS_DIR_ABS" "${STORAGE_MANIFESTS[@]}"
}

install_gitea() {
  echo "==> install Gitea from ${GITEA_MANIFESTS_DIR_ABS}"
  apply_manifests "$GITEA_MANIFESTS_DIR_ABS" "${GITEA_MANIFESTS[@]}"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: wait for Gitea pods"
    return 0
  fi

  echo "==> wait for Gitea pods"
  kubectl_mgmt wait --for=condition=ready \
    "pod" -l app.kubernetes.io/name=gitea -n "$GITEA_NAMESPACE" \
    --timeout="$POD_READY_TIMEOUT"
  kubectl_mgmt get pods,svc -n "$GITEA_NAMESPACE"
  echo
  echo "Gitea UI: http://${GITEA_HOST}:${GITEA_PORT}"
  echo "Login:    ${GITEA_USER} / ${GITEA_PASSWORD}"
  echo
  echo "Recreate empty repos: ./bringup/02_configsync/add-gitea-repos.sh --include-mgmt"
}

uninstall_gitea() {
  echo "==> uninstall Gitea from ${GITEA_MANIFESTS_DIR_ABS}"
  delete_manifests "$GITEA_MANIFESTS_DIR_ABS" "${GITEA_MANIFESTS[@]}"

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  echo "PVCs kept (delete manually to wipe DB):"
  kubectl_mgmt get pvc -n "$GITEA_NAMESPACE" 2>/dev/null || echo "  (none)"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-storage)
      CLI_WITH_STORAGE=1
      shift
      ;;
    --uninstall|-u)
      ACTION="uninstall"
      shift
      ;;
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --kubeconfig)
      CLI_KUBECONFIG="$2"
      shift 2
      ;;
    --context)
      CLI_CONTEXT="$2"
      shift 2
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      load_config
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "error: unexpected argument '$1' (Gitea installs on mgmt only)" >&2
      usage >&2
      exit 1
      ;;
  esac
done

load_config

if [[ "$CLI_WITH_STORAGE" == "1" ]]; then
  WITH_STORAGE=1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "error: kubectl not found in PATH" >&2
  exit 1
fi

if [[ ! -d "$GITEA_MANIFESTS_DIR_ABS" ]]; then
  echo "error: manifests directory not found: ${GITEA_MANIFESTS_DIR_ABS}" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" ]]; then
  validate_kube_access
fi

if [[ "$ACTION" == "uninstall" ]]; then
  uninstall_gitea
  exit 0
fi

if [[ "${WITH_STORAGE:-0}" == "1" ]]; then
  apply_storage
fi

if [[ "$DRY_RUN" != "1" ]] && ! kubectl_mgmt get storageclass local-path >/dev/null 2>&1; then
  echo "error: StorageClass local-path missing — rerun with --with-storage" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" ]] && ! kubectl_mgmt get ipaddresspool -n metallb-system local-pool >/dev/null 2>&1; then
  echo "warning: MetalLB pool local-pool not found — run ./scripts/install_ip_pool.sh mgmt" >&2
fi

install_gitea
