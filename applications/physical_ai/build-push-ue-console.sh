#!/usr/bin/env bash
# Build amd64 Physical AI UE console image (backend + frontend) and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.18-amd64}"
LOCAL_TAG="cosmo3-ue-console:${IMAGE_TAG}"
REMOTE_TAG="${REGISTRY}/cosmo3-ue-console:${IMAGE_TAG}"

cd "${SCRIPT_DIR}"
echo "==> Building ${LOCAL_TAG} (linux/amd64)"
docker build --platform linux/amd64 -f Dockerfile.ue-console -t "${LOCAL_TAG}" -t "${REMOTE_TAG}" .

echo "==> Pushing ${REMOTE_TAG}"
docker push "${REMOTE_TAG}"
echo "Done: ${REMOTE_TAG}"
