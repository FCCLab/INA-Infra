#!/usr/bin/env bash
# Build CCTV images and push to the lab registry.
# Analyzer is the default (frontend/backend source changes). Publisher is
# skipped unless BUILD_PUBLISHER=1 — its layers are independent and slow the
# push. Docker BuildKit caches apt/pip/torch unless requirements change.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.9-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"
BUILD_PUBLISHER="${BUILD_PUBLISHER:-0}"
export DOCKER_BUILDKIT=1

cd "${SCRIPT_DIR}"

echo "==> Building application-cctv:${IMAGE_TAG} (${PLATFORM}) [BuildKit cache]"
docker build --platform "${PLATFORM}" -f edge/Dockerfile \
  -t "application-cctv:${IMAGE_TAG}" \
  -t "${REGISTRY}/application-cctv:${IMAGE_TAG}" \
  .

echo "==> Pushing ${REGISTRY}/application-cctv:${IMAGE_TAG}"
docker push "${REGISTRY}/application-cctv:${IMAGE_TAG}"

echo "==> Building application-cctv-frontend:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" -f frontend/Dockerfile \
  -t "application-cctv-frontend:${IMAGE_TAG}" \
  -t "${REGISTRY}/application-cctv-frontend:${IMAGE_TAG}" \
  frontend/

echo "==> Pushing ${REGISTRY}/application-cctv-frontend:${IMAGE_TAG}"
docker push "${REGISTRY}/application-cctv-frontend:${IMAGE_TAG}"

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
else
  echo "==> Skipping legacy publisher (set BUILD_PUBLISHER=1 to build/push it)"
fi

echo "Done:"
echo "  - ${REGISTRY}/application-cctv:${IMAGE_TAG}"
echo "  - ${REGISTRY}/application-cctv-frontend:${IMAGE_TAG}"
echo "  - ${REGISTRY}/cctv-ue-console:${IMAGE_TAG}"
if [ "${BUILD_PUBLISHER}" = "1" ]; then
  echo "  - ${REGISTRY}/application-cctv-publisher:${IMAGE_TAG}"
fi
