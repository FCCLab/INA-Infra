#!/usr/bin/env bash
# Build CCTV client images (cctv-ue-console & optional publisher) and push to registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.9-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"
BUILD_PUBLISHER="${BUILD_PUBLISHER:-0}"

cd "${SCRIPT_DIR}"

echo "==> Building cctv-ue-console:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" -f Dockerfile.ue-console \
  -t "cctv-ue-console:${IMAGE_TAG}" \
  -t "${REGISTRY}/cctv-ue-console:${IMAGE_TAG}" \
  .

echo "==> Pushing ${REGISTRY}/cctv-ue-console:${IMAGE_TAG}"
docker push "${REGISTRY}/cctv-ue-console:${IMAGE_TAG}"

if [ "${BUILD_PUBLISHER}" = "1" ]; then
  echo "==> Building application-cctv-publisher:${IMAGE_TAG} (${PLATFORM})"
  docker build --platform "${PLATFORM}" -f client/Dockerfile \
    -t "application-cctv-publisher:${IMAGE_TAG}" \
    -t "${REGISTRY}/application-cctv-publisher:${IMAGE_TAG}" \
    .
  echo "==> Pushing ${REGISTRY}/application-cctv-publisher:${IMAGE_TAG}"
  docker push "${REGISTRY}/application-cctv-publisher:${IMAGE_TAG}"
fi

echo "Done:"
echo "  - ${REGISTRY}/cctv-ue-console:${IMAGE_TAG}"
if [ "${BUILD_PUBLISHER}" = "1" ]; then
  echo "  - ${REGISTRY}/application-cctv-publisher:${IMAGE_TAG}"
fi
