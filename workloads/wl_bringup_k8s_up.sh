#!/usr/bin/env bash
# Install Kubernetes on an external workload node and join it to a cluster control plane.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRINGUP_CLUSTER="${BRINGUP_CLUSTER:-$REPO_ROOT/scripts/bringup_cluster.sh}"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NETPLAN_DIR="${NETPLAN_DIR:-$SCRIPT_DIR/netplan}"
NETPLAN_FILE="${NETPLAN_FILE:-55-k8s.yaml}"

# shellcheck source=/dev/null
source "$BRINGUP_CLUSTER"

usage() {
  cat <<EOF
Usage: $(basename "$0") <cluster> <node>

Install kubelet/kubeadm/kubectl on <node> and join it to <cluster>'s control plane.
<node> must already be reachable via SSH (see wl_setup_ssh_mgmt_ip.sh) with netplan applied.

Clusters: central, regional, edge, ue

Examples:
  $(basename "$0") edge gh81
  $(basename "$0") edge gh82

The Kubernetes node IP (kubelet --node-ip) is read from:
  ${NETPLAN_DIR}/<node>/${NETPLAN_FILE}  (10.1.137.x address)

Environment: same as scripts/bringup_cluster.sh (K8S_VERSION, DNS_SERVER, INSTALL_FLANNEL, ...)
Keeps Docker CE (docker-ce, containerd.io, buildx, compose); configures containerd CRI for kubelet.
EOF
}

k8s_ip_from_netplan() {
  local file="$1"
  grep -E '^[[:space:]]*-[[:space:]]*10\.1\.137\.' "$file" | head -1 | awk '{print $2}' | sed 's#/24##'
}

main() {
  local cluster="${1:-}" worker="${2:-}"
  local netplan_src node_ip

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
    echo "Run wl_setup_ssh_mgmt_ip.sh ${worker} <ip> first." >&2
    exit 1
  fi

  netplan_src="${NETPLAN_DIR}/${worker}/${NETPLAN_FILE}"
  if [[ ! -f "$netplan_src" ]]; then
    echo "error: missing ${netplan_src}" >&2
    exit 1
  fi

  node_ip="$(k8s_ip_from_netplan "$netplan_src")"
  if [[ -z "$node_ip" ]]; then
    echo "error: could not read k8s node IP (10.1.137.x) from ${netplan_src}" >&2
    exit 1
  fi

  log_msg() { printf '==> %s\n' "$*"; }
  log_msg "cluster: ${cluster} (cp ${CLUSTER_CP_HOST[$cluster]})"
  log_msg "worker:  ${worker} (node-ip ${node_ip})"

  if ! join_external_worker "$cluster" "$worker" "$node_ip"; then
    exit 1
  fi
}

main "$@"
