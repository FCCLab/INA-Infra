#!/usr/bin/env bash
# Remove an external workload node from a cluster: drain/delete on CP, then kubeadm reset on the node.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRINGUP_CLUSTER="${BRINGUP_CLUSTER:-$REPO_ROOT/scripts/bringup_cluster.sh}"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NETPLAN_DIR="${NETPLAN_DIR:-$SCRIPT_DIR/netplan}"
NETPLAN_FILE="${NETPLAN_FILE:-55-k8s.yaml}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-120s}"

# shellcheck source=/dev/null
source "$BRINGUP_CLUSTER"

usage() {
  cat <<EOF
Usage: $(basename "$0") <cluster> <node>

Remove <node> from <cluster>:
  1. On control plane: cordon, drain, delete Node
  2. On <node>: kubeadm reset and clear local Kubernetes/CNI state

<node> must be reachable via SSH (utils/ssh_config/config).

Clusters: central, regional, edge, ue

Examples:
  $(basename "$0") edge gh81
  $(basename "$0") edge gh82
  $(basename "$0") edge usrp

Environment:
  DRAIN_TIMEOUT   kubectl drain --timeout (default: 120s)
  SSH_CONFIG      SSH config (default: utils/ssh_config/config)
  SUDO_PASSWORD   Optional sudo password if node lacks NOPASSWD
EOF
}

k8s_ip_from_netplan() {
  local file="$1"
  grep -E '^[[:space:]]*-[[:space:]]*10\.1\.137\.' "$file" | head -1 | awk '{print $2}' | sed 's#/24##'
}

resolve_external_k8s_node() {
  local cp_host="$1" worker="$2"
  local netplan_src node_ip k8s_name

  if kubectl_on_remote "$cp_host" get node "$worker" &>/dev/null; then
    printf '%s' "$worker"
    return 0
  fi

  netplan_src="${NETPLAN_DIR}/${worker}/${NETPLAN_FILE}"
  if [[ -f "$netplan_src" ]]; then
    node_ip="$(k8s_ip_from_netplan "$netplan_src")"
    if [[ -n "$node_ip" ]]; then
      k8s_name="$(k8s_node_for_ip "$cp_host" "$node_ip")"
      if [[ -n "$k8s_name" ]]; then
        printf '%s' "$k8s_name"
        return 0
      fi
    fi
  fi

  return 1
}

remove_node_from_api() {
  local cp_host="$1" k8s_node="$2"

  echo "==> [${cp_host}] cordon ${k8s_node}"
  remote_kubectl "$cp_host" cordon "$k8s_node" || true

  echo "==> [${cp_host}] drain ${k8s_node}"
  remote_kubectl "$cp_host" drain "$k8s_node" \
    --ignore-daemonsets \
    --delete-emptydir-data \
    --force \
    --timeout="$DRAIN_TIMEOUT" || true

  echo "==> [${cp_host}] delete node ${k8s_node}"
  remote_kubectl "$cp_host" delete node "$k8s_node" --wait=true || true
}

reset_worker_node() {
  local worker="$1"

  echo
  echo "========================================"
  echo " Disconnect node: ${worker}"
  echo "========================================"

  prompt_sudo_password "$worker"
  ensure_passwordless_sudo "$worker"

  local reset_body
  read -r -d '' reset_body <<'REMOTE' || true
set -euo pipefail

if command -v kubeadm >/dev/null 2>&1 && [[ -f /etc/kubernetes/kubelet.conf || -f /etc/kubernetes/admin.conf ]]; then
  echo "==> kubeadm reset"
  kubeadm reset -f
else
  echo "==> kubeadm not initialized (or already reset); cleaning leftovers"
fi

echo "==> remove CNI and kubelet state"
rm -rf /etc/cni/net.d /var/lib/cni /var/lib/kubelet/* /etc/kubernetes
rm -rf /root/.kube/config
rm -rf /home/*/.kube/config 2>/dev/null || true

echo "==> flush iptables (ignore errors)"
for cmd in \
  "iptables -F" "iptables -t nat -F" "iptables -t mangle -F" "iptables -X" \
  "ip6tables -F" "ip6tables -t nat -F" "ip6tables -t mangle -F" "ip6tables -X"; do
  $cmd 2>/dev/null || true
done

systemctl stop kubelet 2>/dev/null || true
systemctl disable kubelet 2>/dev/null || true
systemctl restart containerd 2>/dev/null || true

echo "==> done on $(hostname)"
REMOTE

  remote_sudo "$reset_body"
}

main() {
  local cluster="${1:-}" worker="${2:-}"
  local cp_host k8s_node

  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  if [[ -z "$cluster" || -z "$worker" ]]; then
    usage >&2
    exit 1
  fi

  if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
    echo "error: unknown cluster '${cluster}' (expected central, regional, edge, or ue)" >&2
    exit 1
  fi

  if [[ ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    exit 1
  fi

  if ! grep -qE "^Host ${worker}\$" "$SSH_CONFIG"; then
    echo "error: no SSH config entry for Host ${worker} in ${SSH_CONFIG}" >&2
    exit 1
  fi

  cp_host="${CLUSTER_CP_HOST[$cluster]}"

  if ! ssh_cmd "$cp_host" "test -f /etc/kubernetes/admin.conf" 2>/dev/null; then
    echo "error: control plane not initialized on ${cp_host}" >&2
    exit 1
  fi

  log_msg() { printf '==> %s\n' "$*"; }
  log_msg "cluster: ${cluster} (cp ${cp_host})"
  log_msg "worker:  ${worker}"

  if k8s_node="$(resolve_external_k8s_node "$cp_host" "$worker")"; then
    log_msg "k8s node object: ${k8s_node}"
    remove_node_from_api "$cp_host" "$k8s_node"
  else
    echo "warning: node '${worker}' not found in cluster API; skipping drain/delete" >&2
  fi

  reset_worker_node "$worker"

  echo
  echo "==> remaining nodes on ${cluster}:"
  remote_kubectl "$cp_host" get nodes -o wide || true
  echo "==> ${worker} removed from ${cluster}"
}

main "$@"
