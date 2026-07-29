#!/usr/bin/env bash
# Wait until ina-infra SMF has registered with NRF.
#
# Checks SMF logs for NRF registration success, or NRF logs for SMF profile
# matching the current smf-core pod IP.
#
# Usage:
#   ./scripts/ina-infra-wait-smf-nrf.sh
#   ./scripts/ina-infra-wait-smf-nrf.sh --timeout 180
#   ./scripts/ina-infra-wait-smf-nrf.sh --since 10m
#
# Env: PROFILE_NS / INA_NS, INA_SMF_CONTEXT, NRF_WAIT_SEC, NRF_LOG_SINCE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
NRF_DEPLOY="${INA_NRF_DEPLOY:-nrf-core}"
TIMEOUT_SEC="${NRF_WAIT_SEC:-120}"
SINCE="${NRF_LOG_SINCE:-5m}"
POLL_SEC="${NRF_POLL_SEC:-5}"

SMF_NRF_OK_RE='HTTP code \(201\)|NF registered|Register NF Instance Response|registration to NRF was successful'

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --timeout) TIMEOUT_SEC="${2:?}"; shift 2 ;;
    --since) SINCE="${2:?}"; shift 2 ;;
    --poll) POLL_SEC="${2:?}"; shift 2 ;;
    --ns) NS="${2:?}"; shift 2 ;;
    --context) SMF_CTX="${2:?}"; shift 2 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

smf_nrf_ok() {
  local smf_ip logs nrf_logs
  smf_ip="$(
    kubectl --context "$SMF_CTX" -n "$NS" get pods \
      -l workload.nephio.org/oai=smf \
      -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true
  )"
  [[ -n "$smf_ip" ]] || return 1
  logs="$(
    kubectl --context "$SMF_CTX" -n "$NS" logs deploy/smf-core \
      --since="$SINCE" 2>/dev/null || true
  )"
  if echo "$logs" | grep -qiE "$SMF_NRF_OK_RE"; then
    return 0
  fi
  nrf_logs="$(
    kubectl --context "$SMF_CTX" -n "$NS" logs "deploy/$NRF_DEPLOY" -c nrf-core \
      --since="$SINCE" 2>/dev/null || true
  )"
  echo "$nrf_logs" | grep -q '"nfType":"SMF"' \
    && echo "$nrf_logs" | grep -q "$smf_ip" \
    && echo "$nrf_logs" | grep -q '"nfStatus":"REGISTERED"'
}

echo "Waiting up to ${TIMEOUT_SEC}s for SMF→NRF registration (ns=$NS since=$SINCE)"
deadline=$((SECONDS + TIMEOUT_SEC))
while (( SECONDS < deadline )); do
  if smf_nrf_ok; then
    echo "OK  SMF registered with NRF"
    exit 0
  fi
  echo "... SMF not registered at NRF yet (retry in ${POLL_SEC}s)"
  sleep "$POLL_SEC"
done

echo "RESULT: FAIL — SMF→NRF registration not ready within ${TIMEOUT_SEC}s" >&2
echo "Hints:" >&2
echo "  kubectl --context $SMF_CTX -n $NS logs deploy/smf-core --since=$SINCE | grep -iE 'nrf|register'" >&2
echo "  kubectl --context $SMF_CTX -n $NS logs deploy/$NRF_DEPLOY -c nrf-core --since=$SINCE | grep -i SMF" >&2
exit 1
