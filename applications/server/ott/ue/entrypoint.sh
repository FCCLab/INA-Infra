#!/usr/bin/env bash
# Entrypoint for OTT UE Console (Backend or Frontend)
set -euo pipefail

ROLE="${CONSOLE_ROLE:-backend}"
CONSOLE_IP="${CONSOLE_IP:-}"

if [ -n "${CONSOLE_IP}" ] && ip link show dev net1 >/dev/null 2>&1; then
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I net1 "${CONSOLE_IP}" >/dev/null 2>&1 || true
  fi
fi

if [ "${ROLE}" = "frontend" ]; then
  TLS_DIR="${TLS_DIR:-/tmp/ott-console-tls}"
  mkdir -p "${TLS_DIR}"
  if [ ! -f "${TLS_DIR}/cert.pem" ] || [ ! -f "${TLS_DIR}/key.pem" ]; then
    SAN="subjectAltName=IP:127.0.0.1,DNS:localhost"
    if [ -n "${CONSOLE_IP}" ]; then
      SAN="subjectAltName=IP:${CONSOLE_IP},IP:127.0.0.1,DNS:localhost"
    fi
    openssl req -x509 -nodes -newkey rsa:2048 \
      -keyout "${TLS_DIR}/key.pem" \
      -out "${TLS_DIR}/cert.pem" \
      -days 3650 \
      -subj "/CN=${CONSOLE_IP:-ott-ue}" \
      -addext "${SAN}"
  fi
  export SSL_CERTFILE="${TLS_DIR}/cert.pem"
  export SSL_KEYFILE="${TLS_DIR}/key.pem"
  export HTTPS_PORT="${HTTPS_PORT:-443}"
  export HTTP_PORT="${HTTP_PORT:-80}"
  export CHROME_UPSTREAM="${CHROME_UPSTREAM:-http://127.0.0.1:3000}"
  exec python3 /app/ue/frontend.py
else
  exec python3 /app/ue/backend.py
fi
