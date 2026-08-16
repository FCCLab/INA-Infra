#!/usr/bin/env bash
# Render the broker config, start mosquitto, then exec the controller.
# The controller runs in the foreground (PID-adjacent); if mosquitto dies the
# background watcher tears the container down so the healthcheck/restart fires.
set -euo pipefail

OTA_IFACE="${OTA_IFACE:-}"
METRICS_IFACE="${METRICS_IFACE:-}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
# Empty OTA_BIND_IP -> bind the broker's OTA listener to all interfaces so the
# single-host bridge test works without a static OTA IP.
export OTA_BIND_IP="${OTA_BIND_IP:-0.0.0.0}"
MOSQUITTO_CONF="/tmp/mosquitto.conf"

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

log info "entrypoint start (mosquitto + controller)"
wait_for_iface "$OTA_IFACE"
wait_for_iface "$METRICS_IFACE"
check_chrony

envsubst '${OTA_BIND_IP}' < /app/edge/mosquitto.conf.tpl > "$MOSQUITTO_CONF"
log info "rendered ${MOSQUITTO_CONF} (OTA listener on ${OTA_BIND_IP}:1883)"

mosquitto -c "$MOSQUITTO_CONF" &
MOSQ_PID=$!
log info "mosquitto started pid=${MOSQ_PID}"

# Give the broker a moment to open its listeners before the controller dials in
# (the controller also retries via connect_async, so this is just to cut noise).
sleep 1

# Exec the controller as PID 1 so `docker stop` delivers SIGTERM straight to it
# for a graceful flush. If mosquitto later dies, the compose healthcheck
# (mosquitto_sub on :1884) fails and the restart policy recreates the container.
exec python3 /app/edge/controller.py
