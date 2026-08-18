#!/usr/bin/env bash
# Build amd64 throughput-statistics image and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.2-amd64}"
LOCAL_TAG="throughput-statistics:${IMAGE_TAG}"
REMOTE_TAG="${REGISTRY}/throughput-statistics:${IMAGE_TAG}"

cd "${SCRIPT_DIR}"
echo "==> Building ${LOCAL_TAG} (linux/amd64)"
docker build --platform linux/amd64 -t "${LOCAL_TAG}" -t "${REMOTE_TAG}" .

echo "==> Pushing ${REMOTE_TAG}"
docker push "${REMOTE_TAG}"
echo "Done: ${REMOTE_TAG}"
