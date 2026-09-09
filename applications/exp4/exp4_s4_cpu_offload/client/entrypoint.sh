#!/usr/bin/env bash
# UE: backend on :8090 + frontend console on :80.
# No-5G: TO_SERVER_IFACE=net1 (same Multus as CONSOLE_IFACE).
set -euo pipefail
case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
  *exp4-no5g*|1|true|True|yes|YES)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net1}"
    ;;
  *)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net1}"
    ;;
esac
ROLE="${CONSOLE_ROLE:-all}"

log() { printf '{"ts":%s,"level":"%s","event":"ue","msg":"%s"}\n' "$(date +%s)" "$1" "$2"; }
wait_iface() {
  local iface="$1" elapsed=0
  [ -z "$iface" ] && return 0
  while ! ip -4 addr show dev "$iface" 2>/dev/null | grep -q 'inet '; do
    [ "$elapsed" -ge "${IFACE_TIMEOUT:-60}" ] && { log warn "${iface} not ready"; return 0; }
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

log info "to_server=${TO_SERVER_IFACE} console=${CONSOLE_IFACE}"
for _p in /exp4/ifaces.sh /app/common/ifaces.sh; do
  [ -f "$_p" ] || continue
  # shellcheck disable=SC1090
  . "$_p"
  exp4_client_ifaces || true
  break
done
export TO_SERVER_IFACE CONSOLE_IFACE
export EXP4_METRICS_ORIGIN="${EXP4_METRICS_ORIGIN:-client}"
export EXP4_APP_TYPE="${EXP4_APP_TYPE:-exp4-s4}"
export SLICE_ID="${SLICE_ID:-4}"
python3 /usr/local/bin/exp4_influx_publish.py &
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8090}"
export DASHBOARD_STATIC="${DASHBOARD_STATIC:-/app/frontend-console/static}"
export FRONTEND_PORT="${FRONTEND_PORT:-80}"

if [ "${ROLE}" = "frontend" ]; then
  exec python3 /app/frontend-console/frontend.py
elif [ "${ROLE}" = "backend" ]; then
  exec python3 /app/backend/backend.py
fi
python3 /app/backend/backend.py &
exec python3 /app/frontend-console/frontend.py
