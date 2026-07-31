#!/usr/bin/env bash
# Staged rolling restart for ina-infra 5GC + RAN (kubectl contexts, no SSH).
#
# Same order as ina_profile_namespace_rollout.sh:
#   1. NRF → wait NRF HTTP
#   2. UPF slices → wait UPF→NRF
#   3. SMF → wait SMF→NRF
#   4. Wait SMF↔UPF PFCP
#   5. RAN (CU-CP, CU-UP, FlexRIC, DU, UEs)
#
# Usage:
#   ./scripts/ina-infra-roll-restart.sh
#   ./scripts/ina-infra-roll-restart.sh --step 2
#   ./scripts/ina-infra-roll-restart.sh --step upf
#   ./scripts/ina-infra-roll-restart.sh --from-step 3
#   ./scripts/ina-infra-roll-restart.sh --skip-nrf-wait
#   ./scripts/ina-infra-roll-restart.sh --skip-ran
#
# Steps: 1=nrf  2=upf  3=smf  4=pfcp  5=ran (includes UEs unless --skip-ues)
#
# Env: PROFILE_NS / INA_NS, SLICE_COUNT, ROLL_TIMEOUT, NRF_WAIT_SEC, PFCP_WAIT_SEC.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
CENTRAL_CTX="${INA_CENTRAL_CONTEXT:-central@central}"
EDGE_CTX="${INA_EDGE_CONTEXT:-edge@edge}"
SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
NRF_DEPLOY="${INA_NRF_DEPLOY:-nrf-core}"
SLICE_COUNT="${SLICE_COUNT:-4}"
TIMEOUT="${ROLL_TIMEOUT:-180s}"
NRF_WAIT_SEC="${NRF_WAIT_SEC:-120}"
PFCP_WAIT_SEC="${PFCP_WAIT_SEC:-120}"
NRF_LOG_SINCE="${NRF_LOG_SINCE:-5m}"
STEP_FROM=1
STEP_TO=5
SKIP_RAN=0
SKIP_UES=0
SKIP_NRF_WAIT=0
NRF_HTTP_WAIT=0
SLICES=()
CONTEXTS=(central@central regional@regional edge@edge)
RAN_DEPLOYS=(oai-cu-cp oai-cu-up-1 oai-cu-up-2 oai-flexric oai-du)
UE_DEPLOYS=(oai-ue-1 oai-ue-2 oai-ue-3 oai-ue-4)

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

step_to_num() {
  case "$1" in
    1|nrf) printf '1' ;;
    2|upf) printf '2' ;;
    3|smf) printf '3' ;;
    4|pfcp) printf '4' ;;
    5|ran) printf '5' ;;
    *)
      echo "Unknown step: $1 (use 1-5 or nrf|upf|smf|pfcp|ran)" >&2
      return 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --step)
      n="$(step_to_num "${2:?}")" || exit 1
      STEP_FROM="$n"
      STEP_TO="$n"
      shift 2
      ;;
    --from-step)
      STEP_FROM="$(step_to_num "${2:?}")" || exit 1
      shift 2
      ;;
    --to-step)
      STEP_TO="$(step_to_num "${2:?}")" || exit 1
      shift 2
      ;;
    --skip-ran) SKIP_RAN=1; STEP_TO=4; shift ;;
    --skip-ues) SKIP_UES=1; shift ;;
    --skip-nrf-wait) SKIP_NRF_WAIT=1; shift ;;
    --nrf-http-wait) NRF_HTTP_WAIT=1; shift ;;
    --nrf-ready-only) SKIP_NRF_WAIT=1; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
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

should_run_step() {
  local n="$1"
  (( n >= STEP_FROM && n <= STEP_TO ))
}

wait_rollout() {
  local ctx="$1" deploy="$2"
  echo "  wait   $ctx  deploy/$deploy  (timeout=$TIMEOUT)"
  kubectl --context "$ctx" -n "$NS" rollout status "deploy/$deploy" --timeout="$TIMEOUT"
}

find_deploy_context() {
  local deploy="$1" ctx
  for ctx in "${CONTEXTS[@]}"; do
    if kubectl --context "$ctx" -n "$NS" get "deploy/$deploy" -o name >/dev/null 2>&1; then
      printf '%s' "$ctx"
      return 0
    fi
  done
  return 1
}

slice_args=()
for n in "${SLICES[@]}"; do
  slice_args+=(--slice "$n")
done

echo "==> ina-infra roll restart (ns=$NS steps=${STEP_FROM}..${STEP_TO})"

if should_run_step 1; then
  echo "==> [1/5] Restart NRF"
  kubectl --context "$CENTRAL_CTX" -n "$NS" rollout restart "deploy/$NRF_DEPLOY"
  wait_rollout "$CENTRAL_CTX" "$NRF_DEPLOY"
  echo "  OK     NRF deployment Ready"
  if [[ "$SKIP_NRF_WAIT" == "1" || "$NRF_HTTP_WAIT" != "1" ]]; then
    [[ "$NRF_HTTP_WAIT" != "1" ]] && echo "  skip NRF HTTP probe (use --nrf-http-wait to enable)"
  else
    echo "  probing NRF SBI HTTP ..."
    NRF_WAIT_SEC="$NRF_WAIT_SEC" \
      "$SCRIPT_DIR/ina-infra-wait-nrf-http.sh" --ns "$NS" --timeout "$NRF_WAIT_SEC"
  fi
fi

if should_run_step 2; then
  echo
  echo "==> [2/5] Restart UPF → wait UPF→NRF"
  "$SCRIPT_DIR/ina-infra-ping-restart-upfs.sh" --no-ping --ns "$NS" --timeout "$TIMEOUT" \
    "${slice_args[@]}"
  NRF_WAIT_SEC="$NRF_WAIT_SEC" NRF_LOG_SINCE="$NRF_LOG_SINCE" \
    "$SCRIPT_DIR/ina-infra-wait-upf-nrf.sh" --ns "$NS" --timeout "$NRF_WAIT_SEC" \
    --since "$NRF_LOG_SINCE" "${slice_args[@]}"
fi

if should_run_step 3; then
  echo
  echo "==> [3/5] Restart SMF → wait SMF→NRF"
  "$SCRIPT_DIR/ina-infra-ping-restart-smf.sh" --no-ping --ns "$NS" --context "$SMF_CTX" \
    --timeout "$TIMEOUT"
  NRF_WAIT_SEC="$NRF_WAIT_SEC" NRF_LOG_SINCE="$NRF_LOG_SINCE" \
    "$SCRIPT_DIR/profile/profile_wait_smf_nrf.sh" "$NS" --context "$SMF_CTX" \
    --timeout "$NRF_WAIT_SEC" --since "$NRF_LOG_SINCE"
fi

if should_run_step 4; then
  echo
  echo "==> [4/5] Wait SMF↔UPF PFCP"
  PFCP_WAIT_SEC="$PFCP_WAIT_SEC" \
    "$SCRIPT_DIR/profile/profile_wait_smf_pfcp_upfs.sh" "$NS" --context "$SMF_CTX" \
    --timeout "$PFCP_WAIT_SEC" --since "$NRF_LOG_SINCE" "${slice_args[@]}"
fi

if [[ "$SKIP_RAN" == "1" ]] || ! should_run_step 5; then
  echo
  echo "RESULT: 5GC roll complete"
  exit 0
fi

echo
echo "==> [5/5] Restart RAN"
for d in "${RAN_DEPLOYS[@]}"; do
  ctx="$(find_deploy_context "$d" || true)"
  if [[ -n "$ctx" ]]; then
    echo "  restart deploy/$d  ($ctx)"
    kubectl --context "$ctx" -n "$NS" rollout restart "deploy/$d"
  fi
done
for d in "${RAN_DEPLOYS[@]}"; do
  ctx="$(find_deploy_context "$d" || true)"
  if [[ -n "$ctx" ]]; then
    wait_rollout "$ctx" "$d"
  fi
done

if [[ "$SKIP_UES" != "1" ]]; then
  for d in "${UE_DEPLOYS[@]}"; do
    ctx="$(find_deploy_context "$d" || true)"
    if [[ -n "$ctx" ]]; then
      echo "  restart deploy/$d  ($ctx)"
      kubectl --context "$ctx" -n "$NS" rollout restart "deploy/$d"
      wait_rollout "$ctx" "$d"
    fi
  done
fi

echo
echo "RESULT: full ina-infra roll complete (NRF → UPF → SMF → PFCP → RAN)"
