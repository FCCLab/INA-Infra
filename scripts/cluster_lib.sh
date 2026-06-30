# Shared workload-cluster definitions (sourced by bringup/install scripts).
ALL_CLUSTERS=(central regional edge ue)

declare -A CLUSTER_CP_HOST=(
  [central]=central-0
  [regional]=regional-0
  [edge]=edge-0
  [ue]=ue-0
)
declare -A CLUSTER_WORKER_HOST=(
  [central]=central-1
  [regional]=regional-1
  [edge]=edge-1
  [ue]=ue-1
)
declare -A CLUSTER_API_IP=(
  [central]=10.1.132.210
  [regional]=10.1.132.220
  [edge]=10.1.132.230
  [ue]=10.1.132.240
)
declare -A CLUSTER_WORKER_IP=(
  [central]=10.1.132.211
  [regional]=10.1.132.221
  [edge]=10.1.132.231
  [ue]=10.1.132.241
)
declare -A CLUSTER_DASHBOARD_VIP=(
  [central]=10.1.132.41
  [regional]=10.1.132.42
  [edge]=10.1.132.43
  [ue]=10.1.132.44
)
declare -A CLUSTER_OPENSPEEDTEST_VIP=(
  [central]=10.1.132.60
  [regional]=10.1.132.70
  [edge]=10.1.132.80
  [ue]=10.1.132.90
)

METALLB_POOL="${METALLB_POOL:-10.1.132.10-10.1.132.99}"

MGMT_CP_HOST="${MGMT_CP_HOST:-mgmt-0}"
MGMT_OPENSPEEDTEST_VIP="${MGMT_OPENSPEEDTEST_VIP:-10.1.132.50}"

ALL_OPENSPEEDTEST_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
ALL_METALLB_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

cluster_cp_host() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_CP_HOST"
  else
    printf '%s' "${CLUSTER_CP_HOST[$cluster]}"
  fi
}

openspeedtest_vip() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_OPENSPEEDTEST_VIP"
  else
    printf '%s' "${CLUSTER_OPENSPEEDTEST_VIP[$cluster]}"
  fi
}

kubeconfig_file() {
  printf 'config-%s' "$1"
}

kube_context() {
  printf '%s@%s' "$1" "$1"
}
