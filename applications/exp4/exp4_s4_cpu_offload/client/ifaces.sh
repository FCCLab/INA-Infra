# Exp4 dual-iface helpers. Source from server/client entrypoints.
#
# Dual Multus (`exp4_deploy.sh`, SCHEME_ID=exp4-no5g):
#   TO_*_IFACE=net1   simulated 5G  10.140.<s>.x   table 1400+octet
#   CONSOLE_IFACE=net2  site console 10.1.137.x    table <last octet>
# 5G GitOps (exp4-s0 …):
#   Server: TO_CLIENT_IFACE=net1 (N6 = site 137)   CONSOLE_IFACE=net1
#   Client: TO_SERVER_IFACE=oaitun_ue*             CONSOLE_IFACE=net2
#
# Dedicated table per source IP so replies leave on the iface they arrived on.
# Requires NET_ADMIN.
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
MULTUS_SUBNET="${MULTUS_SUBNET:-10.1.137.0/24}"
MULTUS_GW="${MULTUS_GW:-${GW:-10.1.137.1}}"

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

exp4_is_cluster_iface() {
  case "${1:-}" in
    eth0|eth1|"") return 0 ;;
  esac
  return 1
}

exp4_table_for_ip() {
  local ip="${1:-}"
  local oct="${ip##*.}"
  case "$ip" in
    10.140.*) printf '%s\n' "$((1400 + oct))" ;;
    *) printf '%s\n' "$oct" ;;
  esac
}

exp4_wait_iface() {
  local iface="$1" elapsed=0
  [ -z "$iface" ] && return 0
  exp4_is_cluster_iface "$iface" && return 0
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
  exp4_is_cluster_iface "$iface" && return 0
  ip link show "$iface" >/dev/null 2>&1 || return 0
  if command -v arping >/dev/null 2>&1; then
    arping -c 1 -w 2 -U -I "$iface" "$ip" >/dev/null 2>&1 || true
  fi
}

exp4_iface_ip() {
  local iface="$1"
  ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true
}

# Reply-path profile: from <ip>/32 lookup <table>, on-link subnet + optional default.
#   exp4_multus_route_profile <iface> <ip> [gw] [subnet] [table]
exp4_multus_route_profile() {
  local iface="${1:-}" ip="${2:-}" gw="${3:-}" subnet="${4:-}" table="${5:-}"
  [ -z "$iface" ] && return 0
  exp4_is_cluster_iface "$iface" && return 0
  ip link show "$iface" >/dev/null 2>&1 || return 0
  [ -z "$ip" ] && ip="$(exp4_iface_ip "$iface")"
  [ -z "$ip" ] && return 0
  [ -z "$subnet" ] && subnet="$MULTUS_SUBNET"
  [ -z "$table" ] && table="$(exp4_table_for_ip "$ip")"

  sysctl -w "net.ipv4.conf.${iface}.rp_filter=2" >/dev/null 2>&1 || true
  ip route replace "${subnet}" dev "$iface" || true
  ip route replace "${subnet}" dev "$iface" table "${table}" || true
  if [ -n "$gw" ]; then
    ip route replace default via "$gw" dev "$iface" table "${table}" || true
  fi
  ip rule del from "${ip}/32" table "${table}" 2>/dev/null || true
  if ip rule add from "${ip}/32" table "${table}" priority 100 2>/dev/null; then
    exp4_log info "multus profile: from ${ip} table ${table} ${subnet} ${gw:+via ${gw} }dev ${iface}"
  else
    exp4_log warn "multus profile failed on ${iface} (need NET_ADMIN)"
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
    CONSOLE_IFACE="${CONSOLE_IFACE:-${TO_CLIENT_IFACE}}"
  fi
  exp4_log info "to_client=${TO_CLIENT_IFACE} console=${CONSOLE_IFACE}"
  exp4_wait_iface "$TO_CLIENT_IFACE"
  exp4_wait_iface "$CONSOLE_IFACE"

  local data_ip="${SIM5G_IP:-${MULTUS_IP:-}}"
  [ -z "$data_ip" ] && data_ip="$(exp4_iface_ip "$TO_CLIENT_IFACE")"
  local cip="${CONSOLE_IP:-}"
  [ -z "$cip" ] && cip="$(exp4_iface_ip "$CONSOLE_IFACE")"
  local gw="${MULTUS_GW:-${GW:-10.1.137.1}}"

  if exp4_no5g; then
    exp4_multus_route_profile "$TO_CLIENT_IFACE" "$data_ip" "" "$(exp4_sim5g_subnet)" "$(exp4_table_for_ip "$data_ip")"
    exp4_multus_route_profile "$CONSOLE_IFACE" "$cip" "$gw" "$MULTUS_SUBNET" "$(exp4_table_for_ip "$cip")"
  else
    # N6 is on 10.1.137.0/24. Replies (laptop + UPF SNAT) must leave net1, not eth0.
    exp4_multus_route_profile "$TO_CLIENT_IFACE" "$data_ip" "$gw" "$MULTUS_SUBNET" "$(exp4_table_for_ip "$data_ip")"
    if [ -n "$CONSOLE_IFACE" ] && [ "$CONSOLE_IFACE" != "$TO_CLIENT_IFACE" ]; then
      exp4_multus_route_profile "$CONSOLE_IFACE" "$cip" "$gw" "$MULTUS_SUBNET" "$(exp4_table_for_ip "$cip")"
    fi
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

# App-server IPs that must leave via TO_SERVER_IFACE (5G PDU / sim5G).
# Never include the console gateway (10.1.137.1) or CONSOLE_IP — those stay on net2.
exp4_to_server_hosts() {
  local h extra
  for h in \
    "${TARGET_SERVER_IP:-}" \
    "${IPERF_HOST:-}" \
    "${SFTP_HOST:-}" \
    "${BROKER_HOST:-}" \
    "${SERVER_HOST:-}" \
    "${SERVER_RTSP_HOST:-}" \
    "${MTX_SOURCE_HOST:-}" \
    "${RTSP_TARGET_HOST:-}"
  do
    [ -n "$h" ] && printf '%s\n' "$h"
  done
  extra="${PDU_ROUTE_HOSTS:-}"
  extra="${extra//,/ }"
  for h in $extra; do
    [ -n "$h" ] && printf '%s\n' "$h"
  done
}

exp4_is_console_host() {
  local h="${1:-}"
  [ -z "$h" ] && return 0
  [ "$h" = "10.1.137.1" ] && return 0
  [ -n "${CONSOLE_IP:-}" ] && [ "$h" = "$CONSOLE_IP" ] && return 0
  [ -n "${MULTUS_GW:-}" ] && [ "$h" = "$MULTUS_GW" ] && return 0
  return 1
}

# Live to-server iface: configured name if it has IPv4, else first oaitun*.
exp4_detect_to_server() {
  local want n
  if exp4_no5g; then
    want="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    if ip -4 addr show dev "$want" 2>/dev/null | grep -q 'inet '; then
      printf '%s\n' "$want"
      return 0
    fi
    return 1
  fi
  want="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
  if ip -4 addr show dev "$want" 2>/dev/null | grep -q 'inet '; then
    printf '%s\n' "$want"
    return 0
  fi
  for n in /sys/class/net/oaitun*; do
    [ -e "$n" ] || continue
    n="${n##*/}"
    if ip -4 addr show dev "$n" 2>/dev/null | grep -q 'inet '; then
      printf '%s\n' "$n"
      return 0
    fi
  done
  return 1
}

# Install more-specific /32s so 10.1.137.21N is not absorbed by net2's connected /24.
exp4_pin_to_server() {
  local iface="${1:-}" src h
  [ -z "$iface" ] && iface="$(exp4_detect_to_server || true)"
  [ -z "$iface" ] && return 1
  ip -4 addr show dev "$iface" 2>/dev/null | grep -q 'inet ' || return 1
  src="$(exp4_iface_ip "$iface")"
  [ -z "$src" ] && return 1
  while read -r h; do
    exp4_is_console_host "$h" && continue
    if [ -n "$src" ]; then
      ip route replace "${h}/32" dev "$iface" src "$src" 2>/dev/null \
        || ip route replace "${h}/32" dev "$iface" || true
    else
      ip route replace "${h}/32" dev "$iface" || true
    fi
  done < <(exp4_to_server_hosts | awk 'NF && !seen[$0]++')
  return 0
}

exp4_watch_to_server_pin() {
  if [ -n "${EXP4_PIN_WATCH_STARTED:-}" ]; then
    return 0
  fi
  EXP4_PIN_WATCH_STARTED=1
  (
    local live
    while true; do
      live="$(exp4_detect_to_server || true)"
      if [ -n "$live" ]; then
        exp4_pin_to_server "$live" || true
      fi
      sleep 2
    done
  ) &
  exp4_log info "auto-detect to-server iface and pin app-server /32 (background)"
}

exp4_client_ifaces() {
  if exp4_no5g; then
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
  else
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
  fi
  exp4_log info "to_server=${TO_SERVER_IFACE} console=${CONSOLE_IFACE}"
  exp4_wait_iface "$CONSOLE_IFACE"

  local cip="${CONSOLE_IP:-}"
  [ -z "$cip" ] && cip="$(exp4_iface_ip "$CONSOLE_IFACE")"
  local gw="${MULTUS_GW:-${GW:-10.1.137.1}}"
  # Console first so operator access does not wait for PDU.
  exp4_multus_route_profile "$CONSOLE_IFACE" "$cip" "$gw" "$MULTUS_SUBNET" "$(exp4_table_for_ip "$cip")"
  exp4_announce "$CONSOLE_IFACE" "$cip"

  if exp4_no5g; then
    exp4_wait_iface "$TO_SERVER_IFACE"
    local data_ip="${SIM5G_IP:-${MULTUS_IP:-}}"
    [ -z "$data_ip" ] && data_ip="$(exp4_iface_ip "$TO_SERVER_IFACE")"
    exp4_multus_route_profile "$TO_SERVER_IFACE" "$data_ip" "${TARGET_SERVER_IP:-}" "$(exp4_sim5g_subnet)" "$(exp4_table_for_ip "$data_ip")"
    exp4_announce "$TO_SERVER_IFACE" "$data_ip"
    exp4_pin_to_server "$TO_SERVER_IFACE" || true
  else
    # PDU appears later; keep installing /32s so app data never uses net2.
    exp4_watch_to_server_pin
  fi
}

exp4_load_ifaces() {
  local p
  for p in /exp4/ifaces.sh /app/common/ifaces.sh; do
    if [ -f "$p" ]; then
      # shellcheck disable=SC1090
      . "$p"
      return 0
    fi
  done
  return 1
}
