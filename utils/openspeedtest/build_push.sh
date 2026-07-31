#!/usr/bin/env bash
# Build OpenSpeedTest client image and push to the lab registry.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
NAME="${OST_CLIENT_NAME:-openspeedtest-client}"
TAG="${OST_CLIENT_TAG:-latest}"
LOCAL_IMAGE="${NAME}:${TAG}"
REMOTE_IMAGE="${REGISTRY}/${NAME}:${TAG}"

echo "==> Building ${LOCAL_IMAGE}"
docker build -t "${LOCAL_IMAGE}" -f "${ROOT}/Dockerfile" "${ROOT}"

echo "==> Pushing -> ${REMOTE_IMAGE}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "${LOCAL_IMAGE}" \
  -n "${NAME}" -t "${TAG}"

echo "==> Done: ${REMOTE_IMAGE}"
echo
echo "Run (host network — same L2 as MetalLB OST VIPs):"
echo "  docker run --rm --network host ${REMOTE_IMAGE} \\"
echo "    --server http://10.1.132.11/ --duration 10 --threads 1"
echo
echo "Lab OST servers:"
echo "  mgmt     http://10.1.132.11/   (default)"
echo "  central  http://10.1.138.101/"
echo "  regional http://10.1.138.126/"
echo "  edge     http://10.1.138.151/"
