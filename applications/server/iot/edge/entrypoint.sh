#!/usr/bin/env bash
# Wait for the dedicated Mosquitto sidecar, then exec the downlink controller.
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
BROKER_HOST="${LOCAL_BROKER_HOST:-127.0.0.1}"
BROKER_PORT="${LOCAL_BROKER_PORT:-1884}"
BROKER_WAIT="${BROKER_WAIT_TIMEOUT:-60}"

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

wait_for_broker() {
  local elapsed=0
  log info "waiting for mosquitto at ${BROKER_HOST}:${BROKER_PORT}"
  while true; do
    if python3 - "$BROKER_HOST" "$BROKER_PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(1)
try:
    s.connect((host, port))
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
    then
      log info "mosquitto ready at ${BROKER_HOST}:${BROKER_PORT}"
      return 0
    fi
    if [ "$elapsed" -ge "$BROKER_WAIT" ]; then
      log warn "mosquitto not reachable after ${BROKER_WAIT}s; controller will keep retrying"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

log info "entrypoint start (controller; dedicated mosquitto sidecar)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
check_chrony
wait_for_broker
exec python3 /app/edge/controller.py
