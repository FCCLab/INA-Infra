#!/usr/bin/env bash
# Print Kubernetes Dashboard login tokens for mgmt and workload clusters.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"


NAMESPACE="${DASHBOARD_NAMESPACE:-kubernetes-dashboard}"
SERVICE_ACCOUNT="${DASHBOARD_SA:-admin-user}"
TOKEN_DURATION="${TOKEN_DURATION:-24h}"

ALL_DASHBOARD_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Print bearer tokens for the Kubernetes Dashboard on each cluster.
With no cluster arguments, prints tokens for mgmt, central, regional, edge, and ue.

Options:
  -d, --duration DURATION   Token lifetime (default: ${TOKEN_DURATION})
  -n, --namespace NAME      Dashboard namespace (default: ${NAMESPACE})
  -s, --service-account SA  Service account (default: ${SERVICE_ACCOUNT})
  -h, --help                Show this help

Environment:
  TOKEN_DURATION
  DASHBOARD_NAMESPACE
  DASHBOARD_SA
  MGMT_DASHBOARD_VIP        mgmt dashboard VIP (default: ${MGMT_DASHBOARD_VIP})

Examples:
  $(basename "$0")
  $(basename "$0") central regional
  $(basename "$0") -d 8h mgmt
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

setup_kubeconfig() {
  local paths=()
  local cluster kcfg

  for cluster in "${ALL_DASHBOARD_CLUSTERS[@]}"; do
    kcfg="$(kubeconfig_path "$cluster")"
    if [[ -f "$kcfg" ]]; then
      paths+=("$kcfg")
    fi
  done

  if [[ ${#paths[@]} -eq 0 ]]; then
    echo "error: no kubeconfig files found under ~/.kube" >&2
    exit 1
  fi

  export KUBECONFIG
  KUBECONFIG="$(IFS=:; echo "${paths[*]}")"
}

kubectl_ctx() {
  local cluster="$1"
  shift
  kubectl --context="$(dashboard_context "$cluster")" "$@"
}

print_dashboard_key() {
  local cluster="$1"
  local ctx kcfg vip

  ctx="$(dashboard_context "$cluster")"
  kcfg="$(kubeconfig_path "$cluster")"
  vip="$(dashboard_vip "$cluster")"

  echo "========================================"
  echo " Cluster: ${cluster}"
  echo " URL:     https://${vip}"
  echo " Context: ${ctx}"
  echo " Config:  ${kcfg}"
  echo "========================================"

  if [[ ! -f "$kcfg" ]]; then
    echo "error: missing kubeconfig: ${kcfg}" >&2
    return 1
  fi

  if ! kubectl_ctx "$cluster" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "error: namespace ${NAMESPACE} not found (install dashboard first)" >&2
    return 1
  fi

  if ! kubectl_ctx "$cluster" -n "$NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
    echo "error: service account ${SERVICE_ACCOUNT} not found in ${NAMESPACE}" >&2
    return 1
  fi

  echo "Service account: ${SERVICE_ACCOUNT}"
  echo "Token (paste into the dashboard login page):"
  echo
  kubectl_ctx "$cluster" -n "$NAMESPACE" create token "$SERVICE_ACCOUNT" --duration="$TOKEN_DURATION"
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

setup_kubeconfig

failed=0
for cluster in "${clusters[@]}"; do
  if ! print_dashboard_key "$cluster"; then
    failed=1
  fi
done

exit "$failed"
