#!/usr/bin/env bash
# Start MediaMTX (pub/sub), FastAPI+dashboard, then the GStreamer/YOLO analyzer.
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
HTTP_PORT="${HTTP_PORT:-8080}"
MTX_CONF="${MTX_CONF:-/app/edge/mediamtx.yml}"

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
    log warn "clock offset ${abs_ms}ms exceeds ${CHRONY_MAX_OFFSET_MS}ms; e2e accuracy degraded"
  else
    log info "clock offset ${abs_ms}ms within budget"
  fi
}

log info "entrypoint start (analyzer + mediamtx + api)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
check_chrony

if [ -n "${MULTUS_IP:-}" ]; then
  export MTX_WEBRTCICEHOSTNAT1TO1IPS="${MULTUS_IP}"
fi

log info "starting MediaMTX"
mediamtx "${MTX_CONF}" &
MTX_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:9997/v3/config/global/get >/dev/null 2>&1; then
    log info "MediaMTX API ready"
    break
  fi
  sleep 0.25
done

cleanup() {
  kill "${MTX_PID}" 2>/dev/null || true
}
trap cleanup EXIT

log info "starting GStreamer analyzer + FastAPI"
exec python3 -m edge.analyzer
