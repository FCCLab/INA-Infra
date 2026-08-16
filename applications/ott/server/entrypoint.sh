#!/usr/bin/env bash
# Wait for interfaces / video file, warn on clock skew, then exec the server.
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
VIDEO_SOURCE="${VIDEO_SOURCE:-/data/source.mp4}"
VIDEO_WAIT_TIMEOUT_S="${VIDEO_WAIT_TIMEOUT_S:-3600}"

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

wait_for_video() {
  local elapsed=0
  while [ ! -s "${VIDEO_SOURCE}" ]; do
    if [ "$elapsed" -ge "$VIDEO_WAIT_TIMEOUT_S" ]; then
      log error "VIDEO_SOURCE ${VIDEO_SOURCE} missing after ${VIDEO_WAIT_TIMEOUT_S}s"
      exit 1
    fi
    if [ $((elapsed % 10)) -eq 0 ]; then
      log info "waiting for VIDEO_SOURCE ${VIDEO_SOURCE} (${elapsed}s)"
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  log info "VIDEO_SOURCE ready ($(wc -c <"${VIDEO_SOURCE}") bytes)"
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

log info "entrypoint start (server)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
wait_for_video
check_chrony

exec python3 /app/server/server.py
