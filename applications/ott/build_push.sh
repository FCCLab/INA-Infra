#!/usr/bin/env bash
# Build OTT Stream (Slice 3 / Slice C) images and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.9-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"

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

echo "==> Building application-ott-client:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" \
  -f client/Dockerfile \
  -t "application-ott-client:${IMAGE_TAG}" \
  -t "${REGISTRY}/application-ott-client:${IMAGE_TAG}" \
  -t "${REGISTRY}/hd-stream-client:hdstream-v2" \
  .

echo "==> Pushing images to ${REGISTRY}"
docker push "${REGISTRY}/application-ott:${IMAGE_TAG}"
docker push "${REGISTRY}/hd-stream-server:hdstream-v2"
docker push "${REGISTRY}/application-ott-frontend:${IMAGE_TAG}"
docker push "${REGISTRY}/application-ott-client:${IMAGE_TAG}"
docker push "${REGISTRY}/hd-stream-client:hdstream-v2"

echo "Done:"
echo "  - ${REGISTRY}/application-ott:${IMAGE_TAG}"
echo "  - ${REGISTRY}/application-ott-frontend:${IMAGE_TAG}"
echo "  - ${REGISTRY}/application-ott-client:${IMAGE_TAG}"
