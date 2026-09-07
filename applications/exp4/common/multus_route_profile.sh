# Multus reply-path profile.
# Dual Multus (`exp4_deploy.sh`):
#   net1 simulated 5G  10.140.<s>.0/24  table 140  (client default via server .1)
#   net2 console       10.1.137.0/24    table 137  default via site GW
#
#   source this file, then:
#     exp4_multus_route_profile <iface> <ip> [gw] [subnet] [table]
# Empty gw skips the default route.
#
# Requires NET_ADMIN (initContainer or container capability).
MULTUS_ROUTE_TABLE="${MULTUS_ROUTE_TABLE:-137}"
MULTUS_SUBNET="${MULTUS_SUBNET:-10.1.137.0/24}"
MULTUS_GW="${MULTUS_GW:-${GW:-10.1.137.1}}"

exp4_multus_route_profile() {
  local iface="${1:-net1}"
  local ip="$2"
  local gw="${3-}"
  local subnet="${4:-$MULTUS_SUBNET}"
  local table="${5:-$MULTUS_ROUTE_TABLE}"
  local i

  [ -z "$iface" ] && return 0
  for i in $(seq 1 40); do
    ip link show "$iface" >/dev/null 2>&1 && break
    sleep 1
  done
  if ! ip link show "$iface" >/dev/null 2>&1; then
    echo "[exp4-multus-route] ${iface} missing" >&2
    return 1
  fi

  if [ -z "$ip" ]; then
    ip="$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  fi
  if [ -z "$ip" ]; then
    echo "[exp4-multus-route] no IPv4 on ${iface}" >&2
    return 1
  fi

  [ -z "$gw" ] && [ "${3+x}" != "x" ] && gw="$MULTUS_GW"

  ip route replace "${subnet}" dev "$iface" || true
  ip route replace "${subnet}" dev "$iface" table "${table}" || true
  if [ -n "$gw" ]; then
    ip route replace default via "$gw" dev "$iface" table "${table}" || true
  fi
  ip rule del from "${ip}/32" table "${table}" 2>/dev/null || true
  ip rule add from "${ip}/32" table "${table}" priority 100

  echo "[exp4-multus-route] from ${ip}/32 lookup table ${table} (${subnet}${gw:+ default via ${gw}} dev ${iface})"
}
