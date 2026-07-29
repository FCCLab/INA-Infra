#!/usr/bin/env bash
# Wait until each ina-infra UPF has successfully registered with NRF.
#
# Polls UPF logs for NRF registration success (and treats persistent
# "Could not get response from NRF" as not ready). Discovers which cluster
# hosts each upf-slice-N.
#
# Usage:
#   ./scripts/ina-infra-wait-upf-nrf.sh
#   ./scripts/ina-infra-wait-upf-nrf.sh --timeout 180
#   ./scripts/ina-infra-wait-upf-nrf.sh --slice 1 --slice 2
#   ./scripts/ina-infra-wait-upf-nrf.sh --since 10m
#
# Env: PROFILE_NS / INA_NS, SLICE_COUNT, NRF_WAIT_SEC, NRF_LOG_SINCE,
#      INA_UPF_N4_PREFIX / INA_UPF_N4_OCTET0 (default 10.1.140 / 40 → .41…).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
SLICE_COUNT="${SLICE_COUNT:-4}"
TIMEOUT_SEC="${NRF_WAIT_SEC:-120}"
SINCE="${NRF_LOG_SINCE:-5m}"
POLL_SEC="${NRF_POLL_SEC:-5}"
N4_PREFIX="${INA_UPF_N4_PREFIX:-10.1.140}"
N4_OCTET0="${INA_UPF_N4_OCTET0:-40}"
SLICES=()
CONTEXTS=(central@central regional@regional edge@edge)

# Success markers from oai-upf Nnrf client.
UPF_NRF_OK_RE='NF Instance Registration to NRF was successful|successfully registered with NRF|Got successful response from NRF|NRF Registration procedure successful|HTTP code \(201\)|Register NF Instance Response|NF registered'
# Hard failure (still retrying).
UPF_NRF_FAIL_RE='Could not get response from NRF|TIME-OUT event timer'

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

# UPF Deployments share selector workload.nephio.org/oai=upf (central has 3+4),
# so `kubectl logs deploy/upf-slice-N` can pick the wrong pod — resolve by name.
upf_pod_name() {
  local n="$1" ctx="$2"
  kubectl --context "$ctx" -n "$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    2>/dev/null | grep -E "^upf-slice-${n}-" | head -1
}

upf_register_nf_yes() {
  local n="$1" ctx="$2" conf
  conf="$(
    kubectl --context "$ctx" -n "$NS" get "cm/upf-slice-${n}" \
      -o jsonpath='{.data.upf\.yaml}' 2>/dev/null || true
  )"
  echo "$conf" | grep -A1 '^register_nf:' | grep -q "upf: 'yes'"
}

upf_nrf_ok() {
  local n="$1" ctx="$2" pod logs
  pod="$(upf_pod_name "$n" "$ctx")"
  [[ -n "$pod" ]] || return 1
  logs="$(
    kubectl --context "$ctx" -n "$NS" logs "$pod" \
      -c "upf-slice-${n}" --since="$SINCE" 2>/dev/null || true
  )"
  # Prefer an explicit success line.
  if echo "$logs" | grep -qiE "$UPF_NRF_OK_RE"; then
    return 0
  fi
  # Some builds only log HTTP 201 / location without the phrase above.
  if echo "$logs" | grep -qiE 'Send NF Instance Registration to NRF' \
    && echo "$logs" | grep -qiE 'HTTP code \(201\)|code \(201\)|status.?201'; then
    return 0
  fi
  return 1
}

slice_nrf_ok() {
  local n="$1" ctx
  ctx="$(find_upf_context "$n" || true)"
  [[ -n "$ctx" ]] || return 1
  upf_register_nf_yes "$n" "$ctx" || return 1
  upf_nrf_ok "$n" "$ctx"
}

echo "Waiting up to ${TIMEOUT_SEC}s for UPF→NRF registration (ns=$NS slices=${SLICES[*]} since=$SINCE)"
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
    if ! slice_nrf_ok "$n"; then
      missing+=("$n")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    for n in "${SLICES[@]}"; do
      echo "OK  UPF${n}  N4=$(n4_for_slice "$n")  ${UPF_CTX[$n]}  NRF registered"
    done
    echo "RESULT: all UPFs registered with NRF"
    exit 0
  fi
  echo "... missing NRF registration for UPF: ${missing[*]} (retry in ${POLL_SEC}s)"
  sleep "$POLL_SEC"
done

echo "RESULT: FAIL — NRF registration not ready for UPF ${missing[*]} within ${TIMEOUT_SEC}s" >&2
echo "Hints:" >&2
echo "  # register_nf must be yes; UPF SBI must reach Multus NRF (default 10.1.140.11)" >&2
for n in "${missing[@]}"; do
  echo "  kubectl --context ${UPF_CTX[$n]} -n $NS get cm upf-slice-${n} -o jsonpath='{.data.upf\\.yaml}' | grep -A1 register_nf" >&2
  echo "  kubectl --context ${UPF_CTX[$n]} -n $NS logs \$(kubectl --context ${UPF_CTX[$n]} -n $NS get pods -o name | grep upf-slice-${n}- | head -1) -c upf-slice-${n} | grep -iE 'NRF|register|TIME-OUT|Could not get'" >&2
done
exit 1
