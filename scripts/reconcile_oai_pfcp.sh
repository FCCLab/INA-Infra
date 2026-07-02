#!/usr/bin/env bash
# Ensure SMF<->UPF PFCP association on central after cold start or SMF restart.
#
# OAI SMF subscribes to NRF UPF registration events. If UPF registers before SMF
# is ready, PFCP association is never established and PDU sessions fail with
# "No UPF available". Restarting UPF triggers NRF NFStatusNotify -> SMF sends
# PFCP Association Setup.
#
# Usage: reconcile_oai_pfcp.sh [ssh-host]
#   ssh-host defaults to central-0 (see utils/ssh_config/config).
set -euo pipefail

SSH_HOST="${1:-central-0}"
SSH_CFG="${SSH_CFG:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/utils/ssh_config/config}"
SMF_NS="${SMF_NS:-oai-cn}"
UPF_NS="${UPF_NS:-oai-upf}"
SMF_DEPLOY="${SMF_DEPLOY:-smf-core}"
UPF_DEPLOY="${UPF_DEPLOY:-upf-core}"
TIMEOUT="${TIMEOUT:-180}"

ssh_() {
  ssh -F "$SSH_CFG" "$SSH_HOST" "$@"
}

wait_rollout() {
  local ns="$1" deploy="$2"
  ssh_ kubectl rollout status "deployment/${deploy}" -n "$ns" --timeout="${TIMEOUT}s"
}

pfcp_associated() {
  ssh_ kubectl logs -n "$SMF_NS" "deploy/${SMF_DEPLOY}" --since=5m 2>/dev/null \
    | grep -q 'Received N4 ASSOCIATION SETUP RESPONSE'
}

echo "Waiting for SMF and UPF deployments..."
wait_rollout "$SMF_NS" "$SMF_DEPLOY"
wait_rollout "$UPF_NS" "$UPF_DEPLOY"

if pfcp_associated; then
  echo "PFCP association already established (SMF received N4 ASSOCIATION SETUP RESPONSE)."
  exit 0
fi

echo "PFCP not associated; restarting UPF to trigger NRF notify -> SMF PFCP setup..."
ssh_ kubectl rollout restart "deployment/${UPF_DEPLOY}" -n "$UPF_NS"
wait_rollout "$UPF_NS" "$UPF_DEPLOY"
sleep 10

if pfcp_associated; then
  echo "PFCP association established."
  ssh_ kubectl logs -n "$UPF_NS" "deploy/${UPF_DEPLOY}" --since=2m 2>/dev/null \
    | grep -i 'HEARTBEAT' | tail -3 || true
  exit 0
fi

echo "ERROR: PFCP association still missing after UPF restart." >&2
echo "Check: kubectl logs -n $SMF_NS deploy/$SMF_DEPLOY | grep -i pfcp" >&2
exit 1
