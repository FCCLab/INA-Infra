#!/usr/bin/env bash
# DL + UL OpenSpeedTest for each UE in a profile namespace.
#
# For each slice UE (oai-ue-N), destination OST server is the MetalLB VIP on the
# cluster that hosts that slice's UPF (upf-slice-N):
#   regional → http://10.1.137.102/
#   edge     → http://10.1.137.103/
#   central  → http://10.1.137.101/
#
# Traffic is bound to the UE PDU tunnel (utils/openspeedtest/speedtest.sh).
#
# Usage:
#   ./scripts/profile/profile_speedtest.sh <profilename>
#   ./scripts/profile/profile_speedtest.sh ina-infra --duration 10 --threads 1
#   ./scripts/profile/profile_speedtest.sh ina-infra --ue1 --ue3 -d 5
#   ./scripts/profile/profile_speedtest.sh ina-infra --dir download -d 0
#   ./scripts/profile/profile_speedtest.sh ina-infra -t                  # tmux: one pane/UE, forever
#   ./scripts/profile/profile_speedtest.sh ina-infra -t --dir upload
#
# Env: SLICE_COUNT, INA_SMF_CONTEXT, UE_HOST, DURATION, THREADS, DIRECTION
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../cluster_lib.sh
source "$ROOT/scripts/cluster_lib.sh"

OST_DIR="$ROOT/utils/openspeedtest"
SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
EDGE_CTX="${INA_EDGE_CONTEXT:-edge@edge}"
DURATION="${DURATION:-10}"
THREADS="${THREADS:-1}"
DIRECTION="${DIRECTION:-both}"
TMUX_MODE=0
SLICE_COUNT="${SLICE_COUNT:-}"
UES=()
CONTEXTS=(central@central regional@regional edge@edge)
declare -A UPF_CTX=()

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

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    -d|--duration) DURATION="${2:?}"; shift 2 ;;
    --threads) THREADS="${2:?}"; shift 2 ;;
    --dir|--direction) DIRECTION="${2:?}"; shift 2 ;;
    -t|--tmux) TMUX_MODE=1; shift ;;
    --ue)
      UES+=("${2:?}")
      shift 2
      ;;
    --ue1|--ue2|--ue3|--ue4|--ue5)
      UES+=("${1#--ue}")
      shift
      ;;
    --slice)
      UES+=("${2:?}")
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
    esac
done

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

if [[ ! -x "$OST_DIR/speedtest.sh" ]]; then
  echo "error: missing $OST_DIR/speedtest.sh" >&2
  exit 1
fi

# Slice count from PL placement when not overridden.
if [[ -z "$SLICE_COUNT" ]]; then
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

# Discover upf-slice-N → hosting context.
discover_upf_sites() {
  local ctx name n
  for ctx in "${CONTEXTS[@]}"; do
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      n="${name#upf-slice-}"
      [[ "$n" =~ ^[0-9]+$ ]] || continue
      UPF_CTX[$n]="$ctx"
    done < <(
      kubectl --context "$ctx" -n "$NS" get deploy \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
        | grep -E '^upf-slice-[0-9]+$' || true
    )
  done
}

discover_upf_sites

if [[ ${#UES[@]} -eq 0 ]]; then
  if [[ -n "$SLICE_COUNT" ]]; then
    mapfile -t UES < <(seq 1 "$SLICE_COUNT")
  else
    # Fall back to discovered UPF slice indices + any oai-ue-N deploys on edge.
    declare -A seen=()
    for n in "${!UPF_CTX[@]}"; do
      seen[$n]=1
    done
    while IFS= read -r name; do
      n="${name#oai-ue-}"
      [[ "$n" =~ ^[0-9]+$ ]] || continue
      seen[$n]=1
    done < <(
      kubectl --context "$EDGE_CTX" -n "$NS" get deploy \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
        | grep -E '^oai-ue-[0-9]+$' || true
    )
    mapfile -t UES < <(printf '%s\n' "${!seen[@]}" | sort -n)
  fi
fi

if [[ ${#UES[@]} -eq 0 ]]; then
  echo "error: no UEs/slices found in ns=$NS" >&2
  exit 1
fi

cluster_from_ctx() {
  local ctx="$1"
  # regional@regional → regional
  printf '%s' "${ctx%%@*}"
}

ost_url_for_slice() {
  local n="$1" ctx cluster vip
  ctx="${UPF_CTX[$n]:-}"
  if [[ -z "$ctx" ]]; then
    # No UPF discovered — default to mgmt OST.
    printf 'http://%s/' "$(openspeedtest_vip mgmt)"
    return 0
  fi
  cluster="$(cluster_from_ctx "$ctx")"
  vip="$(openspeedtest_vip "$cluster")"
  printf 'http://%s/' "$vip"
}

ue_pod_name() {
  local n="$1" name
  while IFS= read -r name; do
    if [[ "$name" =~ ^oai-ue-${n}- ]]; then
      printf '%s' "$name"
      return 0
    fi
  done < <(
    kubectl --context "$EDGE_CTX" -n "$NS" get pods \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true
  )
  return 1
}

echo "Speedtest profile ns=${NS} ues=${UES[*]} duration=${DURATION} threads=${THREADS} dir=${DIRECTION} tmux=${TMUX_MODE}"
echo "UE kubectl via UE_HOST=${UE_HOST:-edge-0} (utils/openspeedtest)"
echo

# Build list of runnable UEs: n|pod|server|cluster
declare -a JOBS=()
for n in "${UES[@]}"; do
  ctx="${UPF_CTX[$n]:-}"
  server="$(ost_url_for_slice "$n")"
  cluster="mgmt"
  [[ -n "$ctx" ]] && cluster="$(cluster_from_ctx "$ctx")"
  pod="$(ue_pod_name "$n" || true)"
  if [[ -z "$pod" ]]; then
    echo "SKIP: UE${n} no oai-ue-${n}-* pod in ${NS} on ${EDGE_CTX} (UPF site=${ctx:-unknown} OST=${server})"
    continue
  fi
  echo "UE${n}  pod=${pod}  UPF=${ctx:-unknown} (${cluster})  OST=${server}"
  JOBS+=("${n}|${pod}|${server}|${cluster}")
done
echo

if [[ ${#JOBS[@]} -eq 0 ]]; then
  echo "error: no runnable UEs" >&2
  exit 1
fi

run_one() {
  local n="$1" pod="$2" server="$3" dur="$4" dir="$5"
  export UE_HOST="${UE_HOST:-edge-0}"
  (
    cd "$OST_DIR"
    ./speedtest.sh "${NS}/${pod}" \
      --server "$server" \
      --duration "$dur" \
      --threads "$THREADS" \
      --direction "$dir"
  )
}

if [[ "$TMUX_MODE" -eq 1 ]]; then
  if ! command -v tmux >/dev/null 2>&1; then
    echo "error: tmux not found; install tmux or run without -t/--tmux" >&2
    exit 1
  fi
  # Forever needs a single direction.
  local_dir="$DIRECTION"
  if [[ "$local_dir" == "both" ]]; then
    local_dir="download"
    echo "tmux: direction=both → using download (pass --dir upload for UL)"
  fi
  safe_ns="$(printf '%s' "$NS" | tr -c 'A-Za-z0-9_' '_')"
  session="${TMUX_SESSION:-oai_speed_${safe_ns}}"

  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Killing existing tmux session ${session}"
    tmux kill-session -t "$session" 2>/dev/null || true
  fi

  first=1
  for job in "${JOBS[@]}"; do
    IFS='|' read -r n pod server cluster <<<"$job"
    # shellcheck disable=SC2016
    pane_cmd="$(
      printf 'export UE_HOST=%q KUBECONFIG=%q; cd %q; echo "UE%s → %s"; while true; do ./speedtest.sh %q --server %q --duration 0 --threads %q --direction %q; echo "[UE%s] exited; retry in 3s"; sleep 3; done' \
        "${UE_HOST:-edge-0}" "${KUBECONFIG}" "$OST_DIR" "$n" "$server" \
        "${NS}/${pod}" "$server" "$THREADS" "$local_dir" "$n"
    )"
    if [[ "$first" -eq 1 ]]; then
      tmux new-session -d -s "$session" -n ue bash -lc "$pane_cmd"
      first=0
    else
      tmux split-window -t "${session}:ue" bash -lc "$pane_cmd"
      tmux select-layout -t "${session}:ue" tiled >/dev/null 2>&1 || true
    fi
  done
  tmux select-layout -t "${session}:ue" tiled >/dev/null 2>&1 || true
  tmux set-option -t "$session" mouse on >/dev/null 2>&1 || true

  echo "tmux session: ${session}  |  ${#JOBS[@]} UE pane(s), ${local_dir} forever"
  echo "Detach: Ctrl-b d  |  Kill: Ctrl-C or tmux kill-session -t ${session}"

  cleanup() {
    tmux kill-session -t "$session" 2>/dev/null || true
  }
  trap cleanup INT TERM EXIT
  tmux attach -t "$session" || true
  trap - INT TERM EXIT
  cleanup
  echo "RESULT: tmux session ended"
  exit 0
fi

fail=0
for job in "${JOBS[@]}"; do
  IFS='|' read -r n pod server cluster <<<"$job"
  echo "======== UE${n}  (${cluster})  OST=${server} ========"
  echo "pod=${NS}/${pod}"
  if ! run_one "$n" "$pod" "$server" "$DURATION" "$DIRECTION"; then
    echo "FAIL: UE${n} speedtest"
    fail=1
  fi
  echo
done

if [[ "$fail" -ne 0 ]]; then
  echo "RESULT: one or more UE speedtests failed/skipped"
  exit 1
fi
echo "RESULT: all selected UE speedtests finished"
