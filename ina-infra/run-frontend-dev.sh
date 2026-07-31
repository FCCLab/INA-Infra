#!/usr/bin/env bash
# Live Vite UI (HMR) without rebuilding/pushing the frontend image.
# Host has no Node — runs via official node container + bind mount.
#
# Usage:
#   ./run-frontend-dev.sh
#   VITE_API_PROXY=http://10.1.132.200:8082 ./run-frontend-dev.sh
#
# Then open http://127.0.0.1:5180 (or http://<mgmt-ip>:5180).
# Stop any baked compose frontend first (this script does that).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-5180}"
API_PROXY="${VITE_API_PROXY:-http://host.docker.internal:8082}"
IMAGE="${FRONTEND_DEV_IMAGE:-node:20-bookworm}"

cd "$ROOT"
if docker compose ps --status running 2>/dev/null | grep -q frontend; then
  echo "Stopping compose frontend on :${PORT} ..."
  docker compose stop frontend >/dev/null
  docker compose rm -f frontend >/dev/null 2>&1 || true
fi
# Also stop a leftover named container if compose is not used.
if docker ps --format '{{.Names}}' | grep -qx 'ina-infra-frontend-1'; then
  docker stop ina-infra-frontend-1 >/dev/null
  docker rm ina-infra-frontend-1 >/dev/null 2>&1 || true
fi

TTY_FLAGS=(-i)
[[ -t 0 && -t 1 ]] && TTY_FLAGS=(-it)

# Drop a previous detached/dev container so we can rebind :5180.
docker rm -f ina-infra-frontend-dev >/dev/null 2>&1 || true

echo "Vite dev → http://0.0.0.0:${PORT}  (API proxy ${API_PROXY})"
echo "Hard-refresh the browser once (Ctrl+Shift+R) if you still see the old nginx UI."
exec docker run --rm "${TTY_FLAGS[@]}" \
  --name ina-infra-frontend-dev \
  -p "${PORT}:5180" \
  -v "${ROOT}/frontend:/app" \
  -w /app \
  --add-host=host.docker.internal:host-gateway \
  -e "VITE_API_PROXY=${API_PROXY}" \
  "${IMAGE}" \
  bash -lc 'npm install && npm run dev -- --host 0.0.0.0 --port 5180'
