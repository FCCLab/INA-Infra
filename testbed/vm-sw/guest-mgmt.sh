#!/bin/sh
# Routed management port on libvirt default NAT (not bridged to br0).
set -e

SW_MGMT_RENAME="${SW_MGMT_RENAME:-}"
WAIT_SECS="${WAIT_SECS:-30}"

[ -n "$SW_MGMT_RENAME" ] || exit 0

eth="${SW_MGMT_RENAME%%:*}"
name="${SW_MGMT_RENAME#*:}"

i=0
while [ "$i" -lt "$WAIT_SECS" ]; do
  ip link show "$eth" >/dev/null 2>&1 && break
  i=$((i + 1))
  sleep 1
done

ip link show "$eth" >/dev/null 2>&1 || {
  echo "vm-sw: missing mgmt port $eth" >&2
  exit 1
}

ip -4 addr flush dev "$eth" 2>/dev/null || true
ip -6 addr flush dev "$eth" 2>/dev/null || true
ip link set "$eth" up
if [ "$eth" != "$name" ]; then
  ip link set "$eth" name "$name"
fi
ip link set "$name" up
udhcpc -i "$name" -b -q -t 10 -T 3 2>/dev/null || true

echo "vm-sw: mgmt port $name up ($(ip -4 -o addr show dev "$name" 2>/dev/null | awk '{print $4}' || echo dhcp))"
