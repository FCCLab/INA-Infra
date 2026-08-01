#!/usr/bin/env bash
# DynDNS site addressing on 10.1.137 (central).
#
# 1) /32 secondary on CLUSTER_DDNS_NODE site NIC (enp7s0):
#      central-0=.106
# 2) On this host (mgmt jump), host route so browsers hit the /32 via the
#    node's mgmt IP (10.1.132.x).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SITE_IFACE_DEFAULT="${SITE_IFACE:-enp7s0}"

ddns_mgmt_nexthop() {
  local cluster="$1" node
  node="${CLUSTER_DDNS_NODE[$cluster]:-}"
  case "$node" in
    central-0) printf '%s' "${CLUSTER_MGMT_IP[$cluster]}" ;;
    central-1) printf '%s' "${CLUSTER_MGMT_WORKER_IP[$cluster]}" ;;
    *)
      echo "error: no mgmt nexthop mapping for DDNS node '${node}'" >&2
      return 1
      ;;
  esac
}

ensure_vip_on_host() {
  local host="$1" vip="$2" iface="${3:-$SITE_IFACE_DEFAULT}"
  echo "==> ${host}: ensure ${vip}/32 on ${iface}"
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=15 "$host" bash -s <<EOF
set -euo pipefail
iface=${iface}
vip=${vip}
if ! ip -4 addr show dev "\$iface" | grep -q "inet \${vip}/"; then
  sudo ip addr add "\${vip}/32" dev "\$iface"
  echo "  added \${vip}/32"
else
  echo "  already present"
fi
ip -4 addr show dev "\$iface" | grep -E "inet .*(${vip}|137\\.)" || true
EOF
}

ensure_mgmt_route() {
  local vip="$1" via="$2"
  echo "==> local route ${vip}/32 via ${via}"
  if ip -4 route show "${vip}/32" 2>/dev/null | grep -q "via ${via}"; then
    echo "  already present"
  else
    sudo ip route replace "${vip}/32" via "${via}"
    echo "  installed"
  fi
  ip -4 route get "${vip}" || true
}

main() {
  local clusters=("$@") skip_routes=0
  if [[ ${#clusters[@]} -gt 0 && "${1:-}" == "--no-mgmt-routes" ]]; then
    skip_routes=1
    shift
    clusters=("$@")
  fi
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi

  for cluster in "${clusters[@]}"; do
    local host vip
    host="${CLUSTER_DDNS_NODE[$cluster]:-}"
    vip="$(ddns_vip "$cluster")"
    if [[ -z "$host" || -z "$vip" ]]; then
      echo "error: CLUSTER_DDNS_VIP/NODE unset for '${cluster}'" >&2
      exit 1
    fi
    ensure_vip_on_host "$host" "$vip"
  done

  if [[ "$skip_routes" -eq 0 ]]; then
    echo
    echo "==> mgmt-jump routes (this host)"
    for cluster in "${clusters[@]}"; do
      ensure_mgmt_route "$(ddns_vip "$cluster")" "$(ddns_mgmt_nexthop "$cluster")"
    done
  fi

  echo
  echo "DDNS URLs:"
  for cluster in "${clusters[@]}"; do
    echo "  ${cluster}  DNS $(ddns_vip "$cluster"):53"
    echo "  ${cluster}  UI  http://$(ddns_vip "$cluster"):8088/"
  done
}

main "$@"
