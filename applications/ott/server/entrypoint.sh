#!/usr/bin/env bash
# OTT Video Streaming Server entrypoint
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-net1}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-10}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
MTX_CONF="${MTX_CONF:-/app/server/mediamtx.yml}"

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
    log warn "chrony not reachable; ensure host is NTP-synced"
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

log info "entrypoint start (ott-server + mediamtx + api)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
check_chrony

# Announce static MAC via gratuitous ARP if net1 exists
if ip link show dev net1 >/dev/null 2>&1; then
  NET1_IP=$(ip -4 -o addr show dev net1 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)
  if [ -n "${NET1_IP}" ] && command -v arping >/dev/null 2>&1; then
    log info "announcing static MAC on net1 (${NET1_IP})"
    arping -c 3 -U -I net1 "${NET1_IP}" >/dev/null 2>&1 || true
  fi
fi

if [ -n "${MULTUS_IP:-}" ]; then
  export MTX_WEBRTCICEHOSTNAT1TO1IPS="${MULTUS_IP}"
fi

START_MEDIAMTX="${START_MEDIAMTX:-false}"
MTX_RTSP_URL="${MTX_RTSP_URL:-rtsp://127.0.0.1:8555}"

if [ "${START_MEDIAMTX}" = "true" ] && command -v mediamtx >/dev/null 2>&1; then
  log info "starting embedded MediaMTX"
  mediamtx "${MTX_CONF}" &
  MTX_PID=$!
  trap 'kill -TERM ${MTX_PID} 2>/dev/null || true' EXIT
fi

exec python3 -m server.main
