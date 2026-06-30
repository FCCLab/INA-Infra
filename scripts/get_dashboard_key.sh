#!/usr/bin/env bash
# Print Kubernetes Dashboard login tokens for mgmt and workload clusters.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_OPTS=(-F "$SSH_CONFIG" -o ConnectTimeout=10 -o RequestTTY=no)
KUBECTL_TIMEOUT="${KUBECTL_TIMEOUT:-15s}"

NAMESPACE="${DASHBOARD_NAMESPACE:-kubernetes-dashboard}"
SERVICE_ACCOUNT="${DASHBOARD_SA:-admin-user}"
TOKEN_DURATION="${TOKEN_DURATION:-24h}"

ALL_DASHBOARD_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Print bearer tokens for the Kubernetes Dashboard on each cluster.
With no cluster arguments, prints tokens for mgmt, central, regional, edge, and ue.

Workload clusters use SSH to the control plane (API is on 10.1.137.x, not
reachable from the operator network on 10.1.132.x).

Options:
  -d, --duration DURATION   Token lifetime (default: ${TOKEN_DURATION})
  -n, --namespace NAME      Dashboard namespace (default: ${NAMESPACE})
  -s, --service-account SA  Service account (default: ${SERVICE_ACCOUNT})
  -h, --help                Show this help

Environment:
  TOKEN_DURATION
  DASHBOARD_NAMESPACE
  DASHBOARD_SA
  KUBECTL_TIMEOUT           Local kubectl timeout (default: 15s; mgmt only)
  SSH_CONFIG
  DASHBOARD_FORWARD_PORT    Port on mgmt IP for workload dashboards (default: 8443)

Examples:
  $(basename "$0")
  $(basename "$0") central regional
  $(basename "$0") -d 8h mgmt

Workload dashboards on 132: run ${SCRIPT_DIR}/kubectl_forward.sh first.
EOF
}

kubeconfig_path() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "${HOME}/.kube/config"
  else
    printf '%s' "${HOME}/.kube/$(kubeconfig_file "$cluster")"
  fi
}

dashboard_context() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf 'mgmt@mgmt'
  else
    kube_context "$cluster"
  fi
}

kubectl_cluster() {
  local cluster="$1"
  shift
  if [[ "$cluster" == "mgmt" ]]; then
    kubectl --context="$(dashboard_context "$cluster")" --request-timeout="$KUBECTL_TIMEOUT" "$@"
  else
    local host
    host="$(cluster_cp_host "$cluster")"
    ssh "${SSH_OPTS[@]}" "$host" "kubectl $(printf '%q ' "$@")"
  fi
}

print_dashboard_key() {
  local cluster="$1"
  local ctx kcfg operator_url site_vip cp_host

  ctx="$(dashboard_context "$cluster")"
  kcfg="$(kubeconfig_path "$cluster")"
  operator_url="$(dashboard_operator_url "$cluster")"
  site_vip="$(dashboard_vip "$cluster")"
  cp_host="$(cluster_cp_host "$cluster")"

  echo "========================================"
  echo " Cluster: ${cluster}"
  echo " URL:     ${operator_url}"
  if [[ "$cluster" != "mgmt" ]]; then
    echo " Site:    https://${site_vip}  (137 network; start ${SCRIPT_DIR}/kubectl_forward.sh if needed)"
  fi
  echo " Context: ${ctx}"
  if [[ "$cluster" == "mgmt" ]]; then
    echo " Config:  ${kcfg}"
  else
    echo " Config:  ${cp_host}:~/.kube/config (via SSH)"
  fi
  echo "========================================"

  if [[ "$cluster" == "mgmt" && ! -f "$kcfg" ]]; then
    echo "error: missing kubeconfig: ${kcfg}" >&2
    return 1
  fi

  if ! kubectl_cluster "$cluster" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "error: namespace ${NAMESPACE} not found (install dashboard first)" >&2
    return 1
  fi

  if ! kubectl_cluster "$cluster" -n "$NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
    echo "error: service account ${SERVICE_ACCOUNT} not found in ${NAMESPACE}" >&2
    return 1
  fi

  echo "Service account: ${SERVICE_ACCOUNT}"
  echo "Token (paste into the dashboard login page):"
  echo
  kubectl_cluster "$cluster" -n "$NAMESPACE" create token "$SERVICE_ACCOUNT" --duration="$TOKEN_DURATION"
  echo
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--duration)
      TOKEN_DURATION="$2"
      shift 2
      ;;
    -n|--namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    -s|--service-account)
      SERVICE_ACCOUNT="$2"
      shift 2
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
  clusters=("${ALL_DASHBOARD_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    case "$cluster" in
      mgmt) ;;
      *)
        if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
          echo "error: unknown cluster '${cluster}' (expected mgmt, central, regional, edge, or ue)" >&2
          exit 1
        fi
        ;;
    esac
    clusters+=("$cluster")
  done
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "error: kubectl not found in PATH" >&2
  exit 1
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! print_dashboard_key "$cluster"; then
    failed=1
  fi
done

exit "$failed"
