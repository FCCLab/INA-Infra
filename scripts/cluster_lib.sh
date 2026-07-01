# Shared workload-cluster definitions (sourced by bringup/install scripts).
ALL_CLUSTERS=(central regional edge ue)

SITE_IFACE="${SITE_IFACE:-enp7s0}"
MGMT_IFACE="${MGMT_IFACE:-enp1s0}"

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
# SSH / operator access (enp1s0, 10.1.132.0/24)
declare -A CLUSTER_MGMT_IP=(
  [central]=10.1.132.210
  [regional]=10.1.132.220
  [edge]=10.1.132.230
  [ue]=10.1.132.240
)
declare -A CLUSTER_MGMT_WORKER_IP=(
  [central]=10.1.132.211
  [regional]=10.1.132.221
  [edge]=10.1.132.231
  [ue]=10.1.132.241
)
# Kubernetes API and node identity (enp7s0, 10.1.137.0/24)
declare -A CLUSTER_API_IP=(
  [central]=10.1.137.110
  [regional]=10.1.137.120
  [edge]=10.1.137.130
  [ue]=10.1.137.140
)
declare -A CLUSTER_WORKER_IP=(
  [central]=10.1.137.111
  [regional]=10.1.137.121
  [edge]=10.1.137.131
  [ue]=10.1.137.141
)
declare -A CLUSTER_DASHBOARD_VIP=(
  [central]=10.1.137.41
  [regional]=10.1.137.42
  [edge]=10.1.137.43
  [ue]=10.1.137.44
)
declare -A CLUSTER_OPENSPEEDTEST_VIP=(
  [central]=10.1.137.60
  [regional]=10.1.137.70
  [edge]=10.1.137.80
  [ue]=10.1.137.90
)
# Nephio PackageVariantSet / WorkloadCluster label (nephio.org/site-type)
declare -A CLUSTER_SITE_TYPE=(
  [central]=core
  [regional]=regional
  [edge]=edge
  [ue]=ue
)

MGMT_METALLB_POOL="${MGMT_METALLB_POOL:-10.1.132.10-10.1.132.99}"
CLUSTER_METALLB_POOL="${CLUSTER_METALLB_POOL:-10.1.137.40-10.1.137.99}"

MGMT_CP_HOST="${MGMT_CP_HOST:-mgmt-0}"
MGMT_WORKER_HOST="${MGMT_WORKER_HOST:-mgmt-1}"
MGMT_API_IP="${MGMT_API_IP:-10.1.132.200}"
MGMT_WORKER_IP="${MGMT_WORKER_IP:-10.1.132.201}"
MGMT_DASHBOARD_VIP="${MGMT_DASHBOARD_VIP:-10.1.132.40}"
MGMT_OPENSPEEDTEST_VIP="${MGMT_OPENSPEEDTEST_VIP:-10.1.132.50}"

ALL_OPENSPEEDTEST_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
ALL_METALLB_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
ALL_BRINGUP_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

cluster_cp_host() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_CP_HOST"
  else
    printf '%s' "${CLUSTER_CP_HOST[$cluster]}"
  fi
}

cluster_mgmt_ip() {
  local host="$1"
  local cluster
  if [[ "$host" == "$MGMT_CP_HOST" || "$host" == mgmt-0 ]]; then
    printf '%s' "$MGMT_API_IP"
    return
  fi
  if [[ "$host" == "$MGMT_WORKER_HOST" || "$host" == mgmt-1 ]]; then
    printf '%s' "$MGMT_WORKER_IP"
    return
  fi
  for cluster in "${ALL_CLUSTERS[@]}"; do
    if [[ "${CLUSTER_CP_HOST[$cluster]}" == "$host" ]]; then
      printf '%s' "${CLUSTER_MGMT_IP[$cluster]}"
      return
    fi
    if [[ "${CLUSTER_WORKER_HOST[$cluster]}" == "$host" ]]; then
      printf '%s' "${CLUSTER_MGMT_WORKER_IP[$cluster]}"
      return
    fi
  done
  printf '%s' "$host"
}

# Kubernetes node InternalIP (kubelet --node-ip); site plane for workload clusters.
cluster_k8s_node_ip() {
  local host="$1"
  local cluster
  if [[ "$host" == "$MGMT_CP_HOST" || "$host" == mgmt-0 ]]; then
    printf '%s' "$MGMT_API_IP"
    return 0
  fi
  if [[ "$host" == "$MGMT_WORKER_HOST" || "$host" == mgmt-1 ]]; then
    printf '%s' "$MGMT_WORKER_IP"
    return 0
  fi
  for cluster in "${ALL_CLUSTERS[@]}"; do
    if [[ "${CLUSTER_CP_HOST[$cluster]}" == "$host" ]]; then
      printf '%s' "${CLUSTER_API_IP[$cluster]}"
      return 0
    fi
    if [[ "${CLUSTER_WORKER_HOST[$cluster]}" == "$host" ]]; then
      printf '%s' "${CLUSTER_WORKER_IP[$cluster]}"
      return 0
    fi
  done
  return 1
}

metallb_pool_for_cluster() {
  local cluster="$1"
  if [[ -n "${METALLB_POOL:-}" ]]; then
    printf '%s' "$METALLB_POOL"
  elif [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_METALLB_POOL"
  else
    printf '%s' "$CLUSTER_METALLB_POOL"
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

dashboard_vip() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_DASHBOARD_VIP"
  else
    printf '%s' "${CLUSTER_DASHBOARD_VIP[$cluster]}"
  fi
}

dashboard_mgmt_ip() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_API_IP"
  else
    printf '%s' "${CLUSTER_MGMT_IP[$cluster]}"
  fi
}

dashboard_nodeport() {
  printf '%s' "${DASHBOARD_NODEPORT:-30443}"
}

# kubectl port-forward fallback (optional; GitOps dashboard uses NodePort).
dashboard_forward_port() {
  printf '%s' "${DASHBOARD_FORWARD_PORT:-8443}"
}

# Operator-network dashboard URL via NodePort on control-plane mgmt IP.
dashboard_operator_url() {
  local cluster="$1"
  printf 'https://%s:%s' "$(dashboard_mgmt_ip "$cluster")" "$(dashboard_nodeport)"
}

kubeconfig_file() {
  printf 'config-%s' "$1"
}

kube_context() {
  printf '%s@%s' "$1" "$1"
}

# Gitea deployment repo name (nephio/<name>); workload clusters use {cluster}-repo.
cluster_gitea_repo_name() {
  local cluster="$1"
  case "$cluster" in
    mgmt|mgmt-staging|oai-packages)
      printf '%s' "$cluster"
      ;;
    *)
      printf '%s-repo' "$cluster"
      ;;
  esac
}

gitea_repo_url() {
  local repo_name="$1"
  local host="${GITEA_HOST:-$MGMT_API_IP}"
  local port="${GITEA_PORT:-3000}"
  local org="${GITEA_ORG:-nephio}"
  printf 'http://%s:%s/%s/%s.git' "$host" "$port" "$org" "$repo_name"
}

cluster_site_type() {
  local cluster="$1"
  printf '%s' "${CLUSTER_SITE_TYPE[$cluster]}"
}

cluster_kubeconfig_secret_name() {
  printf '%s-kubeconfig' "$1"
}

local_kubeconfig_path() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "${HOME}/.kube/config"
  else
    printf '%s' "${HOME}/.kube/$(kubeconfig_file "$cluster")"
  fi
}
