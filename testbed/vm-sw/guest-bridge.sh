#!/bin/sh
# L2 site switch inside vm-sw guest: rename virtio ports, bridge on br0, no IP.
set -e

SW_BR="${SW_BR:-br0}"
# eth0:inf-internal eth1:inf-lower …
SW_RENAMES="${SW_RENAMES:-}"
SW_PORTS="${SW_PORTS:-}"
WAIT_SECS="${WAIT_SECS:-30}"

flush_l3() {
  for iface in "$@"; do
    ip -4 addr flush dev "$iface" 2>/dev/null || true
    ip -6 addr flush dev "$iface" 2>/dev/null || true
  done
}

wait_iface() {
  local iface="$1" i=0
  while [ "$i" -lt "$WAIT_SECS" ]; do
    ip link show "$iface" >/dev/null 2>&1 && return 0
    i=$((i + 1))
    sleep 1
  done
  echo "vm-sw: missing $iface" >&2
  exit 1
}

if [ -n "$SW_RENAMES" ]; then
  SW_PORTS=""
  for pair in $SW_RENAMES; do
    wait_iface "${pair%%:*}"
  done
  for pair in $SW_RENAMES; do
    eth="${pair%%:*}"
    logical="${pair#*:}"
    flush_l3 "$eth"
    ip link set "$eth" up
    if [ "$eth" != "$logical" ]; then
      ip link set "$eth" name "$logical"
    fi
    SW_PORTS="${SW_PORTS} ${logical}"
  done
  SW_PORTS="${SW_PORTS# }"
else
  SW_PORTS="${SW_PORTS:-eth0 eth1}"
  i=0
  while [ "$i" -lt "$WAIT_SECS" ]; do
    ready=1
    for iface in $SW_PORTS; do
      ip link show "$iface" >/dev/null 2>&1 || ready=0
    done
    [ "$ready" -eq 1 ] && break
    i=$((i + 1))
    sleep 1
  done
  for iface in $SW_PORTS; do
    wait_iface "$iface"
    flush_l3 "$iface"
    ip link set "$iface" up
  done
fi

ip link show "$SW_BR" >/dev/null 2>&1 || ip link add name "$SW_BR" type bridge
flush_l3 "$SW_BR"
ip link set "$SW_BR" up
for iface in $SW_PORTS; do
  ip link set "$iface" master "$SW_BR"
done
flush_l3 $SW_PORTS "$SW_BR"

if [ -f "/sys/class/net/${SW_BR}/bridge/stp_state" ]; then
  echo 0 >"/sys/class/net/${SW_BR}/bridge/stp_state" 2>/dev/null || true
fi

echo "vm-sw: L2 switch $SW_BR bridging: $SW_PORTS"
