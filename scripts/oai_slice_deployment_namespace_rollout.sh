#!/usr/bin/env bash
# Staged rollout for oai-slice-deployment + PFCP prerequisite.
#
# Order (check each step):
#   [1] UPF-slice-1..N first (site: 1→central, 2→regional, 3–5→edge), then SMF
#       (OAI SMF discover_upf:no — ASSOCIATION SETUP only at SMF start)
#   [2] Wait until each UPF has PFCP association with SMF (SX SETUP / HEARTBEAT)
#   [3] CU-CP (edge)
#   [4] CU-UP 1..N (co-located with UPF site)
#   [5] DU (edge) + settle for F1 / cell
#   [6] UEs 1..N one-by-one (pod delete + wait oaitun)
#
# Usage:
#   ./scripts/oai_slice_deployment_namespace_rollout.sh
#   SKIP_UES=1 ./scripts/oai_slice_deployment_namespace_rollout.sh
#   SKIP_RAN=1 ./scripts/oai_slice_deployment_namespace_rollout.sh   # SMF+UPF+PFCP only
#   ONLY_UES=1 ./scripts/oai_slice_deployment_namespace_rollout.sh   # UEs only (skip PFCP/RAN)
#   UE_GAP_SEC=45 PDU_WAIT_SEC=120 ./scripts/oai_slice_deployment_namespace_rollout.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SLICE_NS="${OAI_SLICE_NS:-oai-slice-deployment}"
CN_NS="${OAI_CN_NS:-oai-cn}"
UPF_NS="${OAI_UPF_NS:-oai-upf}"
CENTRAL_HOST="${CENTRAL_HOST:-central-0}"
REGIONAL_HOST="${REGIONAL_HOST:-regional-0}"
EDGE_HOST="${EDGE_HOST:-edge-0}"
TIMEOUT="${TIMEOUT:-180}"
SLICE_COUNT="${SLICE_COUNT:-${OAI_SLICE_COUNT:-5}}"
UE_GAP_SEC="${UE_GAP_SEC:-30}"
PDU_WAIT_SEC="${PDU_WAIT_SEC:-90}"
DU_SETTLE_SEC="${DU_SETTLE_SEC:-20}"
PFCP_WAIT_SEC="${PFCP_WAIT_SEC:-120}"
SKIP_UES="${SKIP_UES:-0}"
SKIP_RAN="${SKIP_RAN:-0}"
ONLY_UES="${ONLY_UES:-0}"
OAITUN_IFACE="${OAITUN_IFACE:-oaitun_ue1}"

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

upf_container() {
  # Primary container is upf-slice-N
  printf 'upf-slice-%s' "$1"
}

# True if UPF has seen PFCP association / heartbeat from SMF recently.
# NOTE: UPF Deployments share selector workload.nephio.org/oai=upf, so
# `kubectl logs deploy/upf-slice-N` picks a random UPF pod — must use pod name.
upf_pfcp_connected() {
  local n="$1"
  local site host c pod
  site="$(oai_slice_site "$n")"
  host="$(host_for_site "$site")"
  c="$(upf_container "$n")"
  pod="$(
    ssh_host "$host" kubectl -n "$UPF_NS" get pods -o name 2>/dev/null \
      | grep -E "^pod/upf-slice-${n}-" | head -n1 | cut -d/ -f2 || true
  )"
  [[ -z "$pod" ]] && return 1
  ssh_host "$host" kubectl -n "$UPF_NS" logs "$pod" -c "$c" --since=5m 2>/dev/null \
    | grep -qE 'Handle SX ASSOCIATION SETUP REQUEST|Received SX HEARTBEAT REQUEST|Received N4 ASSOCIATION'
}

wait_all_upf_pfcp() {
  local deadline=$((SECONDS + PFCP_WAIT_SEC))
  local missing n site host
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
        echo "  OK     UPF${n} N4=$(upf_slice_n4 "$n") PFCP associated/heartbeat"
      done
      return 0
    fi
    echo "  ... missing UPF PFCP: ${missing[*]} (retry)"
    sleep 5
  done

  echo "  FAIL   PFCP not ready for all UPFs within ${PFCP_WAIT_SEC}s" >&2
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="$(oai_slice_site "$n")"
    host="$(host_for_site "$site")"
    if upf_pfcp_connected "$n"; then
      echo "  OK     UPF${n} N4=$(upf_slice_n4 "$n")"
    else
      echo "  FAIL   UPF${n} N4=$(upf_slice_n4 "$n") on ${host} — no SX ASSOCIATION/HEARTBEAT" >&2
    fi
  done
  return 1
}

ue_oaitun() {
  local n="$1"
  # Must pass ONE remote command string so sh -c keeps "ip -4 -o ..." intact.
  # Unquoted ssh args become: sh -c ip -4 -o ...  →  sh only runs "ip" (no iface).
  ssh_host "$EDGE_HOST" \
    "kubectl -n ${SLICE_NS} exec deploy/oai-ue-${n} -c ue -- ip -4 -o addr show ${OAITUN_IFACE} 2>/dev/null | awk '{print \$4; exit}'" \
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

echo "==> oai-slice-deployment namespace rollout (ns=${SLICE_NS})"
echo "    central=${CENTRAL_HOST} regional=${REGIONAL_HOST} edge=${EDGE_HOST} slices=${SLICE_COUNT}"
echo "    UE_GAP_SEC=${UE_GAP_SEC} PDU_WAIT_SEC=${PDU_WAIT_SEC} DU_SETTLE_SEC=${DU_SETTLE_SEC} PFCP_WAIT_SEC=${PFCP_WAIT_SEC}"

if [[ "$ONLY_UES" != "1" ]]; then
  echo "==> [1/6] UPF-slice-1..${SLICE_COUNT}, then SMF"
  # UPFs must be listening on N4 before SMF sends ASSOCIATION SETUP.
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="$(oai_slice_site "$n")"
    host="$(host_for_site "$site")"
    restart_deploy "$host" "$UPF_NS" "upf-slice-${n}"
  done
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="$(oai_slice_site "$n")"
    host="$(host_for_site "$site")"
    wait_rollout "$host" "$UPF_NS" "upf-slice-${n}"
  done
  echo "  settle 5s for UPF N4 sockets..."
  sleep 5

  restart_deploy "$CENTRAL_HOST" "$CN_NS" smf-core
  wait_rollout "$CENTRAL_HOST" "$CN_NS" smf-core
  sleep 5

  echo "==> [2/6] Check SMF↔UPF PFCP association"
  if ! wait_all_upf_pfcp; then
    echo "  PFCP incomplete — restart SMF again (UPFs already up)"
    restart_deploy "$CENTRAL_HOST" "$CN_NS" smf-core
    wait_rollout "$CENTRAL_HOST" "$CN_NS" smf-core
    sleep 8
    if ! wait_all_upf_pfcp; then
      echo "ERROR: SMF did not associate all UPF-slice-1..${SLICE_COUNT}." >&2
      echo "Check: ssh ${CENTRAL_HOST} kubectl -n ${CN_NS} logs deploy/smf-core -c smf-core --since=5m | grep ASSOCIATION" >&2
      exit 1
    fi
  fi
  echo "  OK     all ${SLICE_COUNT} UPFs connected to SMF"

  if [[ "$SKIP_RAN" == "1" ]]; then
    echo "==> SKIP_RAN=1 — done (UPF+SMF+PFCP only)"
    exit 0
  fi

  echo "==> [3/6] CU-CP (edge)"
  restart_deploy "$EDGE_HOST" "$SLICE_NS" oai-cu-cp
  wait_rollout "$EDGE_HOST" "$SLICE_NS" oai-cu-cp

  echo "==> [4/6] CU-UP 1..${SLICE_COUNT}"
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="$(oai_slice_site "$n")"
    host="$(host_for_site "$site")"
    restart_deploy "$host" "$SLICE_NS" "oai-cu-up-${n}"
  done
  for n in $(seq 1 "$SLICE_COUNT"); do
    site="$(oai_slice_site "$n")"
    host="$(host_for_site "$site")"
    wait_rollout "$host" "$SLICE_NS" "oai-cu-up-${n}"
  done

  echo "==> [5/6] DU (edge)"
  restart_deploy "$EDGE_HOST" "$SLICE_NS" oai-du
  wait_rollout "$EDGE_HOST" "$SLICE_NS" oai-du
  echo "  settle ${DU_SETTLE_SEC}s for F1 / cell..."
  sleep "$DU_SETTLE_SEC"
else
  echo "==> ONLY_UES=1 — skipping SMF/UPF/RAN"
fi

if [[ "$SKIP_UES" == "1" ]]; then
  echo "==> SKIP_UES=1 — done (no UE restart)"
  exit 0
fi

echo "==> [6/6] UEs 1..${SLICE_COUNT} (pod delete, ${UE_GAP_SEC}s gap)"
# Do not scale replicas=0: Config Sync restores replicas=1 immediately.
failed=0
for n in $(seq 1 "$SLICE_COUNT"); do
  echo "--- UE${n} ---"
  ssh_host "$EDGE_HOST" kubectl -n "$SLICE_NS" delete pod \
    -l "app.kubernetes.io/name=oai-ue-${n}" \
    --wait=true --timeout="${TIMEOUT}s" || true
  wait_rollout "$EDGE_HOST" "$SLICE_NS" "oai-ue-${n}"
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
  echo "Hints:" >&2
  echo "  - AMF NGAP: ssh ${CENTRAL_HOST} kubectl -n ${CN_NS} logs deploy/amf-core -c amf-core --tail=40" >&2
  echo "  - SMF UPF:  ssh ${CENTRAL_HOST} kubectl -n ${CN_NS} logs deploy/smf-core -c smf-core --since=5m | grep -i ASSOCIATION" >&2
  echo "  - DU RA:    ssh ${EDGE_HOST} kubectl -n ${SLICE_NS} logs deploy/oai-du -c du | grep -E 'no free RA|in-sync'" >&2
  exit 1
fi

echo "Done. All ${SLICE_COUNT} UEs have ${OAITUN_IFACE}."
echo "Next: ./scripts/oai_slice_deployment_namespace_ping_test.sh --dnn"
