#!/usr/bin/env bash
# Start Cosmos3 Nano Reasoner (OpenAI-compatible) via native vLLM.
set -euo pipefail

HOST_PORT="${HOST_PORT:-${PORT:-8000}}"
GPU_DEVICE="${GPU_DEVICE:-0}"
MEDIA_PATH="${MEDIA_PATH:-/}"
MODEL="${MODEL:-${MODEL_NAME:-nvidia/Cosmos3-Nano}}"
HF_HOME="${HF_HOME:-/models}"
# Conservative default: GH200 often shares the GPU; A40 (48G) can raise this via env.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.6}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"

export HF_HOME
export HUGGINGFACE_HUB_CACHE="${HF_HOME}"
export HF_HUB_CACHE="${HF_HOME}"
export HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-${HF_TOKEN:-}}"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
export CUDA_VISIBLE_DEVICES="${GPU_DEVICE}"
export PATH="/opt/venv/bin:${PATH}"

mkdir -p "${HF_HOME}"

echo "Starting Cosmos3 Nano Reasoner on 0.0.0.0:${HOST_PORT}"
echo "  model    : ${MODEL}"
echo "  HF cache : ${HF_HOME}"
echo "  GPU      : ${GPU_DEVICE}"
echo "  gpu-mem  : ${GPU_MEMORY_UTILIZATION}"
echo "  max-len  : ${MAX_MODEL_LEN}"
echo "  FlashInfer sampler: ${VLLM_USE_FLASHINFER_SAMPLER}"

exec vllm serve "${MODEL}" \
  --hf-overrides '{"architectures": ["Cosmos3ReasonerForConditionalGeneration"]}' \
  --tensor-parallel-size 1 \
  --mm-encoder-tp-mode data \
  --async-scheduling \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --allowed-local-media-path "${MEDIA_PATH}" \
  --media-io-kwargs '{"video": {"num_frames": -1}}' \
  --host 0.0.0.0 \
  --port "${HOST_PORT}" \
  "$@"
