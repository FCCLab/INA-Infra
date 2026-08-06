#!/usr/bin/env bash
# Build/push iperf3-n6 image (UPF N6 server + UE client helpers).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="${REPO_ROOT}/tools/iperf3-n6"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${IPERF3_N6_TAG:-latest}"
IMAGE_NAME="${IPERF3_N6_IMAGE_NAME:-iperf3-n6}"
LOCAL_IMAGE="${IMAGE_NAME}:${TAG}"
PLATFORM="${IPERF3_N6_PLATFORM:-linux/amd64}"

echo "==> Building ${LOCAL_IMAGE} from ${SRC} (${PLATFORM})"
docker build --platform "${PLATFORM}" -t "${LOCAL_IMAGE}" -f "${SRC}/Dockerfile" "${SRC}"

echo "==> Pushing -> ${REGISTRY}/${IMAGE_NAME}:${TAG}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "${LOCAL_IMAGE}" \
  -n "${IMAGE_NAME}" -t "${TAG}"

echo "==> Done: ${REGISTRY}/${IMAGE_NAME}:${TAG}"
