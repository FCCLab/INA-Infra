#!/usr/bin/env bash
# Entrypoint for CCTV UE Console (Backend or Frontend)
set -euo pipefail

ROLE="${CONSOLE_ROLE:-backend}"
CONSOLE_IP="${CONSOLE_IP:-}"

if [ -n "${CONSOLE_IP}" ] && ip link show dev net1 >/dev/null 2>&1; then
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I net1 "${CONSOLE_IP}" >/dev/null 2>&1 || true
  fi
fi

if [ "${ROLE}" = "frontend" ]; then
  exec python3 /app/ue/frontend.py
else
  exec python3 /app/ue/backend.py
fi
