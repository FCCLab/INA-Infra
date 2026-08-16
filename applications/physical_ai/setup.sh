#!/usr/bin/env bash
# One-time setup: Reasoner venv with release-tested vLLM + vllm-cosmos3.
# CUDA 13 drivers -> cu130 / vllm==0.21.0 (this host).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${SCRIPT_DIR}/.venv}"
TORCH_BACKEND="${TORCH_BACKEND:-cu130}"
VLLM_VERSION="${VLLM_VERSION:-0.21.0}"
VLLM_COSMOS3_SPEC="${VLLM_COSMOS3_SPEC:-vllm-cosmos3 @ git+https://github.com/NVIDIA/cosmos-framework.git#subdirectory=packages/vllm-cosmos3}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found. Install: https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "Creating venv at ${VENV_DIR} (Python 3.13)"
uv venv --python 3.13 --seed --managed-python "${VENV_DIR}"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "Installing vllm==${VLLM_VERSION} (${TORCH_BACKEND}) + vllm-cosmos3 + openai"
uv pip install --torch-backend="${TORCH_BACKEND}" \
  "vllm==${VLLM_VERSION}" \
  "${VLLM_COSMOS3_SPEC}" \
  openai

echo
echo "Setup complete. Activate with:"
echo "  source ${VENV_DIR}/bin/activate"
echo "Then start the Reasoner:"
echo "  ${SCRIPT_DIR}/serve_cosmos3_nano_reasoner.sh"