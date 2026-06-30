#!/usr/bin/env bash
# Deploy per-host site netplan (utils/netplan/<host>/60-nephio.yaml) to workload VMs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NETPLAN_DIR="${NETPLAN_DIR:-$REPO_ROOT/utils/netplan}"
NETPLAN_NAME="${NETPLAN_NAME:-60-nephio.yaml}"
REMOTE_NETPLAN="/etc/netplan/${NETPLAN_NAME}"

ALL_HOSTS=(
  central-0 central-1
  regional-0 regional-1
  edge-0 edge-1
  ue-0 ue-1
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [server-name ...]

Copy utils/netplan/<host>/${NETPLAN_NAME} to each host and run netplan apply.
With no arguments, deploys to all central, regional, edge, and ue nodes.

Mgmt stays in /etc/netplan/50-cloud-init.yaml (enp1s0).
Site NIC enp7s0 is defined in ${NETPLAN_NAME}.

Examples:
  $(basename "$0")
  $(basename "$0") central-0 regional-0

Environment:
  SSH_CONFIG    SSH config file (default: utils/ssh_config/config)
  NETPLAN_DIR   Source tree (default: utils/netplan)
  NETPLAN_NAME  Filename on remote (default: 60-nephio.yaml)
EOF
}

site_ip_from_file() {
  local file="$1"
  grep -E '^[[:space:]]*-[[:space:]]*10\.1\.137\.' "$file" | head -1 | awk '{print $2}' | tr -d '/24'
}

is_virtual_iface() {
  local ifc="$1"
  [[ "$ifc" =~ ^(flannel|cni|docker|br-|veth|cali|tunl|kube|virbr) ]]
}

remote_apply_netplan() {
  local netplan_src="$1"

  if [[ "$EUID" -ne 0 ]]; then
    echo "Please run as root (use sudo)." >&2
    exit 1
  fi

  local site_ip prefix=24 ifc
  site_ip="$(site_ip_from_file "$netplan_src")"
  if [[ -z "$site_ip" ]]; then
    echo "error: could not read site IP from ${netplan_src}" >&2
    exit 1
  fi

  echo "=== Applying ${REMOTE_NETPLAN} (site ${site_ip}/${prefix}) ==="

  rm -f /etc/netplan/99-nephio-site.yaml

  while IFS= read -r ifc; do
    ifc="${ifc%%@*}"
    [[ "$ifc" == "lo" || "$ifc" == "enp1s0" || "$ifc" == "enp7s0" ]] && continue
    if ip -4 -o addr show dev "$ifc" 2>/dev/null | grep -q "inet ${site_ip}/${prefix}"; then
      echo "Removing misplaced ${site_ip}/${prefix} from ${ifc}"
      ip addr del "${site_ip}/${prefix}" dev "$ifc" 2>/dev/null || true
    fi
  done < <(ip -o link show | awk -F': ' 'NR>1 {gsub(/^ /,"",$2); print $2}')

  install -m 600 "$netplan_src" "$REMOTE_NETPLAN"
  netplan apply

  echo "Addresses:"
  ip -4 -br addr show enp1s0 enp7s0 2>/dev/null || ip -4 -br addr
  echo "=== Done ==="
}

run_remote() {
  local server_name="$1"
  local local_file="${NETPLAN_DIR}/${server_name}/${NETPLAN_NAME}"
  local remote_src="/tmp/${NETPLAN_NAME}.$$"

  if [[ ! -f "$local_file" ]]; then
    echo "error: missing ${local_file}" >&2
    return 1
  fi

  echo ">>> ${server_name}"
  scp -q -F "$SSH_CONFIG" "$local_file" "${server_name}:${remote_src}"
  scp -q -F "$SSH_CONFIG" "$0" "${server_name}:/tmp/setup_ip.$$"
  ssh -F "$SSH_CONFIG" -t "$server_name" \
    "sudo SETUP_IP_REMOTE=1 bash /tmp/setup_ip.$$ '${remote_src}'; ec=\$?; rm -f /tmp/setup_ip.$$ '${remote_src}'; exit \$ec"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${SETUP_IP_REMOTE:-}" == "1" ]]; then
  if [[ -z "${1:-}" ]]; then
    echo "Usage: SETUP_IP_REMOTE=1 $0 <netplan-file>" >&2
    exit 1
  fi
  remote_apply_netplan "$1"
  exit 0
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

hosts=()
if [[ $# -eq 0 ]]; then
  hosts=("${ALL_HOSTS[@]}")
else
  hosts=("$@")
fi

failed=0
for server in "${hosts[@]}"; do
  if ! run_remote "$server"; then
    failed=1
  fi
done

exit "$failed"
