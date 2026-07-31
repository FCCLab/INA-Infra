#!/usr/bin/env bash
# Ping test for UEs in namespace oai-slice-deployment (check each UE).
#
# For each selected UE:
#   [1] confirm Running pod
#   [2] confirm PDU iface (oaitun_ue*)
#   [3] ping target via that iface
#
# Default target: mgmt-0 10.1.132.200 (same as scripts/test_ping.py).
# Use --dnn for per-slice DNN GW ${DNN_PREFIX}.{N}.1 (default DNN_PREFIX=10.1).
# Use --n6 for per-slice UPF N6 (${N6_PREFIX}.$((N6_BASE+N)) or upf_slice_n6).
# For ina-infra profile UEs, prefer ./scripts/ina-infra-ping-test.sh (ns + DNN/N6 defaults).
#
# Usage:
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh --ue1 --ue3 --count 10
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh --dnn
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh --n6
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh --host 10.1.132.11
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh --tmux   # forever panes via test_ping.py
#   ./scripts/oai_slice_deployment_namespace_ping_test.sh -t       # same as --tmux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
SLICE_NS="${OAI_SLICE_NS:-oai-slice-deployment}"
SLICE_COUNT="${SLICE_COUNT:-${OAI_SLICE_COUNT:-5}}"
DNN_PREFIX="${DNN_PREFIX:-10.1}"
PING_COUNT="${PING_COUNT:-5}"
PING_HOST="${PING_HOST:-${OAI_TEST_HOST:-10.1.132.200}}"
N6_PREFIX="${N6_PREFIX:-}"
N6_BASE="${N6_BASE:-60}"
USE_DNN=0
USE_N6=0
TMUX_MODE=0
SELECTED=()

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --dnn) USE_DNN=1; USE_N6=0; shift ;;
    --n6) USE_N6=1; USE_DNN=0; shift ;;
    -t|--tmux) TMUX_MODE=1; shift ;;
    --count) PING_COUNT="${2:?}"; shift 2 ;;
    --host) PING_HOST="${2:?}"; USE_DNN=0; USE_N6=0; shift 2 ;;
    --ue1|--ue2|--ue3|--ue4|--ue5)
      SELECTED+=("${1#--ue}")
      shift
      ;;
    --ns) SLICE_NS="${2:?}"; shift 2 ;;
    --edge) EDGE_HOST="${2:?}"; shift 2 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ "$TMUX_MODE" == "1" ]]; then
  # One tmux session per namespace/profile — do not kill other profiles' panes.
  safe_ns="$(printf '%s' "$SLICE_NS" | tr -c 'A-Za-z0-9_-' '-' | sed 's/^-//;s/-$//')"
  [[ -n "$safe_ns" ]] || safe_ns="ue"
  args=(
    --tmux
    --ue-ns "$SLICE_NS"
    --session "oai_ping_${safe_ns}"
    --dnn-prefix "$DNN_PREFIX"
  )
  [[ "$USE_DNN" == "1" ]] && args+=(--dnn)
  if [[ "$USE_N6" == "1" ]]; then
    args+=(--n6)
    [[ -n "$N6_PREFIX" ]] && args+=(--n6-prefix "$N6_PREFIX" --n6-base "$N6_BASE")
  elif [[ -n "${PING_HOST}" && "$USE_DNN" != "1" ]]; then
    args+=(--host "$PING_HOST")
  fi
  for n in "${SELECTED[@]+"${SELECTED[@]}"}"; do
    args+=(--ue"$n")
  done
  echo "tmux session: oai_ping_${safe_ns} (ns=${SLICE_NS})"
  exec "$SCRIPT_DIR/test_ping.py" "${args[@]}"
fi

if [[ ${#SELECTED[@]} -eq 0 ]]; then
  for n in $(seq 1 "$SLICE_COUNT"); do
    SELECTED+=("$n")
  done
fi

ssh_edge() {
  ssh -F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 "$EDGE_HOST" "$@"
}

ue_pod() {
  local n="$1"
  # Avoid pipefail+head SIGPIPE: take first name via awk.
  ssh_edge kubectl -n "$SLICE_NS" get pods \
    -l "app.kubernetes.io/name=oai-ue-${n}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

ue_oaitun() {
  local pod="$1"
  # Prefer oaitun_ue1; fall back to any oaitun_ue*.
  local out
  out="$(ssh_edge kubectl -n "$SLICE_NS" exec "$pod" -c ue -- \
    ip -4 -o addr show oaitun_ue1 2>/dev/null || true)"
  if [[ -z "$out" ]]; then
    out="$(ssh_edge kubectl -n "$SLICE_NS" exec "$pod" -c ue -- \
      ip -4 -o addr show 2>/dev/null || true)"
    out="$(printf '%s\n' "$out" | grep -E 'oaitun_ue[0-9]+' | sed -n '1p' || true)"
  fi
  [[ -z "$out" ]] && return 0
  # ip -o: "4: oaitun_ue1    inet 10.1.1.2/24 ..."
  local iface ip
  iface="$(printf '%s\n' "$out" | awk '{print $2}')"
  ip="$(printf '%s\n' "$out" | awk '{print $4}')"
  printf '%s %s\n' "$iface" "$ip"
}

target_for() {
  local n="$1"
  if [[ "$USE_N6" == "1" ]]; then
    if [[ -n "$N6_PREFIX" ]]; then
      printf '%s.%s' "$N6_PREFIX" "$((N6_BASE + n))"
    else
      upf_slice_n6 "$n"
    fi
  elif [[ "$USE_DNN" == "1" ]]; then
    printf '%s.%s.1' "$DNN_PREFIX" "$n"
  else
    printf '%s' "$PING_HOST"
  fi
}

echo "==> ping test (ns=${SLICE_NS} edge=${EDGE_HOST})"
if [[ "$USE_N6" == "1" ]]; then
  if [[ -n "$N6_PREFIX" ]]; then
    echo "    target=UPF N6 ${N6_PREFIX}.$((N6_BASE+1))..${N6_PREFIX}.$((N6_BASE+SLICE_COUNT))  count=${PING_COUNT}"
  else
    echo "    target=UPF N6 upf_slice_n6(N)  count=${PING_COUNT}"
  fi
elif [[ "$USE_DNN" == "1" ]]; then
  echo "    target=DNN GW ${DNN_PREFIX}.{N}.1  count=${PING_COUNT}"
else
  echo "    target=${PING_HOST}  count=${PING_COUNT}"
fi

failed=0
passed=0
for n in "${SELECTED[@]}"; do
  echo "--- UE${n} ---"
  pod="$(ue_pod "$n")"
  if [[ -z "$pod" ]]; then
    echo "  FAIL  no Running pod for oai-ue-${n}"
    failed=$((failed + 1))
    continue
  fi
  echo "  pod   ${pod}"

  tun_line="$(ue_oaitun "$pod")"
  if [[ -z "$tun_line" ]]; then
    echo "  FAIL  no oaitun (PDU missing)"
    failed=$((failed + 1))
    continue
  fi
  iface="${tun_line%% *}"
  pdu_ip="${tun_line##* }"
  echo "  pdu   ${iface} ${pdu_ip}"

  host="$(target_for "$n")"
  echo "  ping  ${host} via ${iface} (-c ${PING_COUNT})"
  if ssh_edge kubectl -n "$SLICE_NS" exec "$pod" -c ue -- \
      ping -c "$PING_COUNT" -W 2 -I "$iface" "$host"; then
    echo "  OK    UE${n}"
    passed=$((passed + 1))
  else
    echo "  FAIL  UE${n} ping ${host}"
    failed=$((failed + 1))
  fi
done

echo "==> Summary: ${passed} ok, ${failed} failed (of ${#SELECTED[@]})"
if (( failed > 0 )); then
  exit 1
fi
echo "Done."
