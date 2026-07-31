#!/usr/bin/env bash
# Staged rollout for an INA-Infra profile namespace (default: ina-infra).
#
# Same bring-up order as oai_slice_deployment_namespace_rollout.sh, but all
# Deployments live in PROFILE_NS and UPF sites come from PL placement CM.
#
# Order:
#   [1] NRF (central) → wait NRF HTTP
#   [2] UPF-slice-1..N (PL upf_id site) → wait UPF→NRF registration
#   [3] SMF (central) → wait SMF→NRF registration
#   [4] Wait SMF↔UPF PFCP
#   [5] CU-CP (edge)
#   [6] CU-UP 1..N (PL cu_id site — may differ from UPF)
#   [7] FlexRIC + DU (edge) + settle for F1
#   [8] UEs 1..N one-by-one (pod delete + wait oaitun)
#
# Usage:
#   ./backend/scripts/profile_rollout.sh
#   ./backend/scripts/profile_rollout.sh --step 2          # UPF only
#   ./backend/scripts/profile_rollout.sh --step upf
#   ./backend/scripts/profile_rollout.sh --from-step 3       # SMF → end
#   ./backend/scripts/profile_rollout.sh --nrf-http-wait   # also probe SBI HTTP (slow/unreliable)
#   SKIP_UES=1 ./backend/scripts/profile_rollout.sh
#   SKIP_RAN=1 ./backend/scripts/profile_rollout.sh
#   ONLY_UES=1 ./backend/scripts/profile_rollout.sh
#
# Steps: 1=nrf  2=upf  3=smf  4=pfcp  5=cu-cp  6=cu-up  7=du  8=ue
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SSH_CFG="${SSH_CFG:-$SCRIPT_DIR/ssh_config}"
PROFILE_NS="${PROFILE_NS:-ina-infra}"
CENTRAL_HOST="${CENTRAL_HOST:-central-0}"
REGIONAL_HOST="${REGIONAL_HOST:-regional-0}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
TIMEOUT="${TIMEOUT:-180}"
UE_GAP_SEC="${UE_GAP_SEC:-0}"
PDU_WAIT_SEC="${PDU_WAIT_SEC:-90}"
DU_SETTLE_SEC="${DU_SETTLE_SEC:-20}"
PFCP_WAIT_SEC="${PFCP_WAIT_SEC:-120}"
NRF_WAIT_SEC="${NRF_WAIT_SEC:-120}"
NRF_LOG_SINCE="${NRF_LOG_SINCE:-5m}"
SKIP_UES="${SKIP_UES:-0}"
SKIP_RAN="${SKIP_RAN:-0}"
ONLY_UES="${ONLY_UES:-0}"
OAITUN_IFACE="${OAITUN_IFACE:-oaitun_ue1}"
STEP_FROM=1
STEP_TO=8
SKIP_NRF_WAIT="${SKIP_NRF_WAIT:-0}"
NRF_HTTP_WAIT="${NRF_HTTP_WAIT:-0}"

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
    5|cu-cp|cucp) printf '5' ;;
    6|cu-up|cuup) printf '6' ;;
    7|du|ran-du) printf '7' ;;
    8|ue|ues) printf '8' ;;
    *)
      echo "Unknown step: $1 (use 1-8 or nrf|upf|smf|pfcp|cu-cp|cu-up|du|ue)" >&2
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
      ONLY_UES=0
      shift 2
      ;;
    --from-step)
      STEP_FROM="$(step_to_num "${2:?}")" || exit 1
      ONLY_UES=0
      shift 2
      ;;
    --to-step)
      STEP_TO="$(step_to_num "${2:?}")" || exit 1
      shift 2
      ;;
    --skip-nrf-wait) SKIP_NRF_WAIT=1; shift ;;
    --nrf-http-wait) NRF_HTTP_WAIT=1; shift ;;
    --nrf-ready-only) SKIP_NRF_WAIT=1; shift ;;  # deprecated alias
    --skip-ues) SKIP_UES=1; shift ;;
    --skip-ran) SKIP_RAN=1; shift ;;
    --only-ues) ONLY_UES=1; shift ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ "$ONLY_UES" == "1" ]]; then
  STEP_FROM=8
  STEP_TO=8
fi

should_run_step() {
  local n="$1"
  (( n >= STEP_FROM && n <= STEP_TO ))
}

# Filled by load_placement: parallel arrays keyed by slice index (0-based).
declare -a SLICE_NS_IDX=()
declare -a UPF_SITE=()
declare -a CU_SITE=()
SLICE_COUNT="${SLICE_COUNT:-0}"

host_for_site() {
  case "$1" in
    central) printf '%s' "$CENTRAL_HOST" ;;
    regional) printf '%s' "$REGIONAL_HOST" ;;
    *) printf '%s' "$EDGE_HOST" ;;
  esac
}

ssh_host() {
  local host="$1"
  shift
  ssh -F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 "$host" "$@"
}

restart_deploy() {
  local host="$1" ns="$2" deploy="$3"
  echo "  restart ${host}: ${ns}/deployment/${deploy}"
  ssh_host "$host" kubectl -n "$ns" rollout restart "deployment/${deploy}"
}

wait_rollout() {
  local host="$1" ns="$2" deploy="$3"
  echo "  wait   ${host}: ${ns}/deployment/${deploy} (timeout=${TIMEOUT}s)"
  ssh_host "$host" kubectl -n "$ns" rollout status "deployment/${deploy}" --timeout="${TIMEOUT}s"
}

site_from_upf_id() {
  case "$1" in
    0) printf 'edge' ;;
    1) printf 'regional' ;;
    2) printf 'central' ;;
    *) printf 'central' ;;
  esac
}

load_placement() {
  local blob
  echo "  load placement from ${CENTRAL_HOST}: ${PROFILE_NS}/cm/ina-pl-placement"
  blob="$(
    ssh_host "$CENTRAL_HOST" \
      "kubectl -n ${PROFILE_NS} get cm ina-pl-placement -o jsonpath='{.data.placement\\.json}'" \
      2>/dev/null || true
  )"
  if [[ -z "$blob" ]]; then
    echo "ERROR: cannot read ${PROFILE_NS}/ina-pl-placement on ${CENTRAL_HOST}" >&2
    echo "Apply the profile first (Push to Gitea), then re-run rollout." >&2
    exit 1
  fi

  # Populate SLICE_NS_IDX + UPF_SITE (upf_id) + CU_SITE (cu_id) from deploy_map.
  eval "$(
    SLICE_COUNT_ENV="${SLICE_COUNT}" python3 -c '
import json, os, sys
raw = sys.stdin.read()
data = json.loads(raw)
dm = data.get("deploy_map") or {}
ip = data.get("ip_plan") or {}
n_env = int(os.environ.get("SLICE_COUNT_ENV") or "0")
n_plan = int(ip.get("n_slices") or 0)
n = n_env if n_env > 0 else (n_plan if n_plan > 0 else len(dm))
id_to_site = {0: "edge", 1: "regional", 2: "central"}
print(f"SLICE_COUNT={n}")
print("SLICE_NS_IDX=()")
print("UPF_SITE=()")
print("CU_SITE=()")
for i in range(1, n + 1):
    place = dm.get(str(i)) or {}
    upf_id = place.get("upf_id")
    cu_id = place.get("cu_id")
    if upf_id is None:
        upf_id = 2
    if cu_id is None:
        cu_id = 0
    upf_site = id_to_site.get(int(upf_id), "central")
    cu_site = id_to_site.get(int(cu_id), "edge")
    print(f"SLICE_NS_IDX+=(\"{i}\")")
    print(f"UPF_SITE+=(\"{upf_site}\")")
    print(f"CU_SITE+=(\"{cu_site}\")")
' <<<"$blob"
  )"
}

ue_oaitun() {
  local n="$1"
  ssh_host "$EDGE_HOST" \
    "kubectl -n ${PROFILE_NS} exec deploy/oai-ue-${n} -c ue -- ip -4 -o addr show ${OAITUN_IFACE} 2>/dev/null | awk '{print \$4; exit}'" \
    2>/dev/null || true
}

wait_ue_pdu() {
  local n="$1"
  local deadline=$((SECONDS + PDU_WAIT_SEC))
  local ip=""
  while (( SECONDS < deadline )); do
    ip="$(ue_oaitun "$n")"
    if [[ -n "$ip" ]]; then
      echo "  OK     UE${n} ${OAITUN_IFACE}=${ip}"
      return 0
    fi
    sleep 3
  done
  echo "  FAIL   UE${n} no ${OAITUN_IFACE} within ${PDU_WAIT_SEC}s" >&2
  return 1
}

step_nrf() {
  echo "==> [1/8] NRF (central) → wait deployment Ready"
  restart_deploy "$CENTRAL_HOST" "$PROFILE_NS" nrf-core
  wait_rollout "$CENTRAL_HOST" "$PROFILE_NS" nrf-core
  echo "  OK     NRF deployment Ready"
  if [[ "$SKIP_NRF_WAIT" == "1" || "$NRF_HTTP_WAIT" != "1" ]]; then
    [[ "$NRF_HTTP_WAIT" != "1" ]] && echo "  skip NRF HTTP probe (use --nrf-http-wait to enable)"
    return 0
  fi
  echo "  probing NRF SBI HTTP ..."
  NRF_WAIT_SEC="$NRF_WAIT_SEC" \
    "$SCRIPT_DIR/wait_nrf_http.sh" --ns "$PROFILE_NS" \
    --timeout "$NRF_WAIT_SEC"
}

step_upf() {
  echo "==> [2/8] UPF-slice-1..${SLICE_COUNT} → wait UPF→NRF"
  local n site host
  # Controller bakes NRF into Deployment init only at create; re-sync after subnet change.
  "$SCRIPT_DIR/profile_sync_upf_nrf_wait.sh" "$PROFILE_NS"
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="${UPF_SITE[$((n - 1))]}"
    host="$(host_for_site "$site")"
    restart_deploy "$host" "$PROFILE_NS" "upf-slice-${n}"
  done
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="${UPF_SITE[$((n - 1))]}"
    host="$(host_for_site "$site")"
    wait_rollout "$host" "$PROFILE_NS" "upf-slice-${n}"
  done
  echo "  settle 5s for UPF N4 sockets..."
  sleep 5
  NRF_WAIT_SEC="$NRF_WAIT_SEC" NRF_LOG_SINCE="$NRF_LOG_SINCE" \
    "$SCRIPT_DIR/wait_upf_nrf.sh" --ns "$PROFILE_NS" \
    --timeout "$NRF_WAIT_SEC" --since "$NRF_LOG_SINCE" "${wait_args[@]}"
}

step_smf() {
  echo "==> [3/8] SMF (central) → wait SMF→NRF"
  restart_deploy "$CENTRAL_HOST" "$PROFILE_NS" smf-core
  wait_rollout "$CENTRAL_HOST" "$PROFILE_NS" smf-core
  sleep 5
  NRF_WAIT_SEC="$NRF_WAIT_SEC" NRF_LOG_SINCE="$NRF_LOG_SINCE" \
    "$SCRIPT_DIR/profile_wait_smf_nrf.sh" "$PROFILE_NS" \
    --timeout "$NRF_WAIT_SEC" --since "$NRF_LOG_SINCE"
}

step_pfcp() {
  echo "==> [4/8] Wait SMF↔UPF PFCP association"
  if ! PFCP_WAIT_SEC="$PFCP_WAIT_SEC" \
    "$SCRIPT_DIR/profile_wait_smf_pfcp_upfs.sh" "$PROFILE_NS" \
    --timeout "$PFCP_WAIT_SEC" --since "$NRF_LOG_SINCE" "${wait_args[@]}"; then
    echo "  PFCP incomplete — restart SMF again (UPFs already up)"
    restart_deploy "$CENTRAL_HOST" "$PROFILE_NS" smf-core
    wait_rollout "$CENTRAL_HOST" "$PROFILE_NS" smf-core
    sleep 8
    if ! PFCP_WAIT_SEC="$PFCP_WAIT_SEC" \
      "$SCRIPT_DIR/profile_wait_smf_pfcp_upfs.sh" "$PROFILE_NS" \
      --timeout "$PFCP_WAIT_SEC" --since "$NRF_LOG_SINCE" "${wait_args[@]}"; then
      echo "ERROR: SMF did not associate all UPF-slice-1..${SLICE_COUNT}." >&2
      echo "Check: ssh ${CENTRAL_HOST} kubectl -n ${PROFILE_NS} logs deploy/smf-core --since=5m | grep ASSOCIATION" >&2
      exit 1
    fi
  fi
  echo "  OK     all ${SLICE_COUNT} UPFs connected to SMF"
}

step_cu_cp() {
  echo "==> [5/8] CU-CP (edge)"
  restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-cu-cp
  wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-cu-cp
}

step_cu_up() {
  echo "==> [6/8] CU-UP 1..${SLICE_COUNT}"
  local n site host
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="${CU_SITE[$((n - 1))]}"
    host="$(host_for_site "$site")"
    restart_deploy "$host" "$PROFILE_NS" "oai-cu-up-${n}"
  done
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="${CU_SITE[$((n - 1))]}"
    host="$(host_for_site "$site")"
    wait_rollout "$host" "$PROFILE_NS" "oai-cu-up-${n}"
  done
}

step_du() {
  echo "==> [7/8] FlexRIC + DU (edge)"
  if ssh_host "$EDGE_HOST" kubectl -n "$PROFILE_NS" get deploy oai-flexric >/dev/null 2>&1; then
    restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-flexric
    wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-flexric
  fi
  restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-du
  wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-du
  echo "  settle ${DU_SETTLE_SEC}s for F1 / cell..."
  sleep "$DU_SETTLE_SEC"
}

step_ues() {
  echo "==> [8/8] UEs 1..${SLICE_COUNT} (pod delete${UE_GAP_SEC:+, ${UE_GAP_SEC}s gap})"
  local n failed=0 ip
  for n in $(seq 1 "$SLICE_COUNT"); do
    echo "--- UE${n} ---"
    ssh_host "$EDGE_HOST" kubectl -n "$PROFILE_NS" delete pod \
      -l "app.kubernetes.io/name=oai-ue-${n}" \
      --wait=true --timeout="${TIMEOUT}s" || true
    wait_rollout "$EDGE_HOST" "$PROFILE_NS" "oai-ue-${n}"
    if ! wait_ue_pdu "$n"; then
      failed=$((failed + 1))
    fi
    if (( n < SLICE_COUNT && UE_GAP_SEC > 0 )); then
      echo "  gap ${UE_GAP_SEC}s before next UE..."
      sleep "$UE_GAP_SEC"
    fi
  done

  echo "==> Summary (PDU / oaitun)"
  for n in $(seq 1 "$SLICE_COUNT"); do
    ip="$(ue_oaitun "$n")"
    echo "  UE${n} ${OAITUN_IFACE}=${ip:-<none>}"
  done

  if (( failed > 0 )); then
    echo "ERROR: ${failed}/${SLICE_COUNT} UE(s) missing ${OAITUN_IFACE}." >&2
    exit 1
  fi
  echo "Done. All ${SLICE_COUNT} UEs have ${OAITUN_IFACE} in ns=${PROFILE_NS}."
  echo "Next: ./scripts/ina-infra-ping-test.sh --dnn"
}

echo "==> INA profile namespace rollout (ns=${PROFILE_NS})"
echo "    central=${CENTRAL_HOST} regional=${REGIONAL_HOST} edge=${EDGE_HOST}"
echo "    UE_GAP_SEC=${UE_GAP_SEC} PDU_WAIT_SEC=${PDU_WAIT_SEC} DU_SETTLE_SEC=${DU_SETTLE_SEC}"
echo "    NRF_WAIT_SEC=${NRF_WAIT_SEC} PFCP_WAIT_SEC=${PFCP_WAIT_SEC}"
echo "    steps=${STEP_FROM}..${STEP_TO}  nrf_http_wait=${NRF_HTTP_WAIT}"

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

load_placement
echo "    slices=${SLICE_COUNT}"
echo "    UPF sites:$(
  for n in $(seq 1 "$SLICE_COUNT"); do
    printf ' %s→%s' "$n" "${UPF_SITE[$((n - 1))]}"
  done
)"
echo "    CU  sites:$(
  for n in $(seq 1 "$SLICE_COUNT"); do
    printf ' %s→%s' "$n" "${CU_SITE[$((n - 1))]}"
  done
)"

wait_args=()
for n in $(seq 1 "$SLICE_COUNT"); do
  wait_args+=(--slice "$n")
done

if should_run_step 1; then step_nrf; fi
if should_run_step 2; then step_upf; fi
if should_run_step 3; then step_smf; fi
if should_run_step 4; then step_pfcp; fi

if [[ "$SKIP_RAN" == "1" ]]; then
  echo "==> SKIP_RAN=1 — done (NRF+UPF+SMF+PFCP only)"
  exit 0
fi

if should_run_step 5; then step_cu_cp; fi
if should_run_step 6; then step_cu_up; fi
if should_run_step 7; then step_du; fi

if [[ "$SKIP_UES" == "1" ]] || ! should_run_step 8; then
  if [[ "$SKIP_UES" == "1" ]]; then
    echo "==> SKIP_UES=1 — done (no UE restart)"
  fi
  exit 0
fi

step_ues
