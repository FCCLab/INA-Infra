#!/usr/bin/env bash
# Remove imperative MetalLB installs from mgmt and workload clusters.
# Deploy MetalLB via GitOps (repos/) instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Uninstall MetalLB Helm release, IPAddressPool, and metallb-system namespace.
With no arguments, targets mgmt, central, regional, edge.

MetalLB LoadBalancer VIPs will stop working until redeployed via GitOps.

Examples:
  $(basename "$0")
  $(basename "$0") mgmt central

Environment:
  SSH_CONFIG   SSH config (default: utils/ssh_config/config)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  local_script="$(mktemp)"
  remote_script="/tmp/uninstall-metallb-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

uninstall_metallb_on_cluster() {
  local cluster="$1"
  local host
  host="$(cluster_cp_host "$cluster")"

  echo
  echo "========================================"
  echo " Uninstall MetalLB: ${cluster}"
  echo " Control plane: ${host}"
  echo "========================================"

  run_remote_script "$host" <<'EOF'
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"

if [[ ! -f "$KUBECONFIG" ]]; then
  echo "error: missing $KUBECONFIG" >&2
  exit 1
fi

if ! kubectl get namespace metallb-system >/dev/null 2>&1; then
  echo "==> metallb-system not present (already removed)"
  exit 0
fi

echo "==> delete IPAddressPool and L2Advertisement"
kubectl delete ipaddresspool,l2advertisement -n metallb-system --all --ignore-not-found

if command -v helm >/dev/null 2>&1; then
  if helm list -n metallb-system -q 2>/dev/null | grep -qx metallb; then
    echo "==> helm uninstall metallb"
    helm uninstall metallb -n metallb-system --wait --timeout 5m || true
  else
    echo "==> no helm release metallb (skipping helm uninstall)"
  fi
fi

echo "==> delete remaining metallb-system workloads"
kubectl delete all -n metallb-system --all --ignore-not-found --wait=false

echo "==> delete metallb-system namespace"
if kubectl get namespace metallb-system >/dev/null 2>&1; then
  kubectl delete namespace metallb-system --ignore-not-found --wait --timeout=120s || {
    echo "warning: namespace stuck terminating — clearing finalizers"
    kubectl patch namespace metallb-system -p '{"metadata":{"finalizers":[]}}' --type=merge || true
    kubectl wait --for=delete namespace/metallb-system --timeout=60s 2>/dev/null || true
  }
fi

if kubectl get namespace metallb-system >/dev/null 2>&1; then
  echo "warning: metallb-system still present" >&2
else
  echo "==> MetalLB removed from cluster"
fi
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_METALLB_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    case "$cluster" in
      mgmt) ;;
      *)
        if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
          echo "error: unknown cluster '${cluster}'" >&2
          exit 1
        fi
        ;;
    esac
    clusters+=("$cluster")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! uninstall_metallb_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
