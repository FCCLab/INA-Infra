#!/usr/bin/env bash
# Entrypoint for Physical AI UE (starts Python Backend on 8090 + Frontend Console on 80)
set -euo pipefail

ROLE="${CONSOLE_ROLE:-all}"
CONSOLE_IP="${CONSOLE_IP:-}"

if [ -n "${CONSOLE_IP}" ] && ip link show dev net1 >/dev/null 2>&1; then
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I net1 "${CONSOLE_IP}" >/dev/null 2>&1 || true
  fi
fi

if [ "${ROLE}" = "frontend" ]; then
  exec python3 /app/frontend-console/frontend.py
elif [ "${ROLE}" = "backend" ]; then
  exec python3 /app/backend/backend.py
else
  # Launch Python Backend
  python3 /app/backend/backend.py &
  BACKEND_PID=$!

  # Launch Frontend Console
  python3 /app/frontend-console/frontend.py &
  FRONTEND_PID=$!

  trap "kill -TERM ${BACKEND_PID} ${FRONTEND_PID} 2>/dev/null || true" SIGTERM SIGINT EXIT
  wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
fi
