#!/usr/bin/env bash
# Grafana site addressing cleanup on 10.1.137 (edge).
#
# Grafana now uses Multus macvlan (.105/24 on the pod). Host /32 secondaries
# and mgmt-jump /32 routes via the node would hairpin and conflict — remove them
# so traffic uses the site gateway path (same as UPF N6).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SITE_IFACE_DEFAULT="${SITE_IFACE:-enp7s0}"

remove_vip_on_host() {
  local host="$1" vip="$2" iface="${3:-$SITE_IFACE_DEFAULT}"
  echo "==> ${host}: remove ${vip}/32 from ${iface} (Multus owns this IP)"
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=15 "$host" bash -s <<EOF
set -euo pipefail
iface=${iface}
vip=${vip}
if ip -4 addr show dev "\$iface" | grep -q "inet \${vip}/"; then
  sudo ip addr del "\${vip}/32" dev "\$iface" 2>/dev/null \
    || sudo ip addr del "\${vip}/24" dev "\$iface" 2>/dev/null \
    || true
  echo "  removed"
else
  echo "  not present"
fi
ip -4 addr show dev "\$iface" | grep -E "inet .*(${vip}|137\\.)" || true
EOF
}

# Prefer site gateway (10.1.132.1) — same path as UPF N6. Do not route via
# edge-0 mgmt (macvlan hairpin) or edge-1 (forwarding without return route).
ensure_mgmt_route_via_site_gw() {
  local vip="$1"
  local via="${SITE_MGMT_GW:-10.1.132.1}"
  echo "==> local: ${vip}/32 via ${via} (site gw, not node hairpin)"
  if ip -4 route show "${vip}/32" 2>/dev/null | grep -q .; then
    sudo ip route del "${vip}/32" 2>/dev/null || true
  fi
  sudo ip route replace "${vip}/32" via "${via}"
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
    clusters=(edge)
  fi

  for cluster in "${clusters[@]}"; do
    local host vip
    host="${CLUSTER_GRAFANA_NODE[$cluster]:-}"
    vip="$(grafana_vip "$cluster")"
    if [[ -z "$host" || -z "$vip" ]]; then
      echo "error: CLUSTER_GRAFANA_VIP/NODE unset for '${cluster}'" >&2
      exit 1
    fi
    remove_vip_on_host "$host" "$vip"
  done

  if [[ "$skip_routes" -eq 0 ]]; then
    echo
    echo "==> mgmt-jump /32 routes via site gw"
    for cluster in "${clusters[@]}"; do
      ensure_mgmt_route_via_site_gw "$(grafana_vip "$cluster")"
    done
  fi

  echo
  echo "Grafana URLs (Multus macvlan):"
  for cluster in "${clusters[@]}"; do
    echo "  ${cluster}  http://$(grafana_vip "$cluster"):3000/"
  done
}

main "$@"
