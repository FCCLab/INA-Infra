#!/usr/bin/env bash
# Discover Kubernetes nodes in each cluster and print host clock / NTP status.
#
# Connects with kubectl (combined kubeconfigs), lists Ready nodes, maps them to
# SSH aliases in utils/ssh_config/config, then runs timedatectl + date remotely.
#
#   ./utils/time/check_node_time.sh
#   ./utils/time/check_node_time.sh central edge
#   ./utils/time/check_node_time.sh -c central@central
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}}"
SSH_OPTS=(-F "$SSH_CFG" -n -o BatchMode=yes -o ConnectTimeout=10 -o RequestTTY=no)
DEFAULT_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
EXPLICIT_CTX=""
cluster_args=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Discover nodes via kubectl and print timezone, NTP, clock, and skew vs this host.

Default clusters: ${DEFAULT_CLUSTERS[*]}

Options:
  -c CONTEXT     Single kubectl context (skips cluster discovery list)
  -h, --help     Show this help

Environment:
  KUBECONFIG     Combined kubeconfigs if unset
  SSH_CFG        SSH config (default: utils/ssh_config/config)
  K8S_CONTEXTS   Override contexts when no cluster args (space-separated)
EOF
}

ensure_kubeconfig() {
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
  fi
}

ssh_alias_for_node() {
  local n="$1"
  n="${n%%.*}"
  case "$n" in
    node-0) printf 'mgmt-0' ;;
    node-1) printf 'mgmt-1' ;;
    *) canonicalize_node_host "$n" ;;
  esac
}

context_for_cluster() {
  kube_context "$1"
}

print_header() {
  printf '%-10s %-16s %-16s %-10s %-16s %-6s %-8s %8s  %s\n' \
    CLUSTER NODE SSH_HOST READY TZ NTP SYNC SKEW_S DATETIME
  printf '%s\n' "$(printf '=%.0s' {1..128})"
}

check_node() {
  local cluster="$1" name="$2" ready="$3"
  local alias tz ntp sync epoch rfc now skew out
  alias="$(ssh_alias_for_node "$name")"
  if ! out="$(
    ssh "${SSH_OPTS[@]}" "$alias" 'set -euo pipefail
tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo ?)
ntp=$(timedatectl show -p NTP --value 2>/dev/null || echo ?)
sync=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo ?)
epoch=$(date +%s)
rfc=$(date -R)
printf "%s|%s|%s|%s|%s\n" "$tz" "$ntp" "$sync" "$epoch" "$rfc"
'
  )"; then
    printf '%-10s %-16s %-16s %-10s SSH_FAIL\n' "$cluster" "$name" "$alias" "$ready"
    return 0
  fi
  IFS='|' read -r tz ntp sync epoch rfc <<<"$out"
  now="$(date +%s)"
  skew=$((epoch - now))
  printf '%-10s %-16s %-16s %-10s %-16s %-6s %-8s %+7d  %s\n' \
    "$cluster" "$name" "$alias" "$ready" "$tz" "$ntp" "$sync" "$skew" "$rfc"
}

check_context() {
  local cluster="$1" ctx="$2"
  if ! kubectl --context "$ctx" get nodes --no-headers >/dev/null 2>&1; then
    printf '%-10s ERROR: cannot connect to context %s\n' "$cluster" "$ctx"
    return 0
  fi
  local name ready
  while read -r name ready; do
    [[ -z "$name" ]] && continue
    check_node "$cluster" "$name" "$ready"
  done < <(kubectl --context "$ctx" get nodes --no-headers | awk '{print $1, $2}')
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      -c)
        EXPLICIT_CTX="${2:?-c requires a context}"
        shift 2
        ;;
      --)
        shift
        cluster_args+=("$@")
        break
        ;;
      -*)
        echo "error: unknown option $1" >&2
        usage >&2
        exit 2
        ;;
      *)
        cluster_args+=("$1")
        shift
        ;;
    esac
  done
}

main() {
  local ctx cluster
  parse_args "$@"
  ensure_kubeconfig

  printf 'LOCAL  %-14s  epoch=%s  %s\n\n' "$(hostname -s)" "$(date +%s)" "$(date -R)"
  print_header

  if [[ -n "$EXPLICIT_CTX" ]]; then
    cluster="${EXPLICIT_CTX%%@*}"
    check_context "$cluster" "$EXPLICIT_CTX"
    return 0
  fi

  if [[ ${#cluster_args[@]} -eq 0 ]]; then
    if [[ -n "${K8S_CONTEXTS:-}" ]]; then
      # shellcheck disable=SC2206
      local contexts=(${K8S_CONTEXTS})
      for ctx in "${contexts[@]}"; do
        check_context "${ctx%%@*}" "$ctx"
      done
      return 0
    fi
    cluster_args=("${DEFAULT_CLUSTERS[@]}")
  fi

  for cluster in "${cluster_args[@]}"; do
    check_context "$cluster" "$(context_for_cluster "$cluster")"
  done
}

main "$@"
