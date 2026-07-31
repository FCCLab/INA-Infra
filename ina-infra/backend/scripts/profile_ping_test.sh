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
# Use --n6 for per-slice UPF N6 (live DHCP from UPF pods; override with N6_PREFIX).
# Use -d/--dest/--host IP for a fixed destination (overrides --dnn/--n6).
#
# Usage:
#   ./backend/scripts/profile_ping_test.sh <profilename>
#   ./backend/scripts/profile_ping_test.sh ina-infra --dnn
#   ./backend/scripts/profile_ping_test.sh ina-infra --ue1 --ue3 --count 10
#   ./backend/scripts/profile_ping_test.sh test --n6
#   ./backend/scripts/profile_ping_test.sh ina-infra -d 8.8.8.8 -t
#   ./backend/scripts/profile_ping_test.sh ina-infra --tmux
#   # tmux session is oai_ping_<profilename> (does not kill other profiles)
#
# Env: SLICE_COUNT, DNN_PREFIX, N6_PREFIX, N6_BASE, N6_ADDR_N, EDGE_HOST, INA_SMF_CONTEXT
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

# DNN defaults; N6 is DHCP on 10.1.137 — resolve live leases from UPF pods.
DNN_PREFIX="${DNN_PREFIX:-10.140}"
N6_PREFIX="${N6_PREFIX:-}"
N6_BASE="${N6_BASE:-60}"
CONTEXTS=(central@central regional@regional edge@edge)

# Read current UPF n6 inet (DHCP). Empty if pod/iface missing.
discover_upf_n6() {
  local n="$1" ctx pod addr
  for ctx in "${CONTEXTS[@]}"; do
    pod="$(
      kubectl --context "$ctx" -n "$NS" get pods \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
        | awk -v p="^upf-slice-${n}-" '$0 ~ p {print; exit}'
    )"
    [[ -n "$pod" ]] || continue
    addr="$(
      kubectl --context "$ctx" -n "$NS" exec "$pod" -c "upf-slice-${n}" -- \
        ip -4 -o addr show n6 2>/dev/null \
        | awk '{print $4}' | cut -d/ -f1 | head -1
    )"
    if [[ -n "$addr" ]]; then
      printf '%s' "$addr"
      return 0
    fi
  done
  return 1
}

# Unless caller forces N6_PREFIX (static Multus plan), publish live N6_ADDR_N.
if [[ -z "$N6_PREFIX" ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    if addr="$(discover_upf_n6 "$n")"; then
      export "N6_ADDR_${n}=$addr"
      echo "  UPF${n} N6=${addr} (live DHCP)"
    else
      echo "  UPF${n} N6=(undiscovered)" >&2
    fi
  done
fi

export OAI_SLICE_NS="$NS"
export PROFILE_NS="$NS"
export SLICE_COUNT
export DNN_PREFIX
export N6_PREFIX
export N6_BASE

if [[ -n "$N6_PREFIX" ]]; then
  echo "Ping test ns=${NS} slices=${SLICE_COUNT} dnn_prefix=${DNN_PREFIX} n6=${N6_PREFIX}.$((N6_BASE+1)).."
else
  echo "Ping test ns=${NS} slices=${SLICE_COUNT} dnn_prefix=${DNN_PREFIX} n6=live-from-upf"
fi
exec "$ROOT/scripts/oai_slice_deployment_namespace_ping_test.sh" --ns "$NS" "$@"
