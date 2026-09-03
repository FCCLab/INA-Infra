#!/usr/bin/env bash
# Back-compat wrapper: arm64 vLLM on gpu-gh82 only.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BUILD_AMD64="${BUILD_AMD64:-0}"
export BUILD_ARM64="${BUILD_ARM64:-1}"
export PUSH_MANIFEST="${PUSH_MANIFEST:-0}"
export ARM_HOST="${REMOTE_HOST:-gpu-gh82}"
export IMAGE_TAG="${IMAGE_TAG:-nws-v0.7}"
exec "${SCRIPT_DIR}/build-push-vllm.sh"
