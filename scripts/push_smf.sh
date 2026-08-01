#!/usr/bin/env bash
# Tag and push a locally built oai-smf image to the mgmt cluster registry.
#
#   ./scripts/push_smf.sh
#   ./scripts/push_smf.sh --tag v2.2.1-dnn-fix-1
#   ./scripts/push_smf.sh --build --tag v2.2.1-dnn-fix-2
#
# Build first (on this host):
#   ./scripts/push_smf.sh --build --tag v2.2.1-dnn-fix-1
#
# Then update ina-infra SMF op-conf image to:
#   10.1.132.30:5000/oaisoftwarealliance/oai-smf:<tag>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-v2.2.1-dnn-fix-4}"
REPO_NAME="oaisoftwarealliance/oai-smf"
DO_BUILD=0
TAG_ONLY=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Push local oai-smf Docker image to the lab registry (${REGISTRY}).

Options:
  -t, --tag TAG       Image tag (default: v2.2.1-dnn-fix-2, or IMAGE_TAG env)
  -r, --registry HOST:PORT
                      Registry address (default: ${REGISTRY})
  --build             Run nws/build_scripts/build_smf.sh before push
  --tag-only          Tag locally but do not push
  -h, --help          Show this help

Environment:
  REGISTRY            Default registry (default: 10.1.132.30:5000)
  IMAGE_TAG           Default tag when --tag is omitted

Source image (first match):
  oai-smf:\$TAG-\$ARCH, oai-smf:\$TAG, oaisoftwarealliance/oai-smf:\$TAG

Remote:
  ${REGISTRY}/${REPO_NAME}:\$TAG

Examples:
  $(basename "$0") --build --tag v2.2.1-dnn-fix-1
  IMAGE_TAG=v2.2.1-dnn-fix-2 $(basename "$0") --tag v2.2.1-dnn-fix-2
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--tag)
      IMAGE_TAG="${2:?missing value for $1}"
      shift 2
      ;;
    -r|--registry)
      REGISTRY="${2:?missing value for $1}"
      shift 2
      ;;
    --build)
      DO_BUILD=1
      shift
      ;;
    --tag-only)
      TAG_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${IMAGE_TAG}" ]]; then
  echo "IMAGE_TAG must not be empty." >&2
  exit 1
fi

ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ARCH_TAG="amd64" ;;
  aarch64|arm64) ARCH_TAG="arm64" ;;
  *) ARCH_TAG="$ARCH" ;;
esac

LOCAL_IMAGE=""
for candidate in \
  "oai-smf:${IMAGE_TAG}-${ARCH_TAG}" \
  "oai-smf:${IMAGE_TAG}" \
  "oaisoftwarealliance/oai-smf:${IMAGE_TAG}"; do
  if docker image inspect "$candidate" >/dev/null 2>&1; then
    LOCAL_IMAGE="$candidate"
    break
  fi
done

if [[ "${DO_BUILD}" -eq 1 ]]; then
  BUILD_SMF="${REPO_ROOT}/${OAI_SLICE_DIR}/nws/build_scripts/build_smf.sh"
  if [[ ! -x "${BUILD_SMF}" ]]; then
    echo "Error: build script not found or not executable: ${BUILD_SMF}" >&2
    exit 1
  fi
  echo "==> Building oai-smf:${IMAGE_TAG}..."
  "${BUILD_SMF}" --tag "${IMAGE_TAG}"
  LOCAL_IMAGE="oai-smf:${IMAGE_TAG}"
fi

if [[ -z "${LOCAL_IMAGE}" ]]; then
  cat >&2 <<EOF
Error: no local oai-smf image found for tag '${IMAGE_TAG}'.

Build on this host:
  ${REPO_ROOT}/${OAI_SLICE_DIR}/nws/build_scripts/build_smf.sh --tag ${IMAGE_TAG}

Or push with build:
  $(basename "$0") --build --tag ${IMAGE_TAG}
EOF
  exit 1
fi

PUSH_ARGS=(
  "${LOCAL_IMAGE}"
  -r "${REGISTRY}"
  -n "${REPO_NAME}"
  -t "${IMAGE_TAG}"
)
if [[ "${TAG_ONLY}" -eq 1 ]]; then
  PUSH_ARGS+=(--tag-only)
fi

echo "==> Pushing SMF ${LOCAL_IMAGE} -> ${REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"
"${SCRIPT_DIR}/push-image-to-registry.sh" "${PUSH_ARGS[@]}"

if [[ "${TAG_ONLY}" -eq 0 ]] && [[ -x "${REPO_ROOT}/utils/registry/verify_registry_image.sh" ]]; then
  echo "==> Verifying registry image..."
  "${REPO_ROOT}/utils/registry/verify_registry_image.sh" "${REPO_NAME}:${IMAGE_TAG}" || true
fi

echo ""
echo "Registry image: ${REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"
echo "Update ina-infra SMF op-conf, e.g.:"
echo "  image: '${REGISTRY}/${REPO_NAME}:${IMAGE_TAG}'"
echo "Then: ./scripts/render_ina_cn_operators_gitops.sh && push GitOps"
