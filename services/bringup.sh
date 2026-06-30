#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

IFACE="${ENP1S0_PARENT:-enp1s0}"
HOST_IP="${PIHOLE_HOST_IP:?set PIHOLE_HOST_IP in .env}"
PREFIX="${PIHOLE_HOST_PREFIX:-24}"
GATEWAY="${GATEWAY:-10.1.132.1}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo so enp1s0 can get a static IPv4 before Pi-hole starts." >&2
  exit 1
fi

if ! ip link show dev "$IFACE" >/dev/null 2>&1; then
  echo "Interface $IFACE not found." >&2
  exit 1
fi

ip link set "$IFACE" up

# Remove macvlan shim from a previous macvlan deployment, if present.
SHIM_IFACE="${MACVLAN_SHIM_IFACE:-${IFACE}-shim}"
if ip link show dev "$SHIM_IFACE" >/dev/null 2>&1; then
  echo "Removing macvlan shim ${SHIM_IFACE}"
  ip link del "$SHIM_IFACE" || true
fi

if ! ip -4 -o addr show dev "$IFACE" | grep -q "inet ${HOST_IP}/${PREFIX}"; then
  echo "Adding ${HOST_IP}/${PREFIX} on ${IFACE}"
  ip addr add "${HOST_IP}/${PREFIX}" dev "$IFACE"
fi

if ! ip route show dev "$IFACE" | grep -q "^default.*${GATEWAY}"; then
  ip route replace default via "$GATEWAY" dev "$IFACE" metric 10 || true
fi

# Avoid port-53 conflict with systemd-resolved stub listener on 127.0.0.53
if grep -q '^#*DNSStubListener=' /etc/systemd/resolved.conf 2>/dev/null; then
  if ! grep -q '^DNSStubListener=no' /etc/systemd/resolved.conf; then
  echo "Tip: set DNSStubListener=no in /etc/systemd/resolved.conf and restart systemd-resolved" >&2
  echo "     if Pi-hole fails to bind port 53." >&2
  fi
fi

exec docker compose up -d --build "$@"
