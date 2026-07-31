#!/usr/bin/env bash
# Wait until NRF HTTP (oai-nrf:80) responds to discovery queries.
#
# OAI NRF speaks HTTP/2 only — probes use curl --http2-prior-knowledge.
# Use --ready-only to accept deployment Ready without HTTP probe.
#
# Usage:
#   ./scripts/ina-infra-wait-nrf-http.sh
#   ./scripts/ina-infra-wait-nrf-http.sh --timeout 120
#   ./scripts/ina-infra-wait-nrf-http.sh --ready-only
#
# Env: PROFILE_NS / INA_NS, INA_CENTRAL_CONTEXT, NRF_WAIT_SEC.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
CENTRAL_CTX="${INA_CENTRAL_CONTEXT:-central@central}"
NRF_DEPLOY="${INA_NRF_DEPLOY:-nrf-core}"
TIMEOUT_SEC="${NRF_WAIT_SEC:-120}"
POLL_SEC="${NRF_POLL_SEC:-5}"
READY_ONLY=0
DISC_URL='http://oai-nrf:80/nnrf-disc/v1/nf-instances?target-nf-type=SMF&requester-nf-type=AMF'

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --timeout) TIMEOUT_SEC="${2:?}"; shift 2 ;;
    --poll) POLL_SEC="${2:?}"; shift 2 ;;
    --ns) NS="${2:?}"; shift 2 ;;
    --context) CENTRAL_CTX="${2:?}"; shift 2 ;;
    --ready-only) READY_ONLY=1; shift ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

nrf_pod_ready() {
  kubectl --context "$CENTRAL_CTX" -n "$NS" get "deploy/$NRF_DEPLOY" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -qx '1'
}

# NRF SBI is HTTP/2; curl without --http2-prior-knowledge hangs or returns 000.
nrf_http_ok() {
  local code
  code="$(
    kubectl --context "$CENTRAL_CTX" -n "$NS" run "nrf-wait-$$" \
      --rm -i --restart=Never --image=curlimages/curl:8.5.0 \
      --command -- curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      --http2-prior-knowledge "$DISC_URL" 2>/dev/null || true
  )"
  [[ "$code" =~ ^[23] ]]
}

echo "Waiting up to ${TIMEOUT_SEC}s for NRF HTTP/2 (ns=$NS oai-nrf:80)"
if [[ "$READY_ONLY" == "1" ]]; then
  echo "  mode: ready-only (skip HTTP probe)"
fi

deadline=$((SECONDS + TIMEOUT_SEC))
while (( SECONDS < deadline )); do
  if nrf_pod_ready; then
    if [[ "$READY_ONLY" == "1" ]]; then
      echo "OK  NRF deployment Ready (--ready-only)"
      exit 0
    fi
    if nrf_http_ok; then
      echo "OK  NRF HTTP/2 discovery responsive"
      exit 0
    fi
    echo "  WARN NRF pod Ready but HTTP/2 discovery not OK (retry in ${POLL_SEC}s)"
  else
    echo "  ... NRF deployment not Ready yet (retry in ${POLL_SEC}s)"
  fi
  sleep "$POLL_SEC"
done

if nrf_pod_ready && [[ "$READY_ONLY" != "1" ]]; then
  echo "RESULT: FAIL — NRF pod Ready but HTTP/2 not responding within ${TIMEOUT_SEC}s" >&2
else
  echo "RESULT: FAIL — NRF not ready within ${TIMEOUT_SEC}s" >&2
fi
exit 1
