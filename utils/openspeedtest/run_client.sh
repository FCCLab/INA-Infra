#!/usr/bin/env bash
# Run the OpenSpeedTest client container against a lab OST server.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
NAME="${OST_CLIENT_NAME:-openspeedtest-client}"
TAG="${OST_CLIENT_TAG:-latest}"
IMAGE="${OST_CLIENT_IMAGE:-${REGISTRY}/${NAME}:${TAG}}"
SERVER="${OST_SERVER:-http://10.1.132.11/}"
DURATION="${DURATION:-10}"
THREADS="${THREADS:-1}"
DIRECTION="${DIRECTION:-both}"
NETWORK="${DOCKER_NETWORK:-host}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [-- speedtest.py args...]

Options:
  --server URL              OST server (default: ${SERVER})
  -d|--duration SEC         Seconds (default: ${DURATION}; 0|forever = until Ctrl+C)
  --threads|-t N            Parallel connections (default: ${THREADS})
  --dir|--direction DIR     download|upload|both (default: ${DIRECTION})
  --image REF               Image (default: ${IMAGE})
  --network NET             Docker network (default: ${NETWORK})
  --bind IP                 Source bind IP (passed to speedtest.py)
  -h, --help                Show help

Examples:
  $(basename "$0")
  $(basename "$0") --dir download -d 0
  $(basename "$0") --dir upload -d 0
  $(basename "$0") -- --server http://10.1.138.151/ -d 15 -t 4
EOF
}

extra=()
bind=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --server) SERVER="$2"; shift 2 ;;
    -d|--duration) DURATION="$2"; shift 2 ;;
    --threads|-t) THREADS="$2"; shift 2 ;;
    --dir|--direction) DIRECTION="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --network) NETWORK="$2"; shift 2 ;;
    --bind) bind="$2"; shift 2 ;;
    --) shift; extra+=("$@"); break ;;
    *) extra+=("$1"); shift ;;
  esac
done

args=(--server "$SERVER" --duration "$DURATION" --threads "$THREADS" --direction "$DIRECTION")
[[ -n "$bind" ]] && args+=(--bind "$bind")
args+=("${extra[@]}")

# Prefer local tag if present and no remote pull needed for quick loops.
if docker image inspect "${NAME}:${TAG}" >/dev/null 2>&1 && [[ "$IMAGE" == "${REGISTRY}/${NAME}:${TAG}" ]]; then
  run_image="${NAME}:${TAG}"
else
  run_image="$IMAGE"
fi

echo "==> docker run --network ${NETWORK} ${run_image} ${args[*]}"
exec docker run --rm --network "${NETWORK}" "${run_image}" "${args[@]}"
