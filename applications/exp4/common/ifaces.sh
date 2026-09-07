# Exp4 dual-iface helpers. Source from server/client entrypoints.
#
# Dual Multus (`exp4_deploy.sh`, SCHEME_ID=exp4-no5g):
#   Server + client:
#     TO_*_IFACE=net1   simulated 5G  10.140.<s>.x
#     CONSOLE_IFACE=net2  site console 10.1.137.x
# With real 5G later:
#   Server: TO_CLIENT_IFACE=net1 (N6)   CONSOLE_IFACE=eth0
#   Client: TO_SERVER_IFACE=oaitun_ue*  CONSOLE_IFACE=net1
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"

exp4_no5g() {
  case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
    *exp4-no5g*|1|true|True|yes|YES) return 0 ;;
  esac
  return 1
}

exp4_log() {
  printf '{"ts":%s,"level":"%s","event":"iface","msg":"%s"}\n' \
    "$(date +%s)" "$1" "$2"
}

exp4_wait_iface() {
  local iface="$1" elapsed=0
  [ -z "$iface" ] && return 0
  while ! ip link show "$iface" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$IFACE_TIMEOUT" ]; then
      exp4_log warn "interface ${iface} missing after ${IFACE_TIMEOUT}s"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  while ! ip -4 addr show dev "$iface" 2>/dev/null | grep -q 'inet '; do
    if [ "$elapsed" -ge "$IFACE_TIMEOUT" ]; then
      exp4_log warn "interface ${iface} has no IPv4 after ${IFACE_TIMEOUT}s"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  exp4_log info "interface ${iface} ready"
}

exp4_announce() {
  local iface="$1" ip="$2"
  [ -z "$iface" ] || [ -z "$ip" ] && return 0
  ip link show "$iface" >/dev/null 2>&1 || return 0
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I "$iface" "$ip" >/dev/null 2>&1 || true
  fi
}

# Reply-path profile.
#   exp4_multus_route_profile <iface> <ip> [gw] [subnet] [table]
# Empty gw skips the default route. Client sim5G uses TARGET_SERVER_IP as GW.
exp4_multus_route_profile() {
  local iface="${1:-net1}" ip="$2" gw="${3:-}" subnet="${4:-}" table="${5:-137}"
  [ -z "$iface" ] && return 0
  ip link show "$iface" >/dev/null 2>&1 || return 0
  [ -z "$ip" ] && ip="$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  [ -z "$ip" ] && return 0
  [ -z "$subnet" ] && subnet="${MULTUS_SUBNET:-10.1.137.0/24}"
  ip route replace "${subnet}" dev "$iface" || true
  ip route replace "${subnet}" dev "$iface" table "${table}" || true
  if [ -n "$gw" ]; then
    ip route replace default via "$gw" dev "$iface" table "${table}" || true
  fi
  ip rule del from "${ip}/32" table "${table}" 2>/dev/null || true
  if ip rule add from "${ip}/32" table "${table}" priority 100 2>/dev/null; then
    exp4_log info "multus profile: from ${ip} table ${table} ${subnet} ${gw:+via ${gw} }${iface}"
  else
    exp4_log warn "multus profile failed (need NET_ADMIN)"
  fi
}

exp4_sim5g_subnet() {
  printf '10.140.%s.0/24' "${SLICE_ID:-1}"
}

exp4_server_ifaces() {
  TO_CLIENT_IFACE="${TO_CLIENT_IFACE:-${OTA_IFACE:-net1}}"
  if exp4_no5g; then
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
  else
    CONSOLE_IFACE="${CONSOLE_IFACE:-${METRICS_IFACE:-eth0}}"
  fi
  exp4_log info "to_client=${TO_CLIENT_IFACE} console=${CONSOLE_IFACE}"
  exp4_wait_iface "$TO_CLIENT_IFACE"
  exp4_wait_iface "$CONSOLE_IFACE"
  local data_ip="${SIM5G_IP:-${MULTUS_IP:-}}"
  [ -z "$data_ip" ] && data_ip="$(ip -4 -o addr show dev "$TO_CLIENT_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  local cip="${CONSOLE_IP:-}"
  [ -z "$cip" ] && cip="$(ip -4 -o addr show dev "$CONSOLE_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  if exp4_no5g; then
    exp4_multus_route_profile "$TO_CLIENT_IFACE" "$data_ip" "" "$(exp4_sim5g_subnet)" 140
    exp4_multus_route_profile "$CONSOLE_IFACE" "$cip" "${MULTUS_GW:-${GW:-10.1.137.1}}" "10.1.137.0/24" 137
  else
    exp4_multus_route_profile "$TO_CLIENT_IFACE" "$data_ip"
  fi
  exp4_announce "$TO_CLIENT_IFACE" "$data_ip"
  exp4_announce "$CONSOLE_IFACE" "$cip"
}

exp4_start_console() {
  local backend="${BACKEND_URL:-http://127.0.0.1:8080}"
  local script="${CONSOLE_SCRIPT:-/app/frontend-console/frontend.py}"
  [ -f "$script" ] || return 0
  export BACKEND_URL="$backend"
  export DASHBOARD_STATIC="${DASHBOARD_STATIC:-$(dirname "$script")/static}"
  export FRONTEND_PORT="${FRONTEND_PORT:-80}"
  exp4_log info "frontend-console ${script} -> ${backend} :${FRONTEND_PORT}"
  python3 "$script" &
}

exp4_client_ifaces() {
  if exp4_no5g; then
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
  else
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net1}"
  fi
  exp4_log info "to_server=${TO_SERVER_IFACE} console=${CONSOLE_IFACE}"
  exp4_wait_iface "$CONSOLE_IFACE"
  exp4_wait_iface "$TO_SERVER_IFACE"
  local cip="${CONSOLE_IP:-}"
  [ -z "$cip" ] && cip="$(ip -4 -o addr show dev "$CONSOLE_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  local data_ip="${SIM5G_IP:-${MULTUS_IP:-}}"
  [ -z "$data_ip" ] && data_ip="$(ip -4 -o addr show dev "$TO_SERVER_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  if exp4_no5g; then
    exp4_multus_route_profile "$TO_SERVER_IFACE" "$data_ip" "${TARGET_SERVER_IP:-}" "$(exp4_sim5g_subnet)" 140
    exp4_multus_route_profile "$CONSOLE_IFACE" "$cip" "${MULTUS_GW:-${GW:-10.1.137.1}}" "10.1.137.0/24" 137
  else
    exp4_multus_route_profile "$CONSOLE_IFACE" "$cip"
  fi
  exp4_announce "$CONSOLE_IFACE" "$cip"
  exp4_announce "$TO_SERVER_IFACE" "$data_ip"
}
