#!/usr/bin/env bash
# Build Cosmos3 vLLM for amd64 (A40) and/or arm64 (GH200) and push to the lab registry.
# Default: both platforms + a multi-arch tag kubelet can pull by node arch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_CONFIG="${SSH_CONFIG:-/home/fcp/INA-Infra/utils/ssh_config/config}"
ARM_HOST="${ARM_HOST:-gpu-gh82}"
ARM_DIR="${ARM_DIR:-/tmp/cosmo3-vllm-build}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.7}"
BUILD_AMD64="${BUILD_AMD64:-1}"
BUILD_ARM64="${BUILD_ARM64:-1}"
PUSH_MANIFEST="${PUSH_MANIFEST:-1}"

AMD64_TAG="${AMD64_TAG:-${IMAGE_TAG}-amd64}"
ARM64_TAG="${ARM64_TAG:-${IMAGE_TAG}-arm64-cu128}"
NAME="cosmo3-vllm"

ensure_insecure_registry() {
  local host="$1"
  ssh -F "${SSH_CONFIG}" "${host}" bash -s -- "${REGISTRY}" <<'EOS'
set -euo pipefail
reg="$1"
sudo mkdir -p /etc/docker
sudo python3 - <<PY
import json
from pathlib import Path
reg = "${reg}"
p = Path("/etc/docker/daemon.json")
data = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
regs = set(data.get("insecure-registries") or [])
if reg not in regs:
    regs.add(reg)
    data["insecure-registries"] = sorted(regs)
    Path("/tmp/daemon.json").write_text(json.dumps(data, indent=2) + "\n")
    print("updated", data["insecure-registries"])
else:
    print("already listed", sorted(regs))
    raise SystemExit(0)
PY
if [[ -f /tmp/daemon.json ]]; then
  sudo mv /tmp/daemon.json /etc/docker/daemon.json
  sudo systemctl restart docker
fi
EOS
}

if [[ "${BUILD_AMD64}" == "1" ]]; then
  echo "==> Building ${REGISTRY}/${NAME}:${AMD64_TAG} (linux/amd64, local)"
  docker build --platform linux/amd64 --provenance=false --sbom=false \
    -f "${SCRIPT_DIR}/Dockerfile.vllm" \
    -t "${NAME}:${AMD64_TAG}" \
    -t "${REGISTRY}/${NAME}:${AMD64_TAG}" \
    "${SCRIPT_DIR}"
  echo "==> Pushing ${REGISTRY}/${NAME}:${AMD64_TAG}"
  docker push "${REGISTRY}/${NAME}:${AMD64_TAG}"
fi

if [[ "${BUILD_ARM64}" == "1" ]]; then
  echo "==> Building ${REGISTRY}/${NAME}:${ARM64_TAG} (linux/arm64 on ${ARM_HOST})"
  ensure_insecure_registry "${ARM_HOST}"
  ssh -F "${SSH_CONFIG}" "${ARM_HOST}" "sudo rm -rf '${ARM_DIR}' && mkdir -p '${ARM_DIR}' && sudo chown -R \$(id -u):\$(id -g) '${ARM_DIR}'"
  scp -F "${SSH_CONFIG}" \
    "${SCRIPT_DIR}/Dockerfile.vllm" \
    "${SCRIPT_DIR}/entrypoint-vllm.sh" \
    "${ARM_HOST}:${ARM_DIR}/"
  ssh -F "${SSH_CONFIG}" "${ARM_HOST}" bash -s -- "${ARM_DIR}" "${NAME}:${ARM64_TAG}" "${REGISTRY}/${NAME}:${ARM64_TAG}" <<'EOS'
set -euo pipefail
cd "$1"
docker build -f Dockerfile.vllm -t "$2" -t "$3" .
echo "==> Pushing $3"
docker push "$3"
docker image ls "$3"
EOS
fi

if [[ "${PUSH_MANIFEST}" == "1" && "${BUILD_AMD64}" == "1" && "${BUILD_ARM64}" == "1" ]]; then
  echo "==> Creating multi-arch ${REGISTRY}/${NAME}:${IMAGE_TAG}"
  if ! docker buildx imagetools create --insecure -t "${REGISTRY}/${NAME}:${IMAGE_TAG}" \
    "${REGISTRY}/${NAME}:${AMD64_TAG}" \
    "${REGISTRY}/${NAME}:${ARM64_TAG}"; then
    echo "WARN: multi-arch tag ${IMAGE_TAG} not created (HTTP registry). Deploy uses ${AMD64_TAG} / ${ARM64_TAG} by node arch."
  fi
fi

echo "Done:"
[[ "${BUILD_AMD64}" == "1" ]] && echo "  - ${REGISTRY}/${NAME}:${AMD64_TAG}"
[[ "${BUILD_ARM64}" == "1" ]] && echo "  - ${REGISTRY}/${NAME}:${ARM64_TAG}"
[[ "${PUSH_MANIFEST}" == "1" && "${BUILD_AMD64}" == "1" && "${BUILD_ARM64}" == "1" ]] && echo "  - ${REGISTRY}/${NAME}:${IMAGE_TAG} (amd64+arm64)"
