#!/usr/bin/env bash
# Restart oai-slice-deployment split gNB in parallel (no waits):
#   Co-located UPF+CU-UP: slice1 central, slice2 regional, slices 3–5 edge
#   edge — CU-CP + DU + UEs (+ CU-UP 3–5)
#
# Usage:
#   ./scripts/restart_split_gnb.sh
#   SKIP_UES=1 ./scripts/restart_split_gnb.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SLICE_NS="${OAI_SLICE_NS:-oai-slice-deployment}"
CENTRAL_HOST="${CENTRAL_HOST:-central-0}"
REGIONAL_HOST="${REGIONAL_HOST:-regional-0}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
SLICE_COUNT="${SLICE_COUNT:-${OAI_SLICE_COUNT:-5}}"
SKIP_UES="${SKIP_UES:-0}"

host_for_site() {
  case "$1" in
    central) printf '%s' "$CENTRAL_HOST" ;;
    regional) printf '%s' "$REGIONAL_HOST" ;;
    *) printf '%s' "$EDGE_HOST" ;;
  esac
}

restart_deploy() {
  local host="$1" ns="$2" deploy="$3"
  echo "  restart ${host}: ${ns}/deployment/${deploy}"
  ssh -F "$SSH_CFG" "$host" \
    kubectl -n "$ns" rollout restart "deployment/${deploy}"
}

echo "==> Restart split gNB (ns=${SLICE_NS}, parallel, no wait)"
echo "    central=${CENTRAL_HOST} regional=${REGIONAL_HOST} edge=${EDGE_HOST} slices=${SLICE_COUNT}"

pids=()
restart_deploy "$EDGE_HOST" "$SLICE_NS" oai-cu-cp &
pids+=($!)
restart_deploy "$EDGE_HOST" "$SLICE_NS" oai-du &
pids+=($!)

for n in $(seq 1 "$SLICE_COUNT"); do
  site="$(oai_slice_site "$n")"
  host="$(host_for_site "$site")"
  restart_deploy "$host" "$SLICE_NS" "oai-cu-up-${n}" &
  pids+=($!)
done

if [[ "$SKIP_UES" != "1" ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    restart_deploy "$EDGE_HOST" "$SLICE_NS" "oai-ue-${n}" &
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
