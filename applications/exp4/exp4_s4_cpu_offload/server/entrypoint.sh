#!/usr/bin/env bash
# Server: TO_CLIENT_IFACE=net1. No-5G also uses CONSOLE_IFACE=net1.
set -euo pipefail
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
TO_CLIENT_IFACE="${TO_CLIENT_IFACE:-${OTA_IFACE:-net1}}"
case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
  *exp4-no5g*|1|true|True|yes|YES) CONSOLE_IFACE="${CONSOLE_IFACE:-net1}" ;;
  *) CONSOLE_IFACE="${CONSOLE_IFACE:-eth0}" ;;
esac

log() { printf '{"ts":%s,"level":"%s","event":"iface","msg":"%s"}\n' "$(date +%s)" "$1" "$2"; }
wait_iface() {
  local iface="$1" elapsed=0
  [ -z "$iface" ] && return 0
  while ! ip -4 addr show dev "$iface" 2>/dev/null | grep -q 'inet '; do
    [ "$elapsed" -ge "$IFACE_TIMEOUT" ] && { log warn "${iface} not ready"; return 0; }
    sleep 1
    elapsed=$((elapsed + 1))
  done
  log info "${iface} ready"
}

log info "to_client=${TO_CLIENT_IFACE} console=${CONSOLE_IFACE}"
wait_iface "$TO_CLIENT_IFACE"
wait_iface "$CONSOLE_IFACE"
export EXP4_APP_TYPE="${EXP4_APP_TYPE:-exp4-s4}"
export SLICE_ID="${SLICE_ID:-4}"
export TO_CLIENT_IFACE
python3 /app/influx_publish.py &
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8080}"
export DASHBOARD_STATIC="${DASHBOARD_STATIC:-/app/frontend-console/static}"
python3 /app/frontend-console/frontend.py &
exec python3 /app/server.py
