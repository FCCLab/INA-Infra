#!/usr/bin/env bash
# Single entry point for Nephio workload cluster registration on mgmt:
# WorkloadCluster CRs (configsync/api/workloadclusters.yaml) + kubeconfig secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
API_DIR="${API_DIR:-$SCRIPT_DIR/api}"
MGMT_CTX="${MGMT_CTX:-mgmt@mgmt}"
WORKLOAD_NAMESPACE="${WORKLOAD_NAMESPACE:-default}"
FETCH_KUBECONFIG=0
DRY_RUN=0
# TCP dial to 10.1.137.x can hang past kubectl --request-timeout from mgmt/132 hosts.
KUBECTL_PROBE_TIMEOUT="${KUBECTL_PROBE_TIMEOUT:-5}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Register workload clusters on the mgmt Nephio control plane (step E in central/readme.md).
Applies WorkloadCluster CRs once, then creates {cluster}-kubeconfig secrets.

Default (no args): central, regional, edge, ue.

Prerequisites:
  - Nephio operator CRDs on mgmt
  - Workload cluster kubeadm Ready (./scripts/bringup_cluster.sh)
  - Local kubeconfig with context {cluster}@{cluster}, or use --fetch

Options:
  --fetch         SCP kubeconfig from each control plane before creating secrets
  -n, --dry-run   Print actions only
  -h, --help      Show this help

Environment:
  MGMT_CTX              mgmt kubectl context (default: mgmt@mgmt)
  WORKLOAD_NAMESPACE    WorkloadCluster namespace (default: default)
  SSH_CONFIG            SSH config for --fetch (default: utils/ssh_config/config)

Site types (nephio.org/site-type):
  central=core  regional=regional  edge=edge  ue=ue

Examples:
  $(basename "$0")
  $(basename "$0") --fetch central regional
  $(basename "$0") -n

Verify:
  kubectl --context=${MGMT_CTX} get workloadclusters
  kubectl --context=${MGMT_CTX} get secrets | grep kubeconfig

Next: ./configsync/setup_cluster_repos.sh
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
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

ensure_local_kubeconfig() {
  local cluster="$1"
  local kcfg host ctx
  kcfg="$(local_kubeconfig_path "$cluster")"
  ctx="$(kube_context "$cluster")"

  if [[ "$FETCH_KUBECONFIG" == "1" ]]; then
    host="$(cluster_cp_host "$cluster")"
    echo "==> [${cluster}] fetch kubeconfig from ${host}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "    dry-run: scp ${host}:.kube/config -> ${kcfg}"
      return 0
    fi
    mkdir -p "${HOME}/.kube"
    scp -F "$SSH_CONFIG" -q "${host}:.kube/config" "$kcfg"
    chmod 600 "$kcfg"
    "$REPO_ROOT/scripts/rename.sh" "$cluster" "$cluster" "$kcfg"
  fi

  if [[ ! -f "$kcfg" ]]; then
    echo "error: [${cluster}] missing kubeconfig ${kcfg} (run bringup or --fetch)" >&2
    return 1
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  if ! kubectl --kubeconfig="$kcfg" config get-contexts "$ctx" >/dev/null 2>&1; then
    echo "error: [${cluster}] context ${ctx} not found in ${kcfg}" >&2
    echo "       fix: $REPO_ROOT/scripts/rename.sh ${cluster} ${cluster} ${kcfg}" >&2
    return 1
  fi

  # With --fetch the kubeconfig is for Nephio on mgmt; this host often has no 137 route.
  if [[ "$FETCH_KUBECONFIG" == "1" ]]; then
    echo "    kubeconfig ready (skip API probe from this host with --fetch)"
    return 0
  fi

  if command -v timeout >/dev/null 2>&1; then
    if ! timeout "$KUBECTL_PROBE_TIMEOUT" kubectl --kubeconfig="$kcfg" --context="$ctx" get nodes >/dev/null 2>&1; then
      echo "warning: [${cluster}] cannot reach API via ${kcfg} (137 network? run from CP or use --fetch)" >&2
    fi
  elif ! kubectl --kubeconfig="$kcfg" --context="$ctx" --request-timeout="${KUBECTL_PROBE_TIMEOUT}s" get nodes >/dev/null 2>&1; then
    echo "warning: [${cluster}] cannot reach API via ${kcfg} (137 network? run from CP or use --fetch)" >&2
  fi
}

apply_workload_clusters() {
  local manifest="${API_DIR}/workloadclusters.yaml"
  echo "==> apply WorkloadCluster (all) from ${manifest}"
  if [[ ! -f "$manifest" ]]; then
    echo "error: missing manifest ${manifest}" >&2
    return 1
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl apply -f ${manifest}"
    return 0
  fi
  kubectl_mgmt apply -f "$manifest"
}

apply_kubeconfig_secret() {
  local cluster="$1"
  local kcfg secret_name
  kcfg="$(local_kubeconfig_path "$cluster")"
  secret_name="$(cluster_kubeconfig_secret_name "$cluster")"

  echo "==> [${cluster}] secret ${secret_name} from ${kcfg}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: create secret ${secret_name} in ${WORKLOAD_NAMESPACE}"
    return 0
  fi

  kubectl_mgmt create secret generic "$secret_name" \
    --namespace="$WORKLOAD_NAMESPACE" \
    --from-file=value="$kcfg" \
    --dry-run=client -o yaml | kubectl_mgmt apply -f -
}

register_cluster() {
  local cluster="$1"
  ensure_local_kubeconfig "$cluster" || return 1
  apply_kubeconfig_secret "$cluster" || return 1
  echo
}

print_summary() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi
  echo "Registered on mgmt (${MGMT_CTX}):"
  kubectl_mgmt get workloadclusters -n "$WORKLOAD_NAMESPACE" 2>/dev/null || true
  echo
  kubectl_mgmt get secrets -n "$WORKLOAD_NAMESPACE" 2>/dev/null | grep kubeconfig || true
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fetch)
      FETCH_KUBECONFIG=1
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

if ! command -v kubectl >/dev/null 2>&1; then
  echo "error: kubectl not found" >&2
  exit 1
fi

if [[ "$FETCH_KUBECONFIG" == "1" && ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" ]]; then
  if ! kubectl_mgmt get crd workloadclusters.infra.nephio.org >/dev/null 2>&1; then
    echo "error: WorkloadCluster CRD not found on mgmt — install nephio-operator first" >&2
    exit 1
  fi
fi

echo "Register workload clusters on mgmt (${MGMT_CTX}): ${clusters[*]}"
echo

failed=0
if ! apply_workload_clusters; then
  failed=1
fi

for cluster in "${clusters[@]}"; do
  if ! register_cluster "$cluster"; then
    failed=1
  fi
done

print_summary
exit "$failed"
