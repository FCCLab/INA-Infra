#!/usr/bin/env bash
# Pin vLLM via the UE PDU tunnel, then run AIPerf (loop for re-trigger via restart).
set -euo pipefail

PDU_IFACE="${PDU_IFACE:-}"
PDU_ROUTE_HOSTS="${PDU_ROUTE_HOSTS:-}"
PDU_WAIT_TIMEOUT="${PDU_WAIT_TIMEOUT:-300}"
LOOP="${AIPERF_LOOP:-1}"
LOOP_SLEEP_S="${AIPERF_LOOP_SLEEP_S:-30}"

export PATH="/opt/venv/bin:${PATH}"
export HF_HOME="${HF_HOME:-/tmp/hf-cache}"
export HUGGINGFACE_HUB_CACHE="${HF_HOME}"
export HF_HUB_CACHE="${HF_HOME}"
export HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-${HF_TOKEN:-}}"

log() {
  printf '{"ts":%s,"level":"%s","event":"aiperf_entrypoint","msg":"%s"}\n' \
    "$(date +%s)" "$1" "$2"
}

pin_pdu_routes_once() {
  local h ok=0
  IFS=',' read -ra _hosts <<< "$PDU_ROUTE_HOSTS"
  for h in "${_hosts[@]}"; do
    [ -z "$h" ] && continue
    if ip route replace "${h}/32" dev "$PDU_IFACE" 2>/dev/null; then
      ok=$((ok + 1))
    fi
  done
  [ "$ok" -gt 0 ]
}

setup_pdu_routes() {
  [ -z "$PDU_IFACE" ] && return 0
  [ -z "$PDU_ROUTE_HOSTS" ] && return 0
  local elapsed=0
  while ! ip link show "$PDU_IFACE" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$PDU_WAIT_TIMEOUT" ]; then
      log warn "PDU iface ${PDU_IFACE} absent after ${PDU_WAIT_TIMEOUT}s; traffic may not use air interface"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  pin_pdu_routes_once && log info "pinned ${PDU_ROUTE_HOSTS} via ${PDU_IFACE}"
  (
    while true; do
      ip link show "$PDU_IFACE" >/dev/null 2>&1 && pin_pdu_routes_once
      sleep 10
    done
  ) &
}

run_aiperf() {
  # Prefer the image venv aiperf; fall back to aiperf_profile.sh layout.
  MODEL="${MODEL:-nvidia/Cosmos3-Nano}"
  URL="${URL:-http://10.1.132.210:8000}"
  ENDPOINT_TYPE="${ENDPOINT_TYPE:-chat}"
  TOKENIZER="${TOKENIZER:-nvidia/Cosmos3-Nano}"
  IMAGE_WIDTH_MEAN="${IMAGE_WIDTH_MEAN:-1280}"
  IMAGE_HEIGHT_MEAN="${IMAGE_HEIGHT_MEAN:-720}"
  SYNTHETIC_INPUT_TOKENS_MEAN="${SYNTHETIC_INPUT_TOKENS_MEAN:-200}"
  REQUEST_COUNT="${REQUEST_COUNT:-200}"
  CONCURRENCY="${CONCURRENCY:-8}"
  ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/aiperf-results/$(date -u +%Y%m%dT%H%M%SZ)}"
  mkdir -p "${ARTIFACT_DIR}"

  log info "aiperf profile url=${URL} model=${MODEL} requests=${REQUEST_COUNT}"
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
    --artifact-dir "${ARTIFACT_DIR}"
}

log info "entrypoint start (aiperf)"
setup_pdu_routes

if [ "${LOOP}" = "1" ]; then
  while true; do
    run_aiperf || log warn "aiperf run failed; will retry"
    log info "sleeping ${LOOP_SLEEP_S}s before next aiperf run"
    sleep "${LOOP_SLEEP_S}"
  done
else
  run_aiperf
fi
