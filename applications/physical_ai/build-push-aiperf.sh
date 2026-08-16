#!/usr/bin/env bash
# Build amd64 AIPerf image locally and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.5-amd64}"
LOCAL_TAG="cosmo3-aiperf:${IMAGE_TAG}"
REMOTE_TAG="${REGISTRY}/cosmo3-aiperf:${IMAGE_TAG}"

cd "${SCRIPT_DIR}"
echo "==> Building ${LOCAL_TAG} (amd64)"
docker build -f Dockerfile.aiperf -t "${LOCAL_TAG}" .

echo "==> Tagging ${REMOTE_TAG}"
docker tag "${LOCAL_TAG}" "${REMOTE_TAG}"

echo "==> Pushing ${REMOTE_TAG}"
docker push "${REMOTE_TAG}"

echo "Done: ${REMOTE_TAG}"
