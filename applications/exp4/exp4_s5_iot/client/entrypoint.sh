#!/usr/bin/env bash
# Entrypoint for IoT UE (starts Python Backend on 8090 + Frontend Console on 80)
# Dual Multus: TO_SERVER_IFACE=net1 (sim 5G), CONSOLE_IFACE=net2.
set -euo pipefail

ROLE="${CONSOLE_ROLE:-all}"
CONSOLE_IP="${CONSOLE_IP:-}"

case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
  *exp4-no5g*|1|true|True|yes|YES)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
    ;;
  *)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net1}"
    ;;
esac
export TO_SERVER_IFACE CONSOLE_IFACE
export EXP4_METRICS_ORIGIN="${EXP4_METRICS_ORIGIN:-client}"
export EXP4_APP_TYPE="${EXP4_APP_TYPE:-exp4-s5}"
export SLICE_ID="${SLICE_ID:-5}"

if [ -n "${CONSOLE_IP}" ] && ip link show dev "$CONSOLE_IFACE" >/dev/null 2>&1; then
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I "$CONSOLE_IFACE" "${CONSOLE_IP}" >/dev/null 2>&1 || true
  fi
fi

python3 /usr/local/bin/exp4_influx_publish.py &

if [ "${ROLE}" = "frontend" ]; then
  exec python3 /app/frontend-console/frontend.py
elif [ "${ROLE}" = "backend" ]; then
  exec python3 /app/backend/backend.py
else
  python3 /app/backend/backend.py &
  BACKEND_PID=$!
  python3 /app/frontend-console/frontend.py &
  FRONTEND_PID=$!
  trap "kill -TERM ${BACKEND_PID} ${FRONTEND_PID} 2>/dev/null || true" SIGTERM SIGINT EXIT
  wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
fi
