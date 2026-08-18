#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.10-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"

cd "${SCRIPT_DIR}"

echo "==> Building ott-ue-console:${IMAGE_TAG} (${PLATFORM})"
docker build --platform "${PLATFORM}" \
  -f Dockerfile.ue-console \
  -t "ott-ue-console:${IMAGE_TAG}" \
  -t "${REGISTRY}/ott-ue-console:${IMAGE_TAG}" \
  .

echo "==> Pushing to ${REGISTRY}"
docker push "${REGISTRY}/ott-ue-console:${IMAGE_TAG}"

echo "Done: ${REGISTRY}/ott-ue-console:${IMAGE_TAG}"
