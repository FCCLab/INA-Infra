#!/usr/bin/env bash
# Ping UPF Multus peers from the ina-infra SMF pod (N4 path check).
#
# Default: ICMP from smf-core to UPF N4 .41–.44 (PFCP peers).
# Optional --n3 also pings N3 .21–.24.
#
# Usage:
#   ./scripts/ina-infra-ping-smf-upfs.sh
#   ./scripts/ina-infra-ping-smf-upfs.sh --n3
#   ./scripts/ina-infra-ping-smf-upfs.sh --count 3
#   ./scripts/ina-infra-ping-smf-upfs.sh --slice 1 --slice 2
#
# Env: PROFILE_NS / INA_NS (default ina-infra), INA_SMF_CONTEXT (default central@central),
#      PING_COUNT, INA_UPF_N4_BASE (default 10.1.140.40 → +slice), INA_UPF_N3_BASE (10.1.140.20).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
CTX="${INA_SMF_CONTEXT:-central@central}"
PING_COUNT="${PING_COUNT:-2}"
PING_WAIT="${PING_WAIT:-1}"
INCLUDE_N3=0
SLICES=()
N4_PREFIX="${INA_UPF_N4_PREFIX:-10.1.140}"
N3_PREFIX="${INA_UPF_N3_PREFIX:-10.1.140}"
# last octet base: N4 = 40+slice, N3 = 20+slice
N4_OCTET0="${INA_UPF_N4_OCTET0:-40}"
N3_OCTET0="${INA_UPF_N3_OCTET0:-20}"
SLICE_COUNT="${SLICE_COUNT:-4}"

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --n3) INCLUDE_N3=1; shift ;;
    --count) PING_COUNT="${2:?}"; shift 2 ;;
    --wait) PING_WAIT="${2:?}"; shift 2 ;;
    --ns) NS="${2:?}"; shift 2 ;;
    --context) CTX="${2:?}"; shift 2 ;;
    --slice)
      SLICES+=("${2:?}")
      shift 2
      ;;
    --slice1|--slice2|--slice3|--slice4)
      SLICES+=("${1#--slice}")
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ ${#SLICES[@]} -eq 0 ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    SLICES+=("$n")
  done
fi

# Prefer combined kubeconfigs when unset (mgmt + workload sites).
if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

SMF=$(kubectl --context "$CTX" -n "$NS" get pods \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
  | grep -E '^smf-core-' | head -1 || true)
if [[ -z "$SMF" ]]; then
  echo "error: no smf-core-* pod in $NS (context $CTX)" >&2
  exit 1
fi

echo "SMF pod: $SMF  ns=$NS  context=$CTX"
echo

fail=0
ping_one() {
  local label="$1" ip="$2"
  echo "=== $label  ping $ip (count=$PING_COUNT) ==="
  if kubectl --context "$CTX" -n "$NS" exec "$SMF" -c smf-core -- \
    ping -c "$PING_COUNT" -W "$PING_WAIT" "$ip"; then
    echo "OK $label $ip"
  else
    echo "FAIL $label $ip" >&2
    fail=1
  fi
  echo
}

for n in "${SLICES[@]}"; do
  if ! [[ "$n" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: bad slice id: $n" >&2
    exit 1
  fi
  n4="${N4_PREFIX}.$((N4_OCTET0 + n))"
  ping_one "UPF${n}-N4" "$n4"
  if [[ "$INCLUDE_N3" == "1" ]]; then
    n3="${N3_PREFIX}.$((N3_OCTET0 + n))"
    ping_one "UPF${n}-N3" "$n3"
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "RESULT: some pings failed" >&2
  exit 1
fi
echo "RESULT: all pings ok"
