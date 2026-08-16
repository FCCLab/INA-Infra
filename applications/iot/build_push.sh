#!/usr/bin/env bash
# Build Background IoT (Slice D) images and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.5-amd64}"

cd "${SCRIPT_DIR}"

echo "==> Building sliced-edge:${IMAGE_TAG}"
docker build -f edge/Dockerfile -t "sliced-edge:${IMAGE_TAG}" -t "${REGISTRY}/sliced-edge:${IMAGE_TAG}" .

echo "==> Building sliced-client:${IMAGE_TAG}"
docker build -f client/Dockerfile -t "sliced-client:${IMAGE_TAG}" -t "${REGISTRY}/sliced-client:${IMAGE_TAG}" .

echo "==> Pushing images to ${REGISTRY}"
docker push "${REGISTRY}/sliced-edge:${IMAGE_TAG}"
docker push "${REGISTRY}/sliced-client:${IMAGE_TAG}"

echo "Done:"
echo "  - ${REGISTRY}/sliced-edge:${IMAGE_TAG}"
echo "  - ${REGISTRY}/sliced-client:${IMAGE_TAG}"
