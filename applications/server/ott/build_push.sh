#!/usr/bin/env bash
# Build OTT Stream (Slice 3) images and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.10-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"
# Legacy client image is obsolete; UE path uses ott-ue-console (Dockerfile.ue-console).
BUILD_LEGACY_CLIENT="${BUILD_LEGACY_CLIENT:-0}"

cd "${SCRIPT_DIR}"

echo "==> Building application-ott:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" \
  -f server/Dockerfile \
  -t "application-ott:${IMAGE_TAG}" \
  -t "${REGISTRY}/application-ott:${IMAGE_TAG}" \
  -t "${REGISTRY}/hd-stream-server:hdstream-v2" \
  .

echo "==> Building application-ott-frontend:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" \
  -f frontend/Dockerfile \
  -t "application-ott-frontend:${IMAGE_TAG}" \
  -t "${REGISTRY}/application-ott-frontend:${IMAGE_TAG}" \
  frontend/

echo "==> Building ott-ue-console:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" \
  -f Dockerfile.ue-console \
  -t "ott-ue-console:${IMAGE_TAG}" \
  -t "${REGISTRY}/ott-ue-console:${IMAGE_TAG}" \
  .

if [[ "${BUILD_LEGACY_CLIENT}" == "1" ]]; then
  echo "==> Building application-ott-client:${IMAGE_TAG} (legacy)"
  docker build --platform "${PLATFORM}" \
    -f client/Dockerfile \
    -t "application-ott-client:${IMAGE_TAG}" \
    -t "${REGISTRY}/application-ott-client:${IMAGE_TAG}" \
    .
fi

echo "==> Pushing images to ${REGISTRY}"
docker push "${REGISTRY}/application-ott:${IMAGE_TAG}"
docker push "${REGISTRY}/hd-stream-server:hdstream-v2"
docker push "${REGISTRY}/application-ott-frontend:${IMAGE_TAG}"
docker push "${REGISTRY}/ott-ue-console:${IMAGE_TAG}"
if [[ "${BUILD_LEGACY_CLIENT}" == "1" ]]; then
  docker push "${REGISTRY}/application-ott-client:${IMAGE_TAG}"
fi

echo "Done:"
echo "  - ${REGISTRY}/application-ott:${IMAGE_TAG}"
echo "  - ${REGISTRY}/application-ott-frontend:${IMAGE_TAG}"
echo "  - ${REGISTRY}/ott-ue-console:${IMAGE_TAG}"
