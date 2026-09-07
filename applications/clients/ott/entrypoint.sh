#!/usr/bin/env bash
# Entrypoint for OTT UE (starts Python Backend on 8090 + Frontend Console on 80)
set -euo pipefail

ROLE="${CONSOLE_ROLE:-all}"
CONSOLE_IP="${CONSOLE_IP:-}"

# Selkies Chromium iframe needs a secure context; mint a self-signed cert if none given.
CERT_DIR="${CERT_DIR:-/tmp/certs}"
mkdir -p "${CERT_DIR}"
if [ -z "${SSL_CERTFILE:-}" ] || [ ! -f "${SSL_CERTFILE:-}" ]; then
  SAN="DNS:localhost,DNS:ott-ue"
  if [ -n "${CONSOLE_IP}" ]; then
    SAN="${SAN},IP:${CONSOLE_IP}"
  fi
  if openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "${CERT_DIR}/tls.key" \
    -out "${CERT_DIR}/tls.crt" \
    -days 3650 \
    -subj "/CN=${CONSOLE_IP:-ott-ue}" \
    -addext "subjectAltName=${SAN}" >/dev/null 2>&1; then
    export SSL_CERTFILE="${CERT_DIR}/tls.crt"
    export SSL_KEYFILE="${CERT_DIR}/tls.key"
  fi
fi

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
