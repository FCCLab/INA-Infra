#!/usr/bin/env bash
# Restart ina-infra SMF (smf-core), wait Ready, then ping UPF N4 peers.
#
# Use after Multus/N4 changes or when PFCP associations are stuck — SMF
# re-initiates association setup toward static upfs[].
#
# Usage:
#   ./scripts/ina-infra-ping-restart-smf.sh
#   ./scripts/ina-infra-ping-restart-smf.sh --no-ping
#   ./scripts/ina-infra-ping-restart-smf.sh --n3 --count 3
#   ./scripts/ina-infra-ping-restart-smf.sh --timeout 180s
#
# Extra args after options are passed to ina-infra-ping-smf-upfs.sh (unless --no-ping).
#
# Env: PROFILE_NS / INA_NS, INA_SMF_CONTEXT, INA_SMF_DEPLOY (default smf-core).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
CTX="${INA_SMF_CONTEXT:-central@central}"
DEPLOY="${INA_SMF_DEPLOY:-smf-core}"
TIMEOUT="${INA_SMF_RESTART_TIMEOUT:-120s}"
DO_PING=1
PING_ARGS=()

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-ping) DO_PING=0; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
    --ns) NS="${2:?}"; shift 2 ;;
    --context) CTX="${2:?}"; shift 2 ;;
    --deploy) DEPLOY="${2:?}"; shift 2 ;;
    --)
      shift
      PING_ARGS+=("$@")
      break
      ;;
    *)
      # Forward unknown flags to the ping script (e.g. --n3 --slice 1).
      PING_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

echo "Restarting deploy/$DEPLOY  ns=$NS  context=$CTX"
kubectl --context "$CTX" -n "$NS" rollout restart "deploy/$DEPLOY"
echo "Waiting rollout status (timeout=$TIMEOUT) ..."
kubectl --context "$CTX" -n "$NS" rollout status "deploy/$DEPLOY" --timeout="$TIMEOUT"

POD=$(kubectl --context "$CTX" -n "$NS" get pods \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
  | grep -E "^${DEPLOY}-" | head -1 || true)
echo "SMF pod: ${POD:-<none>}"

if [[ "$DO_PING" != "1" ]]; then
  echo "RESULT: restart done (--no-ping)"
  exit 0
fi

echo
echo "Pinging UPF peers from SMF ..."
exec "$SCRIPT_DIR/ina-infra-ping-smf-upfs.sh" \
  --ns "$NS" --context "$CTX" "${PING_ARGS[@]+"${PING_ARGS[@]}"}"
