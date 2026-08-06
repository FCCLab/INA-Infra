#!/usr/bin/env bash
# After oai-ran-controller create-once, pin RAN Deployments to VPC Multus parents
# (node label ina-infra.nephio.lab/multus-master) and strip --sa from
# USE_ADDITIONAL_OPTIONS when the image rejects it (gnb.conf already has sa=1).
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

echo "==> VPC nodeSelectors on ${CTX}/${NS} (CU=${CU_MASTER} DU=${DU_MASTER})"

kubectl --context "$CTX" -n "$NS" patch deployment oai-cu-cp --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${CU_MASTER}\"}}}}}"

kubectl --context "$CTX" -n "$NS" patch deployment oai-cu-up --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${CU_MASTER}\"}}}}}"

kubectl --context "$CTX" -n "$NS" patch deployment oai-du --type=merge -p \
  "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"ina-infra.nephio.lab/multus-master\":\"${DU_MASTER}\",\"kubernetes.io/hostname\":\"usrp\"}}}}}"

echo "==> Strip --sa from USE_ADDITIONAL_OPTIONS (sa=1 already in conf)"
strip_sa_flag oai-cu-cp
strip_sa_flag oai-cu-up
strip_sa_flag oai-du

echo "==> Done. Pods:"
kubectl --context "$CTX" -n "$NS" get pods -o wide | grep -E 'NAME|oai-cu|oai-du|oai-ue|upf'
