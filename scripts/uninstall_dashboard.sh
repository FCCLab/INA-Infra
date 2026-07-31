#!/usr/bin/env bash
# Remove imperative Kubernetes Dashboard from mgmt and workload clusters.
# Deploy Dashboard via GitOps (repos/) instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Uninstall Kubernetes Dashboard Helm release, LoadBalancer Service, and namespace.
With no arguments, targets mgmt, central, regional, edge.

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
  remote_script="/tmp/uninstall-dashboard-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

uninstall_dashboard_on_cluster() {
  local cluster="$1"
  local host
  host="$(cluster_cp_host "$cluster")"

  echo
  echo "========================================"
  echo " Uninstall Dashboard: ${cluster}"
  echo " Control plane: ${host}"
  echo "========================================"

  run_remote_script "$host" <<'EOF'
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"

if [[ ! -f "$KUBECONFIG" ]]; then
  echo "error: missing $KUBECONFIG" >&2
  exit 1
fi

if ! kubectl get namespace kubernetes-dashboard >/dev/null 2>&1; then
  echo "==> kubernetes-dashboard not present (already removed)"
else
  echo "==> delete Dashboard LoadBalancer service"
  kubectl delete svc kubernetes-dashboard-lb -n kubernetes-dashboard --ignore-not-found

  if command -v helm >/dev/null 2>&1; then
    if helm list -n kubernetes-dashboard -q 2>/dev/null | grep -qx kubernetes-dashboard; then
      echo "==> helm uninstall kubernetes-dashboard"
      helm uninstall kubernetes-dashboard -n kubernetes-dashboard --wait --timeout 5m || true
    else
      echo "==> no helm release kubernetes-dashboard"
    fi
  fi

  echo "==> delete remaining kubernetes-dashboard workloads"
  kubectl delete all -n kubernetes-dashboard --all --ignore-not-found --wait=false
fi

echo "==> delete admin-user ClusterRoleBinding"
kubectl delete clusterrolebinding admin-user --ignore-not-found

if kubectl get namespace kubernetes-dashboard >/dev/null 2>&1; then
  echo "==> delete kubernetes-dashboard namespace"
  kubectl delete namespace kubernetes-dashboard --ignore-not-found --wait --timeout=120s || true
fi

if kubectl get namespace kubernetes-dashboard >/dev/null 2>&1; then
  echo "warning: kubernetes-dashboard stuck terminating — clearing finalizers"
  kubectl get namespace kubernetes-dashboard -o json \
    | python3 -c "import json,sys; ns=json.load(sys.stdin); ns.setdefault('spec',{})['finalizers']=[]; json.dump(ns, sys.stdout)" \
    | kubectl replace --raw "/api/v1/namespaces/kubernetes-dashboard/finalize" -f - >/dev/null 2>&1 || true
fi

if kubectl get namespace kubernetes-dashboard >/dev/null 2>&1; then
  echo "warning: kubernetes-dashboard still present" >&2
else
  echo "==> Dashboard removed from cluster"
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
  clusters=("${ALL_BRINGUP_CLUSTERS[@]}")
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
  if ! uninstall_dashboard_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
