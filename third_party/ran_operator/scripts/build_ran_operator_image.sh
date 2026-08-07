#!/usr/bin/env bash
# Build and push the INA-Infra OAI RAN controller image (CU-CP / CU-UP / DU operator).
# Source: third_party/INA-Infra-ran-oai-operators (FCCLab fork of nephio-project/oai).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SRC="${RAN_CONTROLLER_SRC:-$REPO_ROOT/third_party/INA-Infra-ran-oai-operators}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${OAI_RAN_CONTROLLER_TAG:-latest}"
IMAGE_NAME="${OAI_RAN_CONTROLLER_IMAGE_NAME:-oai-ran-controller}"
LOCAL_IMAGE="${IMAGE_NAME}:${TAG}"
PLATFORM="${OAI_RAN_CONTROLLER_PLATFORM:-linux/amd64}"

if [[ ! -f "$SRC/Dockerfile" ]]; then
  echo "error: RAN controller source not found at $SRC (init submodule: git submodule update --init third_party/INA-Infra-ran-oai-operators)" >&2
  exit 1
fi

echo "==> Building ${LOCAL_IMAGE} from ${SRC} (${PLATFORM})"
docker build \
  --platform "${PLATFORM}" \
  -t "${LOCAL_IMAGE}" \
  -f "${SRC}/Dockerfile" \
  "${SRC}"

echo "==> Pushing -> ${REGISTRY}/${IMAGE_NAME}:${TAG}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "${LOCAL_IMAGE}" \
  -n "${IMAGE_NAME}" -t "${TAG}"

echo "==> Done: ${REGISTRY}/${IMAGE_NAME}:${TAG}"
echo "Use in GitOps: OAI_RAN_OPERATOR_IMAGE=${REGISTRY}/${IMAGE_NAME}:${TAG}"
