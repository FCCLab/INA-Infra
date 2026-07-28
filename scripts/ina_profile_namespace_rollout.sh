#!/usr/bin/env bash
# Staged rollout for an INA-Infra profile namespace (default: ina-infra).
#
# Same bring-up order as oai_slice_deployment_namespace_rollout.sh, but all
# Deployments live in PROFILE_NS and UPF sites come from PL placement CM.
#
# Order:
#   [1] UPF-slice-1..N (PL upf_id site), then SMF (central)
#   [2] Wait SMF↔UPF PFCP
#   [3] CU-CP (edge)
#   [4] CU-UP 1..N (PL cu_id site — may differ from UPF)
#   [5] FlexRIC + DU (edge) + settle for F1
#   [6] UEs 1..N one-by-one (pod delete + wait oaitun)
#
# Usage:
#   ./scripts/ina_profile_namespace_rollout.sh
#   PROFILE_NS=ina-infra SLICE_COUNT=4 ./scripts/ina_profile_namespace_rollout.sh
#   SKIP_UES=1 ./scripts/ina_profile_namespace_rollout.sh
#   SKIP_RAN=1 ./scripts/ina_profile_namespace_rollout.sh
#   ONLY_UES=1 ./scripts/ina_profile_namespace_rollout.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
PROFILE_NS="${PROFILE_NS:-ina-infra}"
CENTRAL_HOST="${CENTRAL_HOST:-central-0}"
REGIONAL_HOST="${REGIONAL_HOST:-regional-0}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
TIMEOUT="${TIMEOUT:-180}"
UE_GAP_SEC="${UE_GAP_SEC:-30}"
PDU_WAIT_SEC="${PDU_WAIT_SEC:-90}"
DU_SETTLE_SEC="${DU_SETTLE_SEC:-20}"
PFCP_WAIT_SEC="${PFCP_WAIT_SEC:-120}"
SKIP_UES="${SKIP_UES:-0}"
SKIP_RAN="${SKIP_RAN:-0}"
ONLY_UES="${ONLY_UES:-0}"
OAITUN_IFACE="${OAITUN_IFACE:-oaitun_ue1}"

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

upf_pfcp_connected() {
  local n="$1"
  local idx site host pod
  idx=$((n - 1))
  site="${UPF_SITE[$idx]}"
  host="$(host_for_site "$site")"
  pod="$(
    ssh_host "$host" kubectl -n "$PROFILE_NS" get pods -o name 2>/dev/null \
      | grep -E "^pod/upf-slice-${n}-" | head -n1 | cut -d/ -f2 || true
  )"
  [[ -z "$pod" ]] && return 1
  # Lab OAI UPF (older): SX ASSOCIATION / HEARTBEAT. Profile UPF (v2): pfcp / Association.
  ssh_host "$host" kubectl -n "$PROFILE_NS" logs "$pod" -c "upf-slice-${n}" --since=5m 2>/dev/null \
    | grep -qiE 'Handle SX ASSOCIATION SETUP REQUEST|Received SX HEARTBEAT REQUEST|Received N4 ASSOCIATION|Association Setup|HEARTBEAT_REQUEST|heartbeat request|PFCP Association'
}

smf_sees_upfs() {
  # Count distinct UPF N4 peers SMF has talked to (best-effort).
  local logs
  logs="$(
    ssh_host "$CENTRAL_HOST" \
      kubectl -n "$PROFILE_NS" logs deploy/smf-core -c smf-core --since=5m 2>/dev/null || true
  )"
  echo "$logs" | grep -qiE 'Association Setup Response|ASSOCIATION_SETUP_RESPONSE|Heartbeat Response|N4 Association'
}

wait_all_upf_pfcp() {
  local deadline=$((SECONDS + PFCP_WAIT_SEC))
  local missing n
  echo "  waiting up to ${PFCP_WAIT_SEC}s for SMF↔UPF PFCP on slices 1..${SLICE_COUNT}"
  while (( SECONDS < deadline )); do
    missing=()
    for n in $(seq 1 "$SLICE_COUNT"); do
      if ! upf_pfcp_connected "$n"; then
        missing+=("$n")
      fi
    done
    if [[ ${#missing[@]} -eq 0 ]]; then
      for n in $(seq 1 "$SLICE_COUNT"); do
        echo "  OK     UPF${n} site=${UPF_SITE[$((n - 1))]} PFCP associated/heartbeat"
      done
      return 0
    fi
    # Soft pass: SMF shows associations and all UPF pods Ready (assoc logs may differ by image).
    if smf_sees_upfs; then
      local all_ready=1
      for n in $(seq 1 "$SLICE_COUNT"); do
        site="${UPF_SITE[$((n - 1))]}"
        host="$(host_for_site "$site")"
        if ! ssh_host "$host" \
          "kubectl -n ${PROFILE_NS} get deploy upf-slice-${n} -o jsonpath='{.status.readyReplicas}'" \
          2>/dev/null | grep -qx '1'; then
          all_ready=0
          break
        fi
      done
      if [[ "$all_ready" == "1" && ${#missing[@]} -le "$SLICE_COUNT" ]]; then
        echo "  WARN   UPF PFCP log pattern incomplete; SMF shows N4 activity and UPFs are Ready — continuing"
        return 0
      fi
    fi
    echo "  ... missing UPF PFCP: ${missing[*]} (retry)"
    sleep 5
  done
  if [[ "${IGNORE_PFCP:-0}" == "1" ]]; then
    echo "  WARN   IGNORE_PFCP=1 — continuing without PFCP confirmation" >&2
    return 0
  fi
  echo "  FAIL   PFCP not ready for all UPFs within ${PFCP_WAIT_SEC}s" >&2
  return 1
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

echo "==> INA profile namespace rollout (ns=${PROFILE_NS})"
echo "    central=${CENTRAL_HOST} regional=${REGIONAL_HOST} edge=${EDGE_HOST}"
echo "    UE_GAP_SEC=${UE_GAP_SEC} PDU_WAIT_SEC=${PDU_WAIT_SEC} DU_SETTLE_SEC=${DU_SETTLE_SEC} PFCP_WAIT_SEC=${PFCP_WAIT_SEC}"

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

if [[ "$ONLY_UES" != "1" ]]; then
  echo "==> [1/6] UPF-slice-1..${SLICE_COUNT}, then SMF (central)"
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

  restart_deploy "$CENTRAL_HOST" "$PROFILE_NS" smf-core
  wait_rollout "$CENTRAL_HOST" "$PROFILE_NS" smf-core
  sleep 5

  echo "==> [2/6] Check SMF↔UPF PFCP association"
  if ! wait_all_upf_pfcp; then
    echo "  PFCP incomplete — restart SMF again (UPFs already up)"
    restart_deploy "$CENTRAL_HOST" "$PROFILE_NS" smf-core
    wait_rollout "$CENTRAL_HOST" "$PROFILE_NS" smf-core
    sleep 8
    if ! wait_all_upf_pfcp; then
      echo "ERROR: SMF did not associate all UPF-slice-1..${SLICE_COUNT}." >&2
      echo "Check: ssh ${CENTRAL_HOST} kubectl -n ${PROFILE_NS} logs deploy/smf-core -c smf-core --since=5m | grep ASSOCIATION" >&2
      exit 1
    fi
  fi
  echo "  OK     all ${SLICE_COUNT} UPFs connected to SMF"

  if [[ "$SKIP_RAN" == "1" ]]; then
    echo "==> SKIP_RAN=1 — done (UPF+SMF+PFCP only)"
    exit 0
  fi

  echo "==> [3/6] CU-CP (edge)"
  restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-cu-cp
  wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-cu-cp

  echo "==> [4/6] CU-UP 1..${SLICE_COUNT}"
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

  echo "==> [5/6] FlexRIC + DU (edge)"
  if ssh_host "$EDGE_HOST" kubectl -n "$PROFILE_NS" get deploy oai-flexric >/dev/null 2>&1; then
    restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-flexric
    wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-flexric
  fi
  restart_deploy "$EDGE_HOST" "$PROFILE_NS" oai-du
  wait_rollout "$EDGE_HOST" "$PROFILE_NS" oai-du
  echo "  settle ${DU_SETTLE_SEC}s for F1 / cell..."
  sleep "$DU_SETTLE_SEC"
else
  echo "==> ONLY_UES=1 — skipping SMF/UPF/RAN"
  if [[ "$SLICE_COUNT" -lt 1 ]]; then
    load_placement
  fi
fi

if [[ "$SKIP_UES" == "1" ]]; then
  echo "==> SKIP_UES=1 — done (no UE restart)"
  exit 0
fi

echo "==> [6/6] UEs 1..${SLICE_COUNT} (pod delete, ${UE_GAP_SEC}s gap)"
failed=0
for n in $(seq 1 "$SLICE_COUNT"); do
  echo "--- UE${n} ---"
  ssh_host "$EDGE_HOST" kubectl -n "$PROFILE_NS" delete pod \
    -l "app.kubernetes.io/name=oai-ue-${n}" \
    --wait=true --timeout="${TIMEOUT}s" || true
  wait_rollout "$EDGE_HOST" "$PROFILE_NS" "oai-ue-${n}"
  if ! wait_ue_pdu "$n"; then
    failed=$((failed + 1))
  fi
  if (( n < SLICE_COUNT )); then
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
