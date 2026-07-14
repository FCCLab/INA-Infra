#!/usr/bin/env bash
# Restart oai-slice-deployment split gNB in parallel (no waits):
#   regional — CU-CP + 5 CU-UPs
#   edge     — DU + 5 UEs
#
# Usage:
#   ./scripts/restart_split_gnb.sh
#   SKIP_UES=1 ./scripts/restart_split_gnb.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SLICE_NS="${OAI_SLICE_NS:-oai-slice-deployment}"
REGIONAL_HOST="${REGIONAL_HOST:-regional-0}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
SLICE_COUNT="${SLICE_COUNT:-5}"
SKIP_UES="${SKIP_UES:-0}"

restart_deploy() {
  local host="$1" deploy="$2"
  echo "  restart ${host}: deployment/${deploy}"
  ssh -F "$SSH_CFG" "$host" \
    kubectl -n "$SLICE_NS" rollout restart "deployment/${deploy}"
}

echo "==> Restart split gNB (ns=${SLICE_NS}, parallel, no wait)"
echo "    regional=${REGIONAL_HOST}  edge=${EDGE_HOST}  slices=${SLICE_COUNT}"

# Fire all restarts concurrently.
pids=()
restart_deploy "$REGIONAL_HOST" oai-cu-cp &
pids+=($!)
for n in $(seq 1 "$SLICE_COUNT"); do
  restart_deploy "$REGIONAL_HOST" "oai-cu-up-${n}" &
  pids+=($!)
done
restart_deploy "$EDGE_HOST" oai-du &
pids+=($!)
if [[ "$SKIP_UES" != "1" ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    restart_deploy "$EDGE_HOST" "oai-ue-${n}" &
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
