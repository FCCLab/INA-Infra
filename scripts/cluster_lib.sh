# Shared workload-cluster definitions (sourced by bringup/install scripts).
ALL_CLUSTERS=(central regional edge ue)

SITE_IFACE="${SITE_IFACE:-enp7s0}"
MGMT_IFACE="${MGMT_IFACE:-enp1s0}"
# usrp worker (edge cluster) uses a different NIC name for the site L2
USRP_SITE_IFACE="${USRP_SITE_IFACE:-enp4s0f0}"

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
# Legacy: first IP in each .138 slice; workload dashboard uses NodePort, not MetalLB.
declare -A CLUSTER_DASHBOARD_VIP=(
  [central]=10.1.138.100
  [regional]=10.1.138.125
  [edge]=10.1.138.150
  [ue]=10.1.138.175
)
declare -A CLUSTER_OPENSPEEDTEST_VIP=(
  [central]=10.1.138.101
  [regional]=10.1.138.126
  [edge]=10.1.138.151
  [ue]=10.1.138.176
)
# OAI macvlan on 10.1.139.0/24 (Multus / enp7s0). See docs/oai.md; IPs in nephio/docs/ip.md.
OAI_MACVLAN_GW="${OAI_MACVLAN_GW:-10.1.139.1}"
OAI_MACVLAN_PREFIX="${OAI_MACVLAN_PREFIX:-10.1.139}"
declare -A OAI_MACVLAN_BASE=(
  [central]=10
  [regional]=60
  [edge]=110
  [ue]=160
)
# Legacy names: AMF N2 / CU-CP N2 / UPF N3 on macvlan .139 (not MetalLB .138).
declare -A CLUSTER_AMF_N2_VIP=(
  [central]=10.1.139.10
  [regional]=10.1.139.60
  [edge]=10.1.139.110
  [ue]=10.1.139.160
)
declare -A CLUSTER_UPF_N3_VIP=(
  [central]=10.1.139.11
  [regional]=10.1.139.61
  [edge]=10.1.139.111
  [ue]=10.1.139.161
)
# Nephio PackageVariantSet / WorkloadCluster label (nephio.org/site-type)
declare -A CLUSTER_SITE_TYPE=(
  [central]=core
  [regional]=regional
  [edge]=edge
  [ue]=ue
)

MGMT_METALLB_POOL="${MGMT_METALLB_POOL:-10.1.132.10-10.1.132.99}"
CLUSTER_METALLB_POOL="${CLUSTER_METALLB_POOL:-10.1.138.100-10.1.138.199}"
# MetalLB VIPs on enp7s0 (10.1.138.0/24), partitioned per cluster on shared site L2.
CLUSTER_METALLB_SITE_POOL="${CLUSTER_METALLB_SITE_POOL:-10.1.138.100-10.1.138.199}"
declare -A CLUSTER_METALLB_SITE_POOL_SLICE=(
  [central]=10.1.138.100-10.1.138.124
  [regional]=10.1.138.125-10.1.138.149
  [edge]=10.1.138.150-10.1.138.174
  [ue]=10.1.138.175-10.1.138.199
)

MGMT_CP_HOST="${MGMT_CP_HOST:-mgmt-0}"
MGMT_WORKER_HOST="${MGMT_WORKER_HOST:-mgmt-1}"
MGMT_API_IP="${MGMT_API_IP:-10.1.132.200}"
MGMT_WORKER_IP="${MGMT_WORKER_IP:-10.1.132.201}"
MGMT_DASHBOARD_VIP="${MGMT_DASHBOARD_VIP:-10.1.132.10}"
MGMT_OPENSPEEDTEST_VIP="${MGMT_OPENSPEEDTEST_VIP:-10.1.132.11}"

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

# MetalLB VIP pool on site NIC (enp7s0, 10.1.138.0/24); one slice per workload cluster.
metallb_site_pool_for_cluster() {
  local cluster="$1"
  if [[ -n "${METALLB_SITE_POOL:-}" ]]; then
    printf '%s' "$METALLB_SITE_POOL"
    return
  fi
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_METALLB_POOL"
  else
    printf '%s' "${CLUSTER_METALLB_SITE_POOL_SLICE[$cluster]}"
  fi
}

metallb_site_pool_name() {
  if [[ "${1:-}" == "mgmt" ]]; then
    printf 'mgmt-pool'
  else
    printf 'site-pool'
  fi
}

metallb_l2_interface_for_cluster() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "${MGMT_IFACE:-enp1s0}"
  else
    printf '%s' "${SITE_IFACE:-enp7s0}"
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

amf_n2_vip() {
  printf '%s' "${CLUSTER_AMF_N2_VIP[$1]}"
}

upf_n3_vip() {
  printf '%s' "${CLUSTER_UPF_N3_VIP[$1]}"
}

# SMF N4 macvlan on central (static peer for every UPF, all sites).
oai_smf_n4_ip() {
  oai_macvlan_ip central 2
}

# NRF MetalLB VIP on central (cross-cluster SBI; avoid ClusterIP DNS in UPF cfg).
OAI_NRF_LB_IP="${OAI_NRF_LB_IP:-10.1.138.100}"
oai_nrf_lb_ip() {
  printf '%s' "${OAI_NRF_LB_IP}"
}

# OAI macvlan IP: cluster_base + offset on 10.1.139.0/24.
oai_macvlan_ip() {
  local cluster="$1"
  local offset="$2"
  printf '%s.%s' "$OAI_MACVLAN_PREFIX" "$((OAI_MACVLAN_BASE[$cluster] + offset))"
}

# --- oai-slice-deployment: co-located UPF + CU-UP per slice ---
# Placement (pod site): 1→central, 2→regional, 3–5→edge.
# IPs stay on the original pools (shared L2 10.1.139.0/24 — address ≠ host cluster):
#   UPF  .20–.34 (central offsets 10–24): N3/N4/N6 per slice
#   CU-UP .70–.84 (regional offsets 10–24): E1/F1-U/N3 per slice
OAI_SLICE_COUNT="${OAI_SLICE_COUNT:-5}"
OAI_UPF_SLICE_OFFSET0="${OAI_UPF_SLICE_OFFSET0:-10}"   # +10 → .20
OAI_CUUP_SLICE_OFFSET0="${OAI_CUUP_SLICE_OFFSET0:-10}" # +10 → .70
OAI_SLICE_DU_OFFSET="${OAI_SLICE_DU_OFFSET:-3}"        # edge +3 → .113 (F1)
OAI_SLICE_DU_RF_OFFSET="${OAI_SLICE_DU_RF_OFFSET:-4}"  # edge +4 → .114 (rfsim IQ)
OAI_SLICE_UE_OFFSET0="${OAI_SLICE_UE_OFFSET0:-5}"      # edge +5 → .115
# CU-CP on edge (same L2 as DU; low F1 RTT). Uses free edge +0..+2.
OAI_SLICE_CUCP_N2_OFFSET="${OAI_SLICE_CUCP_N2_OFFSET:-0}"   # .110
OAI_SLICE_CUCP_F1C_OFFSET="${OAI_SLICE_CUCP_F1C_OFFSET:-1}" # .111
OAI_SLICE_CUCP_E1_OFFSET="${OAI_SLICE_CUCP_E1_OFFSET:-2}"   # .112
# FlexRIC / xApp on edge macvlan (after UEs .115–.119)
OAI_SLICE_FLEXRIC_OFFSET="${OAI_SLICE_FLEXRIC_OFFSET:-10}" # .120 nearRT-RIC E2
OAI_SLICE_XAPP_E2_OFFSET="${OAI_SLICE_XAPP_E2_OFFSET:-11}" # .121 xApp E42
# Public Swagger on edge mgmt IP (10.1.132.0/24) — reachable from operator LAN.
# MetalLB .138 is not routed from mgmt; use kube-proxy externalIPs like Gitea.
OAI_XAPP_SWAGGER_VIP="${OAI_XAPP_SWAGGER_VIP:-${CLUSTER_MGMT_IP[edge]}}"
OAI_XAPP_API_PORT="${OAI_XAPP_API_PORT:-18080}"

# Mgmt LAN CIDR (SSH / Gitea / OpenSpeedTest). UPF N6 routes this via per-site macvlan GW.
MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"

# Per-site N6 gateway on shared 10.1.139.0/24 (one shim per site CP — avoid ARP clash).
#   central → .1, regional → .2, edge → .3
oai_n6_gw_ip() {
  case "$1" in
    central) printf '%s.1' "$OAI_MACVLAN_PREFIX" ;;
    regional) printf '%s.2' "$OAI_MACVLAN_PREFIX" ;;
    edge) printf '%s.3' "$OAI_MACVLAN_PREFIX" ;;
    *) printf '%s' "${OAI_MACVLAN_GW}" ;;
  esac
}

# Slice → cluster hosting that slice's UPF + CU-UP pods.
oai_slice_site() {
  case "$1" in
    1) printf 'central' ;;
    2) printf 'regional' ;;
    *) printf 'edge' ;;
  esac
}

# Slice index 1..N → UPF N3/N4/N6 (legacy central pool .20+)
upf_slice_n3() { oai_macvlan_ip central $((OAI_UPF_SLICE_OFFSET0 + ($1 - 1) * 3)); }
upf_slice_n4() { oai_macvlan_ip central $((OAI_UPF_SLICE_OFFSET0 + ($1 - 1) * 3 + 1)); }
upf_slice_n6() { oai_macvlan_ip central $((OAI_UPF_SLICE_OFFSET0 + ($1 - 1) * 3 + 2)); }

# Slice index 1..N → CU-UP E1/F1-U/N3 (legacy regional pool .70+)
cuup_slice_e1() { oai_macvlan_ip regional $((OAI_CUUP_SLICE_OFFSET0 + ($1 - 1) * 3)); }
cuup_slice_f1u() { oai_macvlan_ip regional $((OAI_CUUP_SLICE_OFFSET0 + ($1 - 1) * 3 + 1)); }
cuup_slice_n3() { oai_macvlan_ip regional $((OAI_CUUP_SLICE_OFFSET0 + ($1 - 1) * 3 + 2)); }

oai_slice_du_f1() { oai_macvlan_ip edge "$OAI_SLICE_DU_OFFSET"; }
oai_slice_du_rf() { oai_macvlan_ip edge "$OAI_SLICE_DU_RF_OFFSET"; }
oai_slice_ue_rf() { oai_macvlan_ip edge $((OAI_SLICE_UE_OFFSET0 + $1 - 1)); }
oai_slice_cucp_n2() { oai_macvlan_ip edge "$OAI_SLICE_CUCP_N2_OFFSET"; }
oai_slice_cucp_f1c() { oai_macvlan_ip edge "$OAI_SLICE_CUCP_F1C_OFFSET"; }
oai_slice_cucp_e1() { oai_macvlan_ip edge "$OAI_SLICE_CUCP_E1_OFFSET"; }
oai_slice_flexric() { oai_macvlan_ip edge "$OAI_SLICE_FLEXRIC_OFFSET"; }
oai_slice_xapp_e2() { oai_macvlan_ip edge "$OAI_SLICE_XAPP_E2_OFFSET"; }

# NSSAI SD for slice N (hex without 0x): 000001 .. 000005
oai_slice_sd_hex() { printf '%06x' "$1"; }
# IMSI for slice UE N (uses pre-provisioned 001010000000101..105)
oai_slice_imsi() { printf '00101000000010%d' "$1"; }

# Regional/edge CU-CP N2 on site macvlan .139.
cucp_n2_vip() {
  printf '%s' "${CLUSTER_AMF_N2_VIP[$1]}"
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

# netshoot sidecar for OAI pod network troubleshooting (tcpdump, ping, etc.)
OAI_DEBUG_SIDECAR_IMAGE="${OAI_DEBUG_SIDECAR_IMAGE:-docker.io/nicolaka/netshoot}"

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
