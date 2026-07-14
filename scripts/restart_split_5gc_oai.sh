#!/usr/bin/env bash
# Restart OAI 5GC for the split / slice lab in parallel (no waits):
#   central oai-cn  — nrf, udr, udm, ausf, amf, smf
#   central oai-upf — upf-slice-1..5 (and optionally upf-core)
#
# Does not restart mysql by default (data plane / DB churn).
#
# Usage:
#   ./scripts/restart_split_5gc_oai.sh
#   SKIP_SLICE_UPF=1 ./scripts/restart_split_5gc_oai.sh
#   INCLUDE_UPF_CORE=1 ./scripts/restart_split_5gc_oai.sh
#   INCLUDE_MYSQL=1 ./scripts/restart_split_5gc_oai.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
CENTRAL_HOST="${CENTRAL_HOST:-central-0}"
CN_NS="${OAI_CN_NS:-oai-cn}"
UPF_NS="${OAI_UPF_NS:-oai-upf}"
SLICE_COUNT="${SLICE_COUNT:-5}"
SKIP_SLICE_UPF="${SKIP_SLICE_UPF:-0}"
INCLUDE_UPF_CORE="${INCLUDE_UPF_CORE:-0}"
INCLUDE_MYSQL="${INCLUDE_MYSQL:-0}"

restart_deploy() {
  local ns="$1" deploy="$2"
  echo "  restart ${CENTRAL_HOST}: ${ns}/deployment/${deploy}"
  ssh -F "$SSH_CFG" "$CENTRAL_HOST" \
    kubectl -n "$ns" rollout restart "deployment/${deploy}"
}

echo "==> Restart split OAI 5GC (parallel, no wait)"
echo "    host=${CENTRAL_HOST}  cn=${CN_NS}  upf=${UPF_NS}  slices=${SLICE_COUNT}"

pids=()

# Control-plane NFs
for d in nrf-core udr-core udm-core ausf-core amf-core smf-core; do
  restart_deploy "$CN_NS" "$d" &
  pids+=($!)
done

if [[ "$INCLUDE_MYSQL" == "1" ]]; then
  restart_deploy "$CN_NS" mysql &
  pids+=($!)
fi

if [[ "$INCLUDE_UPF_CORE" == "1" ]]; then
  restart_deploy "$UPF_NS" upf-core &
  pids+=($!)
fi

if [[ "$SKIP_SLICE_UPF" != "1" ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    restart_deploy "$UPF_NS" "upf-slice-${n}" &
    pids+=($!)
  done
fi

rc=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    rc=1
  fi
done

if (( rc != 0 )); then
  echo "ERROR: one or more rollout restart commands failed." >&2
  exit 1
fi

echo "Done. All restart requests issued."
echo "Note: after SMF comes up, if PDU fails with 'No UPF available', re-run:"
echo "  SKIP_SLICE_UPF=0 INCLUDE_UPF_CORE=0 ./scripts/restart_split_5gc_oai.sh"
echo "  (or restart only upf-slice-* once SMF is Ready)"
