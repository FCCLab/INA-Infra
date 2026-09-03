#!/usr/bin/env bash
# Profile Cosmos3 Nano Reasoner with AIPerf (chat / multimodal synthetic images).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${DEPLOY_DIR}/.env"

VENV_DIR="${VENV_DIR:-${SCRIPT_DIR}/.venv}"
RESULTS_DIR="${RESULTS_DIR:-${SCRIPT_DIR}/results}"
ARTIFACT_DIR="${ARTIFACT_DIR:-${RESULTS_DIR}/$(date -u +%Y%m%dT%H%M%SZ)}"

MODEL="${MODEL:-nvidia/Cosmos3-Nano}"
URL="${URL:-http://localhost:8000}"
ENDPOINT_TYPE="${ENDPOINT_TYPE:-chat}"
TOKENIZER="${TOKENIZER:-nvidia/Cosmos3-Nano}"
IMAGE_WIDTH_MEAN="${IMAGE_WIDTH_MEAN:-1280}"
IMAGE_HEIGHT_MEAN="${IMAGE_HEIGHT_MEAN:-720}"
SYNTHETIC_INPUT_TOKENS_MEAN="${SYNTHETIC_INPUT_TOKENS_MEAN:-200}"
REQUEST_COUNT="${REQUEST_COUNT:-200}"
CONCURRENCY="${CONCURRENCY:-8}"

export HF_TOKEN
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
export HF_HOME
export HUGGINGFACE_HUB_CACHE="${HF_HOME}"
export HF_HUB_CACHE="${HF_HOME}"

if [[ ! -x "${VENV_DIR}/bin/aiperf" ]]; then
  echo "aiperf venv missing; running setup..."
  "${SCRIPT_DIR}/setup_aiperf.sh"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
mkdir -p "${ARTIFACT_DIR}"

echo "AIPerf profile"
echo "  model       : ${MODEL}"
echo "  url         : ${URL}"
echo "  tokenizer   : ${TOKENIZER}"
echo "  image       : ${IMAGE_WIDTH_MEAN}x${IMAGE_HEIGHT_MEAN}"
echo "  input tokens: ${SYNTHETIC_INPUT_TOKENS_MEAN}"
echo "  requests    : ${REQUEST_COUNT}"
echo "  concurrency : ${CONCURRENCY}"
echo "  artifacts   : ${ARTIFACT_DIR}"
echo

aiperf profile \
  --model "${MODEL}" \
  --url "${URL}" \
  --endpoint-type "${ENDPOINT_TYPE}" \
  --tokenizer "${TOKENIZER}" \
  --image-width-mean "${IMAGE_WIDTH_MEAN}" \
  --image-height-mean "${IMAGE_HEIGHT_MEAN}" \
  --synthetic-input-tokens-mean "${SYNTHETIC_INPUT_TOKENS_MEAN}" \
  --streaming \
  --request-count "${REQUEST_COUNT}" \
  --concurrency "${CONCURRENCY}" \
  --artifact-dir "${ARTIFACT_DIR}" \
  "$@"

echo
echo "Done. Results under: ${ARTIFACT_DIR}"