#!/usr/bin/env bash
# Wait until SMF has PFCP (N4) association activity with each ina-infra UPF.
#
# Polls UPF logs (association / heartbeat) and SMF logs (N4 ASSOCIATION /
# Association Setup Response). Discovers which cluster hosts each upf-slice-N.
#
# Usage:
#   ./scripts/ina-infra-wait-smf-pfcp-upfs.sh
#   ./scripts/ina-infra-wait-smf-pfcp-upfs.sh --timeout 180
#   ./scripts/ina-infra-wait-smf-pfcp-upfs.sh --slice 1 --slice 2
#   ./scripts/ina-infra-wait-smf-pfcp-upfs.sh --since 10m
#
# Env: PROFILE_NS / INA_NS, INA_SMF_CONTEXT, SLICE_COUNT, PFCP_WAIT_SEC,
#      INA_UPF_N4_PREFIX / INA_UPF_N4_OCTET0 (default 10.1.140 / 40 → .41…).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
SMF_DEPLOY="${INA_SMF_DEPLOY:-smf-core}"
SLICE_COUNT="${SLICE_COUNT:-4}"
TIMEOUT_SEC="${PFCP_WAIT_SEC:-120}"
SINCE="${PFCP_LOG_SINCE:-5m}"
POLL_SEC="${PFCP_POLL_SEC:-5}"
N4_PREFIX="${INA_UPF_N4_PREFIX:-10.1.140}"
N4_OCTET0="${INA_UPF_N4_OCTET0:-40}"
SLICES=()
CONTEXTS=(central@central regional@regional edge@edge)

# UPF-side: association setup or heartbeat from SMF.
UPF_PFCP_RE='Handle SX ASSOCIATION SETUP REQUEST|Received SX HEARTBEAT REQUEST|Received N4 ASSOCIATION|Association Setup|HEARTBEAT_REQUEST|heartbeat request|PFCP Association|ASSOCIATION_SETUP'
# SMF-side: got association response (or heartbeat) for a peer.
SMF_PFCP_RE='Received N4 ASSOCIATION SETUP RESPONSE|Association Setup Response|ASSOCIATION_SETUP_RESPONSE|Heartbeat Response|N4 Association'

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

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

n4_for_slice() {
  local n="$1"
  printf '%s.%s' "$N4_PREFIX" "$((N4_OCTET0 + n))"
}

# Print context hosting deploy/upf-slice-N, or empty.
find_upf_context() {
  local n="$1" ctx
  for ctx in "${CONTEXTS[@]}"; do
    if kubectl --context "$ctx" -n "$NS" get "deploy/upf-slice-${n}" \
      -o name >/dev/null 2>&1; then
      printf '%s' "$ctx"
      return 0
    fi
  done
  return 1
}

# UPF Deployments share selector workload.nephio.org/oai=upf — resolve pod by name.
upf_pod_name() {
  local n="$1" ctx="$2"
  kubectl --context "$ctx" -n "$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    2>/dev/null | grep -E "^upf-slice-${n}-" | head -1
}

upf_pfcp_ok() {
  local n="$1" ctx="$2" pod
  pod="$(upf_pod_name "$n" "$ctx")"
  [[ -n "$pod" ]] || return 1
  kubectl --context "$ctx" -n "$NS" logs "$pod" \
    -c "upf-slice-${n}" --since="$SINCE" 2>/dev/null \
    | grep -qiE "$UPF_PFCP_RE"
}

smf_pfcp_ok_for_n4() {
  local n4="$1"
  local logs
  logs="$(
    kubectl --context "$SMF_CTX" -n "$NS" logs "deploy/${SMF_DEPLOY}" \
      -c smf-core --since="$SINCE" 2>/dev/null || true
  )"
  # Prefer a success line that mentions this N4; else any SMF assoc success
  # while we still require UPF-side evidence per slice.
  if echo "$logs" | grep -F "$n4" | grep -qiE "$SMF_PFCP_RE"; then
    return 0
  fi
  echo "$logs" | grep -qiE "$SMF_PFCP_RE"
}

slice_pfcp_ok() {
  local n="$1" ctx n4
  ctx="$(find_upf_context "$n" || true)"
  [[ -n "$ctx" ]] || return 1
  n4="$(n4_for_slice "$n")"
  if upf_pfcp_ok "$n" "$ctx"; then
    return 0
  fi
  # Soft: SMF logged assoc for this N4 even if UPF log pattern differs.
  if smf_pfcp_ok_for_n4 "$n4" && kubectl --context "$ctx" -n "$NS" get \
    "deploy/upf-slice-${n}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null \
    | grep -qx '1'; then
    return 0
  fi
  return 1
}

if ! kubectl --context "$SMF_CTX" -n "$NS" get "deploy/${SMF_DEPLOY}" \
  -o name >/dev/null 2>&1; then
  echo "error: deploy/${SMF_DEPLOY} not found in $NS ($SMF_CTX)" >&2
  exit 1
fi

echo "Waiting up to ${TIMEOUT_SEC}s for SMF↔UPF PFCP (ns=$NS slices=${SLICES[*]} since=$SINCE)"
declare -A UPF_CTX=()
for n in "${SLICES[@]}"; do
  ctx="$(find_upf_context "$n" || true)"
  if [[ -z "$ctx" ]]; then
    echo "error: upf-slice-${n} not found on central/regional/edge" >&2
    exit 1
  fi
  UPF_CTX[$n]="$ctx"
  echo "  UPF${n}  N4=$(n4_for_slice "$n")  site=$ctx"
done
echo

deadline=$((SECONDS + TIMEOUT_SEC))
missing=()
while (( SECONDS < deadline )); do
  missing=()
  for n in "${SLICES[@]}"; do
    if ! slice_pfcp_ok "$n"; then
      missing+=("$n")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    for n in "${SLICES[@]}"; do
      echo "OK  UPF${n}  N4=$(n4_for_slice "$n")  ${UPF_CTX[$n]}  PFCP associated/heartbeat"
    done
    echo "RESULT: all PFCP associations ready"
    exit 0
  fi
  echo "... missing PFCP for UPF: ${missing[*]} (retry in ${POLL_SEC}s)"
  sleep "$POLL_SEC"
done

echo "RESULT: FAIL — PFCP not ready for UPF ${missing[*]} within ${TIMEOUT_SEC}s" >&2
echo "Hints:" >&2
echo "  kubectl --context $SMF_CTX -n $NS logs deploy/${SMF_DEPLOY} -c smf-core | grep -iE 'PFCP|Associat|Failed'" >&2
for n in "${missing[@]}"; do
  echo "  kubectl --context ${UPF_CTX[$n]} -n $NS logs \$(kubectl --context ${UPF_CTX[$n]} -n $NS get pods -o name | grep upf-slice-${n}- | head -1) -c upf-slice-${n} | grep -iE 'PFCP|Associat|heartbeat'" >&2
done
exit 1
