#!/usr/bin/env bash
# Deploy per-host netplan (55-nephio-mgmt.yaml; + 60-nephio.yaml on workload VMs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NETPLAN_DIR="${NETPLAN_DIR:-$REPO_ROOT/utils/netplan}"

NETPLAN_MGMT="${NETPLAN_MGMT:-55-nephio-mgmt.yaml}"
NETPLAN_SITE="${NETPLAN_SITE:-60-nephio.yaml}"
MGMT_GATEWAY="${MGMT_GATEWAY:-10.1.132.1}"

ALL_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1
  regional-0 regional-1
  edge-0 edge-1

)

MGMT_ONLY_HOSTS=(mgmt-0 mgmt-1)

usage() {
  cat <<EOF
Usage: $(basename "$0") [server-name ...]

Copy utils/netplan/<host>/${NETPLAN_MGMT} (and ${NETPLAN_SITE} when present) to each
host and run netplan apply. With no arguments, deploys to all testbed nodes.

${NETPLAN_MGMT}  enp1s0 mgmt IP, default via ${MGMT_GATEWAY}, DNS 8.8.8.8
${NETPLAN_SITE}  enp7s0 site IPs on 10.1.137.0/24 (K8s) + 10.1.138.0/24 (MetalLB)
                 workload nodes only (mgmt-0/mgmt-1 have no site NIC)

Examples:
  $(basename "$0")
  $(basename "$0") mgmt-0 mgmt-1
  $(basename "$0") regional-0 regional-1

Environment:
  SSH_CONFIG     SSH config file (default: utils/ssh_config/config)
  NETPLAN_DIR    Source tree (default: utils/netplan)
  MGMT_GATEWAY   Default route gateway (default: 10.1.132.1)
EOF
}

site_ip_from_file() {
  local file="$1"
  grep -E '^[[:space:]]*-[[:space:]]*10\.1\.137\.' "$file" | head -1 | awk '{print $2}' | sed 's#/24##'
}

sync_resolv_conf() {
  local mgmt_src="$1"
  local dns
  dns="$(grep -A3 'nameservers:' "$mgmt_src" | grep -E '^[[:space:]]*-' | head -1 | awk '{print $2}')"
  if [[ -z "$dns" ]]; then
    dns="8.8.8.8"
  fi
  rm -f /etc/resolv.conf
  printf 'nameserver %s\n' "$dns" > /etc/resolv.conf
  chmod 644 /etc/resolv.conf
  echo "resolv.conf: nameserver ${dns}"
}

is_mgmt_only_host() {
  local host="$1" h
  for h in "${MGMT_ONLY_HOSTS[@]}"; do
    [[ "$host" == "$h" ]] && return 0
  done
  return 1
}

remote_apply_netplan_mgmt() {
  local mgmt_src="$1"

  if [[ "$EUID" -ne 0 ]]; then
    echo "Please run as root (use sudo)." >&2
    exit 1
  fi

  echo "=== Applying mgmt netplan (default via ${MGMT_GATEWAY}) ==="

  install -m 600 "$mgmt_src" "/etc/netplan/${NETPLAN_MGMT}"
  chmod 600 /etc/netplan/*.yaml 2>/dev/null || true
  netplan apply
  sync_resolv_conf "$mgmt_src"

  echo "Routes:"
  ip -4 route show default || true
  echo "Addresses:"
  ip -4 -br addr show enp1s0 2>/dev/null || ip -4 -br addr
  echo "=== Done ==="
}

remote_apply_netplan() {
  local mgmt_src="$1"
  local site_src="$2"

  if [[ "$EUID" -ne 0 ]]; then
    echo "Please run as root (use sudo)." >&2
    exit 1
  fi

  local site_ip prefix=24 ifc
  site_ip="$(site_ip_from_file "$site_src")"
  if [[ -z "$site_ip" ]]; then
    echo "error: could not read site IP from ${site_src}" >&2
    exit 1
  fi

  echo "=== Applying netplan (mgmt default via ${MGMT_GATEWAY}, site ${site_ip}/${prefix}) ==="

  rm -f /etc/netplan/99-nephio-site.yaml

  while IFS= read -r ifc; do
    ifc="${ifc%%@*}"
    [[ "$ifc" == "lo" || "$ifc" == "enp1s0" || "$ifc" == "enp7s0" ]] && continue
    if ip -4 -o addr show dev "$ifc" 2>/dev/null | grep -q "inet ${site_ip}/${prefix}"; then
      echo "Removing misplaced ${site_ip}/${prefix} from ${ifc}"
      ip addr del "${site_ip}/${prefix}" dev "$ifc" 2>/dev/null || true
    fi
  done < <(ip -o link show | awk -F': ' 'NR>1 {gsub(/^ /,"",$2); print $2}')

  install -m 600 "$mgmt_src" "/etc/netplan/${NETPLAN_MGMT}"
  install -m 600 "$site_src" "/etc/netplan/${NETPLAN_SITE}"
  chmod 600 /etc/netplan/*.yaml 2>/dev/null || true
  netplan apply
  sync_resolv_conf "$mgmt_src"

  echo "Routes:"
  ip -4 route show default || true
  echo "Addresses:"
  ip -4 -br addr show enp1s0 enp7s0 2>/dev/null || ip -4 -br addr
  echo "=== Done ==="
}

run_remote() {
  local server_name="$1"
  local mgmt_file="${NETPLAN_DIR}/${server_name}/${NETPLAN_MGMT}"
  local site_file="${NETPLAN_DIR}/${server_name}/${NETPLAN_SITE}"
  local remote_mgmt="/tmp/${NETPLAN_MGMT}.$$"
  local remote_site="/tmp/${NETPLAN_SITE}.$$"

  if [[ ! -f "$mgmt_file" ]]; then
    echo "error: missing ${mgmt_file}" >&2
    return 1
  fi

  echo ">>> ${server_name}"
  scp -q -F "$SSH_CONFIG" "$mgmt_file" "${server_name}:${remote_mgmt}"
  scp -q -F "$SSH_CONFIG" "$0" "${server_name}:/tmp/setup_ip.$$"

  if is_mgmt_only_host "$server_name" || [[ ! -f "$site_file" ]]; then
    if ! ssh -F "$SSH_CONFIG" -t "$server_name" \
      "sudo SETUP_IP_REMOTE=1 SETUP_IP_MGMT_ONLY=1 bash /tmp/setup_ip.$$ '${remote_mgmt}'; ec=\$?; rm -f /tmp/setup_ip.$$ '${remote_mgmt}'; exit \$ec"; then
      return 1
    fi
    return 0
  fi

  scp -q -F "$SSH_CONFIG" "$site_file" "${server_name}:${remote_site}"
  if ! ssh -F "$SSH_CONFIG" -t "$server_name" \
    "sudo SETUP_IP_REMOTE=1 bash /tmp/setup_ip.$$ '${remote_mgmt}' '${remote_site}'; ec=\$?; rm -f /tmp/setup_ip.$$ '${remote_mgmt}' '${remote_site}'; exit \$ec"; then
    return 1
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${SETUP_IP_REMOTE:-}" == "1" ]]; then
  if [[ "${SETUP_IP_MGMT_ONLY:-}" == "1" ]]; then
    if [[ $# -lt 1 ]]; then
      echo "Usage: SETUP_IP_REMOTE=1 SETUP_IP_MGMT_ONLY=1 $0 <mgmt-netplan>" >&2
      exit 1
    fi
    remote_apply_netplan_mgmt "$1"
    exit 0
  fi
  if [[ $# -lt 2 ]]; then
    echo "Usage: SETUP_IP_REMOTE=1 $0 <mgmt-netplan> <site-netplan>" >&2
    exit 1
  fi
  remote_apply_netplan "$1" "$2"
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
