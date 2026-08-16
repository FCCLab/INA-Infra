#!/usr/bin/env bash
# Build CCTV images and push to the lab registry.
# Analyzer is the default (frontend/backend source changes). Publisher is
# skipped unless BUILD_PUBLISHER=1 — its layers are independent and slow the
# push. Docker BuildKit caches apt/pip/torch unless requirements change.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.7-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"
BUILD_PUBLISHER="${BUILD_PUBLISHER:-0}"
export DOCKER_BUILDKIT=1

cd "${SCRIPT_DIR}"

echo "==> Building slicea-analyzer:${IMAGE_TAG} (${PLATFORM}) [BuildKit cache]"
docker build --platform "${PLATFORM}" -f edge/Dockerfile \
  -t "slicea-analyzer:${IMAGE_TAG}" \
  -t "${REGISTRY}/slicea-analyzer:${IMAGE_TAG}" \
  .

echo "==> Pushing ${REGISTRY}/slicea-analyzer:${IMAGE_TAG}"
docker push "${REGISTRY}/slicea-analyzer:${IMAGE_TAG}"

if [ "${BUILD_PUBLISHER}" = "1" ]; then
  echo "==> Building slicea-publisher:${IMAGE_TAG} (${PLATFORM})"
  docker build --platform "${PLATFORM}" -f client/Dockerfile \
    -t "slicea-publisher:${IMAGE_TAG}" \
    -t "${REGISTRY}/slicea-publisher:${IMAGE_TAG}" \
    .
  echo "==> Pushing ${REGISTRY}/slicea-publisher:${IMAGE_TAG}"
  docker push "${REGISTRY}/slicea-publisher:${IMAGE_TAG}"
else
  echo "==> Skipping publisher (set BUILD_PUBLISHER=1 to build/push it)"
fi

echo "Done:"
echo "  - ${REGISTRY}/slicea-analyzer:${IMAGE_TAG}"
if [ "${BUILD_PUBLISHER}" = "1" ]; then
  echo "  - ${REGISTRY}/slicea-publisher:${IMAGE_TAG}"
fi
