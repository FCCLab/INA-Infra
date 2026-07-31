# Minimal helpers for ina-infra backend scripts (no monorepo cluster_lib).
# Sourced by scripts in this directory. Do not source from outside ina-infra.

ALL_CLUSTERS=(central regional edge)

MGMT_API_IP="${MGMT_API_IP:-10.1.132.200}"
declare -A CLUSTER_MGMT_IP=(
  [central]=10.1.132.210
  [regional]=10.1.132.220
  [edge]=10.1.132.230
)
declare -A CLUSTER_OPENSPEEDTEST_VIP=(
  [central]=10.1.137.101
  [regional]=10.1.137.102
  [edge]=10.1.137.103
)
MGMT_OPENSPEEDTEST_VIP="${MGMT_OPENSPEEDTEST_VIP:-10.1.132.11}"

# Default Multus NRF (overridden by profile ina-core-ips / env).
INA_NRF_SBI_IP="${INA_NRF_SBI_IP:-10.1.140.11}"
INA_NRF_LB_IP="${INA_NRF_LB_IP:-${INA_NRF_SBI_IP}}"

ina_nrf_sbi_ip() {
  printf '%s' "${INA_NRF_SBI_IP}"
}

ina_nrf_lb_ip() {
  printf '%s' "${INA_NRF_LB_IP}"
}

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

github_gitops_repo_name() {
  local cluster="$1"
  printf 'INA-Infra-%s' "$(cluster_gitea_repo_name "$cluster")"
}

github_repo_url() {
  local repo_name="$1"
  printf 'https://github.com/FCCLab/%s.git' "$repo_name"
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

openspeedtest_vip() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_OPENSPEEDTEST_VIP"
  else
    printf '%s' "${CLUSTER_OPENSPEEDTEST_VIP[$cluster]}"
  fi
}

# Resolve ina-infra root and GitOps repos dir (REPOS_DIR env wins).
# Scripts set SCRIPT_DIR before sourcing this file.
_ina_scripts_resolve_roots() {
  if [[ -z "${INA_INFRA_ROOT:-}" ]]; then
    INA_INFRA_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  fi
  if [[ -z "${REPOS_DIR:-}" ]]; then
    if [[ -d "${INA_INFRA_ROOT}/../repos" ]]; then
      REPOS_DIR="$(cd "${INA_INFRA_ROOT}/../repos" && pwd)"
    else
      REPOS_DIR="${INA_INFRA_ROOT}/repos"
    fi
  fi
  # Parent monorepo (optional; only for submodule init / legacy env).
  if [[ -z "${REPO_ROOT:-}" ]]; then
    if [[ -d "${INA_INFRA_ROOT}/../.git" || -f "${INA_INFRA_ROOT}/../.gitmodules" ]]; then
      REPO_ROOT="$(cd "${INA_INFRA_ROOT}/.." && pwd)"
    else
      REPO_ROOT="${INA_INFRA_ROOT}"
    fi
  fi
  export INA_INFRA_ROOT REPOS_DIR REPO_ROOT
}

_ina_scripts_resolve_roots
