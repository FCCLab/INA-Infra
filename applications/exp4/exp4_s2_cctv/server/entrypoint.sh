#!/usr/bin/env bash
# Start MediaMTX, then DeepStream YOLO (default) or legacy Ultralytics CCTV analyzer.
set -euo pipefail

TO_CLIENT_IFACE="${TO_CLIENT_IFACE:-${OTA_IFACE:-net1}}"
case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
  *exp4-no5g*|1|true|True|yes|YES) CONSOLE_IFACE="${CONSOLE_IFACE:-net2}" ;;
  *) CONSOLE_IFACE="${CONSOLE_IFACE:-${METRICS_IFACE:-eth0}}" ;;
esac
OTA_IFACE="${OTA_IFACE:-$TO_CLIENT_IFACE}"
METRICS_IFACE="${METRICS_IFACE:-$CONSOLE_IFACE}"
IFACE_TIMEOUT="${IFACE_TIMEOUT:-60}"
CHRONY_MAX_OFFSET_MS="${CHRONY_MAX_OFFSET_MS:-5}"
CHRONYC_HOST="${CHRONYC_HOST:-}"
HTTP_PORT="${HTTP_PORT:-8080}"
MTX_CONF="${MTX_CONF:-/app/edge/mediamtx.yml}"
YOLO_BACKEND="${YOLO_BACKEND:-deepstream}"
DS_NUM_STREAMS="${DS_NUM_STREAMS:-4}"

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

log info "entrypoint start backend=${YOLO_BACKEND} streams=${DS_NUM_STREAMS} to_client=${TO_CLIENT_IFACE} console=${CONSOLE_IFACE}"
wait_for_iface "$TO_CLIENT_IFACE"
wait_for_iface "$CONSOLE_IFACE"
check_chrony

for _iface in "$TO_CLIENT_IFACE" "$CONSOLE_IFACE"; do
  [ -z "$_iface" ] && continue
  if ip link show dev "$_iface" >/dev/null 2>&1; then
    _ip=$(ip -4 -o addr show dev "$_iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)
    if [ -n "${_ip}" ] && command -v arping >/dev/null 2>&1; then
      log info "announcing static MAC on ${_iface} (${_ip})"
      arping -c 3 -U -I "$_iface" "${_ip}" >/dev/null 2>&1 || true
    fi
  fi
done

ICE_HOST="${CONSOLE_IP:-${MULTUS_IP:-}}"
if [ -n "${ICE_HOST}" ]; then
  export MTX_WEBRTCICEHOSTNAT1TO1IPS="${ICE_HOST}"
fi

START_MEDIAMTX="${START_MEDIAMTX:-true}"
MTX_RTSP_URL="${MTX_RTSP_URL:-rtsp://127.0.0.1:8555}"
export MTX_RTSP_URL DS_NUM_STREAMS YOLO_BACKEND

PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
}
trap cleanup EXIT

if [ "${START_MEDIAMTX}" = "true" ] && [[ "${MTX_RTSP_URL}" == *"127.0.0.1"* || "${MTX_RTSP_URL}" == *"localhost"* ]]; then
  log info "starting local MediaMTX"
  mediamtx "${MTX_CONF}" &
  PIDS+=($!)
  for _ in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:9997/v3/config/global/get >/dev/null 2>&1; then
      log info "MediaMTX API ready"
      break
    fi
    sleep 0.25
  done
else
  log info "using external MediaMTX at ${MTX_RTSP_URL}"
fi

export EXP4_APP_TYPE="${EXP4_APP_TYPE:-exp4-s2}"
export SLICE_ID="${SLICE_ID:-2}"
export TO_CLIENT_IFACE
export EXP4_METRICS_ORIGIN="${EXP4_METRICS_ORIGIN:-server}"
python3 /app/edge/influx_publish.py &
PIDS+=($!)

export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8080}"
export DASHBOARD_STATIC="${DASHBOARD_STATIC:-/app/frontend-console/static}"

if [ "${YOLO_BACKEND}" = "deepstream" ]; then
  log info "starting DeepStream multi-stream YOLO (N=${DS_NUM_STREAMS})"
  # ds_main serves FastAPI on :8080 and owns feeders + ds_pipeline.
  # Optional separate console proxy (same as legacy).
  if [ -f /app/frontend-console/frontend.py ]; then
    python3 /app/frontend-console/frontend.py &
    PIDS+=($!)
  fi
  exec python3 /app/edge/ds_main.py
fi

log info "starting legacy GStreamer CCTV server + FastAPI (YOLO_BACKEND=${YOLO_BACKEND})"
python3 /app/frontend-console/frontend.py &
PIDS+=($!)
exec python3 -m edge.cctv
