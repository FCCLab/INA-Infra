#!/usr/bin/env bash
# After oai-ran-controller create-once, pin RAN Deployments to VPC Multus parents
# (node label ina-infra.nephio.lab/multus-master) and strip --sa from
# USE_ADDITIONAL_OPTIONS when the image rejects it (gnb.conf already has sa=1).
#
# Waits for operator create-once Deployments (Config Sync + reconcile race).
#
#   ./scripts/patch_oai_benchmark_ran_vpc.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config:$HOME/.kube/config-central:$HOME/.kube/config-regional:$HOME/.kube/config-edge}"

CTX="${OAI_BENCH_EDGE_CONTEXT:-edge@edge}"
NS="${BENCH_NS:-oai-benchmark}"
CU_MASTER="${OAI_BENCH_CU_MULTUS_MASTER:-${SITE_IFACE:-enp7s0}}"
DU_MASTER="${OAI_BENCH_DU_MULTUS_MASTER:-${USRP_SITE_IFACE:-enp4s0f0}}"
DU_NODE="${OAI_BENCH_DU_NODE:-usrp}"
WAIT_SEC="${OAI_BENCH_PATCH_WAIT_SEC:-240}"
WAIT_INTERVAL="${OAI_BENCH_PATCH_WAIT_INTERVAL:-5}"

strip_sa_flag() {
  # Remove standalone --sa from USE_ADDITIONAL_OPTIONS; keep --rfsim / telnet / etc.
  local deploy="$1"
  local cur
  cur="$(kubectl --context "$CTX" -n "$NS" get deploy "$deploy" \
    -o jsonpath='{range .spec.template.spec.containers[0].env[?(@.name=="USE_ADDITIONAL_OPTIONS")]}{.value}{end}' 2>/dev/null || true)"
  [[ -z "$cur" ]] && return 0
  local new
  new="$(echo "$cur" | sed -E 's/(^|[[:space:]])--sa([[:space:]]|$)/\1\2/g; s/[[:space:]]+/ /g; s/^ //; s/ $//')"
  if [[ "$new" != "$cur" ]]; then
    echo "  $deploy: strip --sa → ${new}"
    kubectl --context "$CTX" -n "$NS" set env "deployment/${deploy}" \
      "USE_ADDITIONAL_OPTIONS=${new}"
  fi
}

wait_for_ran_deployments() {
  local deadline=$((SECONDS + WAIT_SEC))
  local missing
  echo "==> Waiting up to ${WAIT_SEC}s for oai-cu-cp / oai-cu-up / oai-du (operator create-once)…"
  while (( SECONDS < deadline )); do
    missing=()
    for d in oai-cu-cp oai-cu-up oai-du; do
      if ! kubectl --context "$CTX" -n "$NS" get "deployment/${d}" >/dev/null 2>&1; then
        missing+=("$d")
      fi
    done
    if ((${#missing[@]} == 0)); then
      echo "  all RAN Deployments present"
      return 0
    fi
    echo "  missing: ${missing[*]} — retry in ${WAIT_INTERVAL}s"
    sleep "$WAIT_INTERVAL"
  done
  echo "ERROR: timed out after ${WAIT_SEC}s waiting for: ${missing[*]}" >&2
  echo "  kubectl --context ${CTX} -n ${NS} get nfdeployment,deploy" >&2
  return 1
}

wait_for_ran_deployments

echo "==> VPC nodeSelectors on ${CTX}/${NS} (CU=${CU_MASTER} DU=${DU_MASTER} DU_NODE=${DU_NODE})"

kubectl --context "$CTX" -n "$NS" patch deployment oai-cu-cp --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${CU_MASTER}\"}}}}}"

kubectl --context "$CTX" -n "$NS" patch deployment oai-cu-up --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${CU_MASTER}\"}}}}}"

kubectl --context "$CTX" -n "$NS" patch deployment oai-du --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${DU_MASTER}\",\"kubernetes.io/hostname\":\"${DU_NODE}\"}}}}}"

# CU-UP: non-privileged → SCHED_OTHER so CFS cpu.max applies.
# CU-CP + DU: privileged → SCHED_RR (bypasses CFS).
echo "==> Pin securityContext (CU-UP=OTHER; CU-CP+DU=RR/privileged)"
CUUP_SEC='{"privileged":false,"capabilities":{"drop":["ALL"],"add":["NET_ADMIN","NET_RAW","IPC_LOCK"]}}'
RR_SEC='{"privileged":true}'
kubectl --context "$CTX" -n "$NS" patch deployment/oai-cu-up --type=json -p \
  "[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/securityContext\",\"value\":${CUUP_SEC}}]"
for d in oai-cu-cp oai-du; do
  kubectl --context "$CTX" -n "$NS" patch "deployment/${d}" --type=json -p \
    "[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/securityContext\",\"value\":${RR_SEC}}]"
done

# nosysnice create-once often omits DU rf Multus; UE waits on tcp://DU_RF:4043.
DU_F1_IP="$(oai_bench_du_f1)"
DU_RF_IP="$(oai_bench_du_rf)"
GW="${OAI_MACVLAN_GW}"
echo "==> Ensure DU Multus f1+rf annotation"
python3 - "$CTX" "$NS" "$DU_F1_IP" "$DU_RF_IP" "$GW" <<'PY'
import json, subprocess, sys
ctx, ns, f1, rf, gw = sys.argv[1:6]
net = json.dumps([
    {"name": "du-bench-f1", "interface": "f1", "ips": [f"{f1}/24"], "gateways": [gw]},
    {"name": "du-bench-rf", "interface": "rf", "ips": [f"{rf}/24"], "gateways": [gw]},
])
patch = [{"op": "add", "path": "/spec/template/metadata/annotations/k8s.v1.cni.cncf.io~1networks", "value": net}]
r = subprocess.run(
    ["kubectl", "--context", ctx, "-n", ns, "patch", "deployment", "oai-du",
     "--type=json", "-p", json.dumps(patch)],
    capture_output=True, text=True,
)
if r.returncode != 0:
    patch[0]["op"] = "replace"
    r = subprocess.run(
        ["kubectl", "--context", ctx, "-n", ns, "patch", "deployment", "oai-du",
         "--type=json", "-p", json.dumps(patch)],
        check=False,
    )
    raise SystemExit(r.returncode)
print(r.stdout.strip() or "patched")
PY

echo "==> Strip --sa from USE_ADDITIONAL_OPTIONS (sa=1 already in conf)"
strip_sa_flag oai-cu-cp
strip_sa_flag oai-cu-up
strip_sa_flag oai-du

echo "==> Done. Pods:"
kubectl --context "$CTX" -n "$NS" get pods -o wide | grep -E 'NAME|oai-cu|oai-du|oai-ue|upf' || true
