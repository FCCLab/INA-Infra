#!/usr/bin/env bash
# Build multi-cluster resource dashboard images and push to local registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DASHBOARD_ROOT/.." && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${TAG:-latest}"

echo "==> Building backend image..."
docker build -f "$DASHBOARD_ROOT/backend/Dockerfile" -t "${REGISTRY}/dashboard/backend:${TAG}" "$DASHBOARD_ROOT/backend"

echo "==> Building frontend image..."
docker build -f "$DASHBOARD_ROOT/frontend/Dockerfile" -t "${REGISTRY}/dashboard/frontend:${TAG}" "$DASHBOARD_ROOT/frontend"

echo "==> Pushing images to registry ${REGISTRY}..."
"$REPO_ROOT/scripts/push-image-to-registry.sh" "${REGISTRY}/dashboard/backend:${TAG}" -n dashboard/backend -t "${TAG}"
"$REPO_ROOT/scripts/push-image-to-registry.sh" "${REGISTRY}/dashboard/frontend:${TAG}" -n dashboard/frontend -t "${TAG}"

echo "==> Built and pushed: ${REGISTRY}/dashboard/{backend,frontend}:${TAG}"
