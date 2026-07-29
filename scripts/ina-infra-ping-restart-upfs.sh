#!/usr/bin/env bash
# Restart ina-infra UPF slice deployments, wait Ready, then ping from SMF.
#
# Discovers which cluster hosts each upf-slice-N (central/regional/edge).
# Use when Multus/N4 peers need a bounce so SMF can re-associate PFCP.
#
# Usage:
#   ./scripts/ina-infra-ping-restart-upfs.sh
#   ./scripts/ina-infra-ping-restart-upfs.sh --no-ping
#   ./scripts/ina-infra-ping-restart-upfs.sh --slice 1 --slice 2
#   ./scripts/ina-infra-ping-restart-upfs.sh --wait-pfcp
#   ./scripts/ina-infra-ping-restart-upfs.sh --timeout 180s --n3
#
# Ping-related flags (--n3, --count, …) are forwarded to ina-infra-ping-smf-upfs.sh.
#
# Env: PROFILE_NS / INA_NS, SLICE_COUNT, INA_SMF_CONTEXT, INA_UPF_RESTART_TIMEOUT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
SLICE_COUNT="${SLICE_COUNT:-4}"
TIMEOUT="${INA_UPF_RESTART_TIMEOUT:-120s}"
DO_PING=1
DO_WAIT_PFCP=0
SLICES=()
PING_ARGS=()
CONTEXTS=(central@central regional@regional edge@edge)

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-ping) DO_PING=0; shift ;;
    --wait-pfcp) DO_WAIT_PFCP=1; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
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
    --)
      shift
      PING_ARGS+=("$@")
      break
      ;;
    *)
      PING_ARGS+=("$1")
      shift
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

declare -A UPF_CTX=()
for n in "${SLICES[@]}"; do
  ctx="$(find_upf_context "$n" || true)"
  if [[ -z "$ctx" ]]; then
    echo "error: upf-slice-${n} not found on central/regional/edge" >&2
    exit 1
  fi
  UPF_CTX[$n]="$ctx"
done

echo "Restarting UPF slices in ns=$NS (timeout=$TIMEOUT)"
for n in "${SLICES[@]}"; do
  ctx="${UPF_CTX[$n]}"
  echo "  restart deploy/upf-slice-${n}  ($ctx)"
  kubectl --context "$ctx" -n "$NS" rollout restart "deploy/upf-slice-${n}"
done

echo "Waiting rollouts ..."
for n in "${SLICES[@]}"; do
  ctx="${UPF_CTX[$n]}"
  echo "  wait upf-slice-${n} ($ctx)"
  kubectl --context "$ctx" -n "$NS" rollout status "deploy/upf-slice-${n}" \
    --timeout="$TIMEOUT"
done

echo "UPF pods:"
for n in "${SLICES[@]}"; do
  ctx="${UPF_CTX[$n]}"
  pod=$(kubectl --context "$ctx" -n "$NS" get pods \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | grep -E "^upf-slice-${n}-" | head -1 || true)
  echo "  UPF${n}  ${pod:-<none>}  $ctx"
done

if [[ "$DO_WAIT_PFCP" == "1" ]]; then
  echo
  echo "Waiting SMF↔UPF PFCP ..."
  pfcp_args=(--ns "$NS" --context "$SMF_CTX")
  for n in "${SLICES[@]}"; do
    pfcp_args+=(--slice "$n")
  done
  "$SCRIPT_DIR/ina-infra-wait-smf-pfcp-upfs.sh" "${pfcp_args[@]}"
fi

if [[ "$DO_PING" != "1" ]]; then
  echo "RESULT: UPF restart done (--no-ping)"
  exit 0
fi

echo
echo "Pinging UPF peers from SMF ..."
ping_args=(--ns "$NS" --context "$SMF_CTX")
for n in "${SLICES[@]}"; do
  ping_args+=(--slice "$n")
done
ping_args+=("${PING_ARGS[@]+"${PING_ARGS[@]}"}")
exec "$SCRIPT_DIR/ina-infra-ping-smf-upfs.sh" "${ping_args[@]}"
