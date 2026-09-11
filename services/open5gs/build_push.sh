#!/usr/bin/env bash
# Build the FCCLab/5gc-open5gs 5GC image and push it to the lab registry.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${OPEN5GS_TAG:-v2.7.0}"
OS_VERSION="${OS_VERSION:-22.04}"
OPEN5GS_VERSION="${OPEN5GS_VERSION:-v2.7.0}"
LOCAL_IMAGE="5gc-open5gs-5gc:${TAG}"
REMOTE_NAME="${OPEN5GS_REMOTE_NAME:-open5gs/5gc}"

echo "==> Building ${LOCAL_IMAGE} (linux/amd64, Open5GS ${OPEN5GS_VERSION})"
docker build \
  --platform linux/amd64 \
  --target open5gs \
  --build-arg "OS_VERSION=${OS_VERSION}" \
  --build-arg "OPEN5GS_VERSION=${OPEN5GS_VERSION}" \
  -t "${LOCAL_IMAGE}" \
  -f "${ROOT}/Dockerfile" \
  "${ROOT}"

echo "==> Pushing ${LOCAL_IMAGE} -> ${REGISTRY}/${REMOTE_NAME}:${TAG}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "${LOCAL_IMAGE}" \
  -n "${REMOTE_NAME}" -t "${TAG}"

echo "==> Done. Image: ${REGISTRY}/${REMOTE_NAME}:${TAG}"
echo "Next:"
echo "  ./scripts/render_open5gs_gitops.sh"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Open5GS 5GC on edge' edge"
