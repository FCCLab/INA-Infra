#!/usr/bin/env bash
# Build ina-infra images and push to lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${TAG:-latest}"

cd "$REPO_ROOT"

echo "==> Building backend (context=repo root)"
docker build -f ina-infra/backend/Dockerfile -t "${REGISTRY}/ina-infra/backend:${TAG}" .

echo "==> Building frontend"
docker build -f ina-infra/frontend/Dockerfile -t "${REGISTRY}/ina-infra/frontend:${TAG}" ina-infra/frontend

echo "==> Pushing to ${REGISTRY}"
"$REPO_ROOT/scripts/push-image-to-registry.sh" "${REGISTRY}/ina-infra/backend:${TAG}" -n ina-infra/backend -t "${TAG}"
"$REPO_ROOT/scripts/push-image-to-registry.sh" "${REGISTRY}/ina-infra/frontend:${TAG}" -n ina-infra/frontend -t "${TAG}"

echo "Done: ${REGISTRY}/ina-infra/{backend,frontend}:${TAG}"
