#!/usr/bin/env bash
# Build HD-Stream (Slice C) images and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-hdstream-v2}"

cd "${SCRIPT_DIR}"

echo "==> Building hd-stream-server:${IMAGE_TAG}"
docker build -f server/Dockerfile -t "hd-stream-server:${IMAGE_TAG}" -t "${REGISTRY}/hd-stream-server:${IMAGE_TAG}" .

echo "==> Building hd-stream-client:${IMAGE_TAG}"
docker build -f client/Dockerfile -t "hd-stream-client:${IMAGE_TAG}" -t "${REGISTRY}/hd-stream-client:${IMAGE_TAG}" .

echo "==> Pushing images to ${REGISTRY}"
docker push "${REGISTRY}/hd-stream-server:${IMAGE_TAG}"
docker push "${REGISTRY}/hd-stream-client:${IMAGE_TAG}"

echo "Done:"
echo "  - ${REGISTRY}/hd-stream-server:${IMAGE_TAG}"
echo "  - ${REGISTRY}/hd-stream-client:${IMAGE_TAG}"
