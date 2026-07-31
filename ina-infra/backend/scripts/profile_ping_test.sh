#!/usr/bin/env bash
# Ping test for UEs in a profile namespace.
#
# Thin wrapper around scripts/oai_slice_deployment_namespace_ping_test.sh with
# profile-oriented defaults (ns, slice count, DNN/N6 prefixes).
#
# For each selected UE:
#   [1] confirm Running pod
#   [2] confirm PDU iface (oaitun_ue*)
#   [3] ping target via that iface
#
# Default target: mgmt-0 10.1.132.200. Use --dnn for per-slice DNN GW.
# Use --n6 for per-slice UPF N6.
#
# Usage:
#   ./backend/scripts/profile_ping_test.sh <profilename>
#   ./backend/scripts/profile_ping_test.sh ina-infra --dnn
#   ./backend/scripts/profile_ping_test.sh ina-infra --ue1 --ue3 --count 10
#   ./backend/scripts/profile_ping_test.sh test --n6
#   ./backend/scripts/profile_ping_test.sh ina-infra --tmux
#   # tmux session is oai_ping_<profilename> (does not kill other profiles)
#
# Env: SLICE_COUNT, DNN_PREFIX, N6_PREFIX, N6_BASE, EDGE_HOST, INA_SMF_CONTEXT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SMF_CTX="${INA_SMF_CONTEXT:-central@central}"

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

if [[ $# -lt 1 ]]; then
  echo "error: missing <profilename>" >&2
  usage 1
fi

case "$1" in
  -h|--help) usage 0 ;;
  -*)
    echo "error: first argument must be <profilename>, got: $1" >&2
    usage 1
    ;;
esac

NS="$1"
shift

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

# Slice count from PL placement when not overridden.
if [[ -z "${SLICE_COUNT:-}" ]]; then
  SLICE_COUNT="$(
    kubectl --context "$SMF_CTX" -n "$NS" get cm ina-pl-placement \
      -o jsonpath='{.data.placement\.json}' 2>/dev/null \
      | python3 -c '
import json,sys
try:
  d=json.load(sys.stdin)
  n=int((d.get("ip_plan") or {}).get("n_slices") or 0)
  if n<=0:
    n=len(d.get("deploy_map") or {})
  print(n if n>0 else "")
except Exception:
  print("")
' 2>/dev/null || true
  )"
fi
SLICE_COUNT="${SLICE_COUNT:-4}"

# DNN/N6 defaults aligned with ina-infra Multus plan (override via env).
DNN_PREFIX="${DNN_PREFIX:-10.140}"
N6_PREFIX="${N6_PREFIX:-}"
if [[ -z "$N6_PREFIX" ]]; then
  _subnet="$(
    kubectl --context "$SMF_CTX" -n "$NS" get cm ina-core-ips \
      -o jsonpath='{.data.subnet}' 2>/dev/null || true
  )"
  if [[ "$_subnet" =~ ^([0-9]+\.[0-9]+\.[0-9]+)\. ]]; then
    N6_PREFIX="${BASH_REMATCH[1]}"
  else
    N6_PREFIX="10.1.140"
  fi
fi
N6_BASE="${N6_BASE:-60}"

export OAI_SLICE_NS="$NS"
export PROFILE_NS="$NS"
export SLICE_COUNT
export DNN_PREFIX
export N6_PREFIX
export N6_BASE

echo "Ping test ns=${NS} slices=${SLICE_COUNT} dnn_prefix=${DNN_PREFIX} n6=${N6_PREFIX}.$((N6_BASE+1)).."
exec "$ROOT/scripts/oai_slice_deployment_namespace_ping_test.sh" --ns "$NS" "$@"
