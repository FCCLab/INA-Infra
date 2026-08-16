#!/usr/bin/env bash
# Wait for the OTA interface, pin 5G PDU routes, warn on clock skew, then exec
# the IoT client.
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
# 5G PDU tunnel routing (over-the-air MQTT). When running as a sidecar in an
# OAI UE pod, PDU_IFACE is the UE's tunnel (e.g. oaitun_ue1) shared via the pod
# netns, and PDU_ROUTE_HOSTS is a CSV of destinations (the broker) to pin
# through it so MQTT traverses the 5G air interface instead of pod/mgmt net.
PDU_IFACE="${PDU_IFACE:-}"
PDU_ROUTE_HOSTS="${PDU_ROUTE_HOSTS:-}"
PDU_WAIT_TIMEOUT="${PDU_WAIT_TIMEOUT:-300}"

log() {
  printf '{"ts":%s,"level":"%s","event":"entrypoint","msg":"%s"}\n' \
    "$(date +%s)" "$1" "$2"
}

wait_for_iface() {
  local iface="$1" elapsed=0
  [ -z "$iface" ] && return 0
  while ! ip -4 addr show dev "$iface" 2>/dev/null | grep -q 'inet '; do
    if [ "$elapsed" -ge "$IFACE_TIMEOUT" ]; then
      log warn "interface ${iface} has no IPv4 after ${IFACE_TIMEOUT}s; continuing"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  log info "interface ${iface} ready"
}

check_chrony() {
  local args=(tracking)
  [ -n "$CHRONYC_HOST" ] && args=(-h "$CHRONYC_HOST" tracking)
  if ! command -v chronyc >/dev/null 2>&1; then
    log warn "chronyc not installed; clock offset unchecked"
    return 0
  fi
  local offset
  offset="$(chronyc "${args[@]}" 2>/dev/null | awk '/Last offset/ {print $4}' || true)"
  if [ -z "$offset" ]; then
    log warn "chrony not reachable; ensure host is NTP-synced over ens0"
    return 0
  fi
  local abs_ms
  abs_ms="$(awk -v o="$offset" 'BEGIN{o=(o<0?-o:o); printf "%.3f", o*1000}')"
  if awk -v a="$abs_ms" -v m="$CHRONY_MAX_OFFSET_MS" 'BEGIN{exit !(a>m)}'; then
    log warn "clock offset ${abs_ms}ms exceeds ${CHRONY_MAX_OFFSET_MS}ms; delay accuracy degraded"
  else
    log info "clock offset ${abs_ms}ms within budget"
  fi
}

pin_pdu_routes_once() {
  # Pin each PDU_ROUTE_HOSTS entry via the PDU tunnel (idempotent).
  local h ok=0
  IFS=',' read -ra _hosts <<< "$PDU_ROUTE_HOSTS"
  for h in "${_hosts[@]}"; do
    [ -z "$h" ] && continue
    if ip route replace "${h}/32" dev "$PDU_IFACE" 2>/dev/null; then
      ok=$((ok + 1))
    fi
  done
  [ "$ok" -gt 0 ]
}

setup_pdu_routes() {
  [ -z "$PDU_IFACE" ] && return 0
  [ -z "$PDU_ROUTE_HOSTS" ] && return 0
  local elapsed=0
  # Wait for the tunnel (appears after UE registration + PDU session), then pin
  # synchronously so the FIRST MQTT connect already egresses over the air.
  while ! ip link show "$PDU_IFACE" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$PDU_WAIT_TIMEOUT" ]; then
      log warn "PDU iface ${PDU_IFACE} absent after ${PDU_WAIT_TIMEOUT}s; MQTT may not use the air interface"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  pin_pdu_routes_once && log info "pinned ${PDU_ROUTE_HOSTS} via ${PDU_IFACE}"
  # Keep re-pinning in the background: the tunnel can flap on UE re-attach and
  # its routes get torn down with it. `ip route replace` is idempotent.
  (
    while true; do
      ip link show "$PDU_IFACE" >/dev/null 2>&1 && pin_pdu_routes_once
      sleep 10
    done
  ) &
}

log info "entrypoint start (iot_client)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
setup_pdu_routes
check_chrony

exec python3 /app/client/iot_client.py
