#!/usr/bin/env bash
# Remove imperative Flannel CNI from mgmt and workload clusters.
# Deploy CNI via GitOps (repos/) instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
FLANNEL_MANIFEST="${FLANNEL_MANIFEST:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Uninstall Flannel (kube-flannel namespace and cluster RBAC).
With no arguments, targets mgmt, central, regional, edge.

Warning: pod networking stops until another CNI is deployed via GitOps.

Examples:
  $(basename "$0")
  $(basename "$0") mgmt central

Environment:
  SSH_CONFIG        SSH config (default: utils/ssh_config/config)
  FLANNEL_MANIFEST  Manifest URL used for delete (default: upstream kube-flannel.yml)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  local_script="$(mktemp)"
  remote_script="/tmp/uninstall-flannel-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

uninstall_flannel_on_cluster() {
  local cluster="$1"
  local host manifest
  host="$(cluster_cp_host "$cluster")"
  manifest="$FLANNEL_MANIFEST"

  echo
  echo "========================================"
  echo " Uninstall Flannel: ${cluster}"
  echo " Control plane: ${host}"
  echo "========================================"

  run_remote_script "$host" <<EOF
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"
FLANNEL_MANIFEST='${manifest}'

if [[ ! -f "\$KUBECONFIG" ]]; then
  echo "error: missing \$KUBECONFIG" >&2
  exit 1
fi

if ! kubectl get namespace kube-flannel >/dev/null 2>&1; then
  echo "==> kube-flannel not present (already removed)"
else
  echo "==> delete Flannel manifest (if reachable)"
  if tmp=\$(mktemp) && curl -fsSL "\$FLANNEL_MANIFEST" -o "\$tmp" 2>/dev/null; then
    kubectl delete -f "\$tmp" --ignore-not-found --wait=false || true
    rm -f "\$tmp"
  else
    echo "    skip manifest delete (URL unreachable)"
  fi

  echo "==> delete kube-flannel namespace"
  kubectl delete namespace kube-flannel --ignore-not-found --wait --timeout=120s || true
fi

kubectl delete clusterrolebinding flannel --ignore-not-found
kubectl delete clusterrole flannel --ignore-not-found

if kubectl get namespace kube-flannel >/dev/null 2>&1; then
  echo "warning: kube-flannel stuck terminating — clearing finalizers"
  kubectl get namespace kube-flannel -o json \
    | python3 -c "import json,sys; ns=json.load(sys.stdin); ns.setdefault('spec',{})['finalizers']=[]; json.dump(ns, sys.stdout)" \
    | kubectl replace --raw "/api/v1/namespaces/kube-flannel/finalize" -f - >/dev/null 2>&1 || true
fi

if kubectl get namespace kube-flannel >/dev/null 2>&1; then
  echo "warning: kube-flannel still present" >&2
else
  echo "==> Flannel removed from cluster"
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
  if ! uninstall_flannel_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
