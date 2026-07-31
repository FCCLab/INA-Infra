#!/usr/bin/env bash
# OpenSpeedTest site addressing for UE/N6 path + mgmt-jump reachability.
#
# 1) /32 secondary on each OST node site NIC (enp7s0):
#      central-0=.101  regional-1=.102  edge-0=.103
# 2) On this host (mgmt jump), host routes so browsers hit those /32s via the
#    node's mgmt IP (10.1.132.x) — no SSH jump to a site VM required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SITE_IFACE_DEFAULT="${SITE_IFACE:-enp7s0}"

# OST node → mgmt-plane next hop for jump-host routes.
ost_mgmt_nexthop() {
  local cluster="$1" node
  node="${CLUSTER_OPENSPEEDTEST_NODE[$cluster]:-}"
  case "$node" in
    central-0|regional-0|edge-0) printf '%s' "${CLUSTER_MGMT_IP[$cluster]}" ;;
    central-1|regional-1|edge-1) printf '%s' "${CLUSTER_MGMT_WORKER_IP[$cluster]}" ;;
    *)
      echo "error: no mgmt nexthop mapping for OST node '${node}'" >&2
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

# Replace default "via 10.1.132.1" path with a direct route to the OST node.
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
    clusters=("${ALL_CLUSTERS[@]}")
  fi

  for cluster in "${clusters[@]}"; do
    local host vip
    host="${CLUSTER_OPENSPEEDTEST_NODE[$cluster]:-}"
    vip="$(openspeedtest_vip "$cluster")"
    if [[ -z "$host" || "$cluster" == "mgmt" ]]; then
      continue
    fi
    ensure_vip_on_host "$host" "$vip"
  done

  if [[ "$skip_routes" -eq 0 ]]; then
    echo
    echo "==> mgmt-jump routes (this host)"
    for cluster in "${clusters[@]}"; do
      [[ "$cluster" == "mgmt" ]] && continue
      ensure_mgmt_route "$(openspeedtest_vip "$cluster")" "$(ost_mgmt_nexthop "$cluster")"
    done
  fi

  echo
  echo "OST URLs (UE/N6 and mgmt browser after routes):"
  for cluster in "${clusters[@]}"; do
    [[ "$cluster" == "mgmt" ]] && continue
    echo "  ${cluster}  http://$(openspeedtest_vip "$cluster")/"
  done
}

main "$@"
