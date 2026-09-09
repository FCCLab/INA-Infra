#!/usr/bin/env bash
# CCTV UE: MediaMTX (N annotated pulls) + backend :8090 + console :80
set -euo pipefail

ROLE="${CONSOLE_ROLE:-all}"
CONSOLE_IP="${CONSOLE_IP:-}"
DS_NUM_STREAMS="${DS_NUM_STREAMS:-4}"

case "${SCHEME_ID:-}${EXP4_NO5G:-}" in
  *exp4-no5g*|1|true|True|yes|YES)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-net1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net2}"
    ;;
  *)
    TO_SERVER_IFACE="${TO_SERVER_IFACE:-${PDU_IFACE:-oaitun_ue1}}"
    CONSOLE_IFACE="${CONSOLE_IFACE:-net1}"
    ;;
esac
export TO_SERVER_IFACE CONSOLE_IFACE DS_NUM_STREAMS
for _p in /exp4/ifaces.sh /app/common/ifaces.sh; do
  [ -f "$_p" ] || continue
  # shellcheck disable=SC1090
  . "$_p"
  exp4_client_ifaces || true
  break
done
export EXP4_METRICS_ORIGIN="${EXP4_METRICS_ORIGIN:-client}"
export EXP4_APP_TYPE="${EXP4_APP_TYPE:-exp4-s2}"
export SLICE_ID="${SLICE_ID:-2}"

if [ -n "${CONSOLE_IP}" ] && ip link show dev "$CONSOLE_IFACE" >/dev/null 2>&1; then
  if command -v arping >/dev/null 2>&1; then
    arping -c 2 -U -I "$CONSOLE_IFACE" "${CONSOLE_IP}" >/dev/null 2>&1 || true
  fi
fi

export MTX_SOURCE_HOST="${MTX_SOURCE_HOST:-${RTSP_TARGET_HOST:-${TARGET_SERVER_IP:-10.1.137.212}}}"
export MTX_SOURCE_RTSP_PORT="${MTX_SOURCE_RTSP_PORT:-8555}"
export MTX_HLS_URL="${MTX_HLS_URL:-http://127.0.0.1:8888}"
export MTX_WHEP_URL="${MTX_WHEP_URL:-http://127.0.0.1:8889}"
export MTX_API_URL="${MTX_API_URL:-http://127.0.0.1:9997}"
ICE_HOST="${CONSOLE_IP:-10.1.137.222}"
export MTX_WEBRTCADDITIONALHOSTS="${ICE_HOST}"

# Legacy single-path env (kept for status compatibility).
export MTX_LOCAL_PATH="${MTX_LOCAL_PATH:-annotated1}"
export MTX_SOURCE_PATH="${MTX_SOURCE_PATH:-annotated/cam1}"
export MTX_SOURCE_RTSP="${MTX_SOURCE_RTSP:-rtsp://${MTX_SOURCE_HOST}:${MTX_SOURCE_RTSP_PORT}/${MTX_SOURCE_PATH}}"

PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do
    kill -TERM "${p}" 2>/dev/null || true
  done
}
trap cleanup SIGTERM SIGINT EXIT

python3 /usr/local/bin/exp4_influx_publish.py &
PIDS+=($!)

start_mediamtx() {
  local src="${MTX_CONF:-/app/mediamtx.yml}"
  local dst="/tmp/mediamtx.yml"
  local paths="" i
  for i in $(seq 1 "${DS_NUM_STREAMS}"); do
    paths="${paths}  annotated${i}:
    source: rtsp://${MTX_SOURCE_HOST}:${MTX_SOURCE_RTSP_PORT}/annotated/cam${i}
    sourceOnDemand: no
"
  done
  # Indent block for YAML under paths:
  python3 - <<PY
from pathlib import Path
src = Path("${src}")
dst = Path("${dst}")
text = src.read_text()
paths = """${paths}"""
# Replace only the real placeholders (not comment text). Use count=1 for PATHS.
if "__PATHS_BLOCK__" not in text:
    raise SystemExit("mediamtx.yml missing __PATHS_BLOCK__ placeholder")
text = text.replace("__PATHS_BLOCK__", paths.rstrip() + "\n", 1)
text = text.replace("__CONSOLE_IP__", "${ICE_HOST}")
if "__PATHS_BLOCK__" in text:
    raise SystemExit("mediamtx.yml still contains __PATHS_BLOCK__ after replace (comment?)")
dst.write_text(text)
print(f'wrote {dst} streams=${DS_NUM_STREAMS} host=${MTX_SOURCE_HOST}')
PY
  echo "{\"level\":\"info\",\"event\":\"entrypoint\",\"msg\":\"starting UE MediaMTX N=${DS_NUM_STREAMS} host=${MTX_SOURCE_HOST} ice=${ICE_HOST}\"}"
  pkill -TERM mediamtx 2>/dev/null || true
  sleep 0.4
  mediamtx "${dst}" &
  PIDS+=($!)
  local i
  for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:9997/v3/config/global/get >/dev/null 2>&1; then
      echo "{\"level\":\"info\",\"event\":\"entrypoint\",\"msg\":\"UE MediaMTX API ready\"}"
      return 0
    fi
    sleep 0.25
  done
  echo "{\"level\":\"warn\",\"event\":\"entrypoint\",\"msg\":\"UE MediaMTX API not ready yet\"}"
}

if [ "${START_MEDIAMTX:-true}" != "false" ] && [ "${ROLE}" != "frontend" ]; then
  (
    # Pulls must start after PDU + /32 pin; otherwise TCP sticks to net2 forever.
    while true; do
      if command -v exp4_detect_to_server >/dev/null 2>&1; then
        live="$(exp4_detect_to_server || true)"
      else
        live=""
        ip -4 addr show dev "${TO_SERVER_IFACE}" 2>/dev/null | grep -q 'inet ' && live="${TO_SERVER_IFACE}"
      fi
      if [ -n "${live}" ]; then
        exp4_pin_to_server "${live}" 2>/dev/null || true
        start_mediamtx
        wait || true
        echo "{\"level\":\"warn\",\"event\":\"entrypoint\",\"msg\":\"UE MediaMTX exited; restarting after pin\"}"
        sleep 1
        continue
      fi
      sleep 1
    done
  ) &
  PIDS+=($!)
fi

if [ "${ROLE}" = "frontend" ]; then
  exec python3 /app/frontend-console/frontend.py
elif [ "${ROLE}" = "backend" ]; then
  exec python3 /app/backend/backend.py
else
  python3 /app/backend/backend.py &
  BACKEND_PID=$!
  python3 /app/frontend-console/frontend.py &
  FRONTEND_PID=$!
  PIDS+=("${BACKEND_PID}" "${FRONTEND_PID}")
  wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
fi
