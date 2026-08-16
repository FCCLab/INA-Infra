#!/usr/bin/env bash
# Deploy Cosmos3 Nano Reasoner (OpenAI-compatible API) via native vLLM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"

VENV_DIR="${VENV_DIR:-${SCRIPT_DIR}/.venv}"
HOST_PORT="${HOST_PORT:-8000}"
GPU_DEVICE="${GPU_DEVICE:-0}"
PID_FILE="${PID_FILE:-${SCRIPT_DIR}/cosmos3-nano-reasoner.pid}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/cosmos3-nano-reasoner.log}"
MEDIA_PATH="${MEDIA_PATH:-/}"

export HF_TOKEN
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
export HF_HOME
export HUGGINGFACE_HUB_CACHE="${HF_HOME}"
export HF_HUB_CACHE="${HF_HOME}"
# Ensure FlashInfer / ninja from the venv are on PATH when launched via nohup.
export PATH="${VENV_DIR}/bin:${PATH}"

# Host has GCC 13 + CUDA toolkit 12.3 only. FlashInfer JIT sampling fails with:
#   "gcc versions later than 12 are not supported!"
# Disable FlashInfer sampler so vLLM uses the native sampler instead.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

mkdir -p "${HF_HOME}"

if [[ ! -x "${VENV_DIR}/bin/vllm" ]]; then
  echo "venv missing or incomplete at ${VENV_DIR}"
  echo "Running setup first..."
  "${SCRIPT_DIR}/setup_cosmos3_reasoner.sh"
fi

if [[ -f "${PID_FILE}" ]]; then
  if kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "Reasoner already running (pid $(cat "${PID_FILE}")). Stop it first:"
    echo "  ${SCRIPT_DIR}/stop_cosmos3_nano_reasoner.sh"
    exit 1
  fi
  rm -f "${PID_FILE}"
fi

echo "Starting Cosmos3 Nano Reasoner on 0.0.0.0:${HOST_PORT}"
echo "  HF cache : ${HF_HOME}"
echo "  GPU      : ${GPU_DEVICE}"
echo "  FlashInfer sampler: ${VLLM_USE_FLASHINFER_SAMPLER}"
echo "  log      : ${LOG_FILE}"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

nohup env CUDA_VISIBLE_DEVICES="${GPU_DEVICE}" \
  VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER}" \
  HF_TOKEN="${HF_TOKEN}" \
  HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
  HF_HOME="${HF_HOME}" \
  HUGGINGFACE_HUB_CACHE="${HF_HOME}" \
  HF_HUB_CACHE="${HF_HOME}" \
  PATH="${PATH}" \
  vllm serve nvidia/Cosmos3-Nano \
    --hf-overrides '{"architectures": ["Cosmos3ReasonerForConditionalGeneration"]}' \
    --tensor-parallel-size 1 \
    --mm-encoder-tp-mode data \
    --async-scheduling \
    --allowed-local-media-path "${MEDIA_PATH}" \
    --media-io-kwargs '{"video": {"num_frames": -1}}' \
    --host 0.0.0.0 \
    --port "${HOST_PORT}" \
  >"${LOG_FILE}" 2>&1 &

echo $! >"${PID_FILE}"

echo
echo "Reasoner started (pid $(cat "${PID_FILE}"))."
echo "Follow logs:"
echo "  tail -f ${LOG_FILE}"
echo
echo "Wait for readiness:"
echo "  curl http://localhost:${HOST_PORT}/v1/models"