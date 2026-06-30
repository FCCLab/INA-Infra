#!/usr/bin/env bash
# Expose workload-cluster Kubernetes Dashboard on the mgmt (10.1.132.x) NIC via kubectl port-forward.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NAMESPACE="${DASHBOARD_NAMESPACE:-kubernetes-dashboard}"
SERVICE="${DASHBOARD_SERVICE:-kubernetes-dashboard-kong-proxy}"
LOCAL_PORT="${DASHBOARD_FORWARD_PORT:-8443}"
BIND_ADDRESS="${DASHBOARD_FORWARD_BIND:-}"

ALL_FORWARD_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
STARTED_CLUSTERS=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster|all ...]

Run kubectl port-forward for the Kubernetes Dashboard, listening on the cluster
control plane's mgmt address (10.1.132.x) so browsers on the operator network
can reach workload dashboards without routing to 10.1.137.0/24.

With no arguments, forwards all clusters in the background (default: all).

  mgmt      https://${MGMT_API_IP}:${LOCAL_PORT}  (MetalLB VIP ${MGMT_DASHBOARD_VIP} also works)
  central   https://${CLUSTER_MGMT_IP[central]}:${LOCAL_PORT}
  regional  https://${CLUSTER_MGMT_IP[regional]}:${LOCAL_PORT}
  edge      https://${CLUSTER_MGMT_IP[edge]}:${LOCAL_PORT}
  ue        https://${CLUSTER_MGMT_IP[ue]}:${LOCAL_PORT}

Options:
  -p, --port PORT     Local port on each mgmt IP (default: ${LOCAL_PORT})
  -b, --bind ADDR     Bind address (default: cluster mgmt IP; only for a single cluster)
  -h, --help          Show this help

Environment:
  SSH_CONFIG              SSH config (default: utils/ssh_config/config)
  DASHBOARD_FORWARD_PORT  Same as -p (default: 8443)
  DASHBOARD_FORWARD_BIND  Same as -b
  DASHBOARD_NAMESPACE     Dashboard namespace (default: kubernetes-dashboard)
  DASHBOARD_SERVICE       Service to forward (default: kubernetes-dashboard-kong-proxy)

Examples:
  $(basename "$0")                 # all clusters (background)
  $(basename "$0") central         # one cluster (foreground)
  $(basename "$0") central regional
  $(basename "$0") -p 9443 edge

Login token:
  ${SCRIPT_DIR}/get_dashboard_key.sh
EOF
}

stop_forward() {
  local cluster="$1"
  local host
  host="$(cluster_cp_host "$cluster")"
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=5 "$host" \
    "pkill -f 'port-forward.*${SERVICE}'" 2>/dev/null || true
}

cleanup_forwards() {
  local cluster
  for cluster in "${STARTED_CLUSTERS[@]}"; do
    stop_forward "$cluster"
  done
}

cluster_mgmt_bind() {
  local cluster="$1"
  if [[ -n "$BIND_ADDRESS" ]]; then
    printf '%s' "$BIND_ADDRESS"
    return
  fi
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "$MGMT_API_IP"
  else
    printf '%s' "${CLUSTER_MGMT_IP[$cluster]}"
  fi
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    all) return 0 ;;
    mgmt) return 0 ;;
    *)
      if [[ -n "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
        return 0
      fi
      ;;
  esac
  echo "error: unknown cluster '${cluster}' (expected all, mgmt, central, regional, edge, or ue)" >&2
  return 1
}

print_forward_info() {
  local cluster="$1"
  local host bind port

  host="$(cluster_cp_host "$cluster")"
  bind="$(cluster_mgmt_bind "$cluster")"
  port="$LOCAL_PORT"

  if [[ "$cluster" == "mgmt" ]]; then
    echo "note: [mgmt] MetalLB VIP https://${MGMT_DASHBOARD_VIP} is on 132 already; forward is optional"
  fi

  echo "==> [${cluster}] ${host}: https://${bind}:${port}"
  echo "    Site VIP (137): https://$(dashboard_vip "$cluster")"
}

remote_forward_cmd() {
  local bind="$1" port="$2"
  printf 'kubectl -n %q port-forward --address=%q svc/%q %q:443' \
    "$NAMESPACE" "$bind" "$SERVICE" "$port"
}

start_forward_background() {
  local cluster="$1"
  local host bind port cmd log attempt

  host="$(cluster_cp_host "$cluster")"
  bind="$(cluster_mgmt_bind "$cluster")"
  port="$LOCAL_PORT"
  cmd="$(remote_forward_cmd "$bind" "$port")"
  log="/tmp/dashboard-forward-${cluster}.log"

  stop_forward "$cluster"
  : >"$log"
  # ssh -f exits after starting the remote command; do not track its PID.
  ssh -f -F "$SSH_CONFIG" -o ExitOnForwardFailure=yes "$host" "$cmd" >>"$log" 2>&1

  for attempt in $(seq 1 15); do
    if grep -q 'Forwarding from' "$log" 2>/dev/null; then
      STARTED_CLUSTERS+=("$cluster")
      echo "    log: ${log}"
      return 0
    fi
    if grep -Eiq 'error|unable|not found|failed|refused' "$log" 2>/dev/null; then
      echo "error: [${cluster}] forward failed; see ${log}" >&2
      tail -5 "$log" >&2 || true
      return 1
    fi
    sleep 0.2
  done

  echo "error: [${cluster}] forward timed out; see ${log}" >&2
  tail -5 "$log" >&2 || true
  return 1
}

forward_dashboard_foreground() {
  local cluster="$1"
  local host bind port cmd

  host="$(cluster_cp_host "$cluster")"
  bind="$(cluster_mgmt_bind "$cluster")"
  port="$LOCAL_PORT"
  cmd="$(remote_forward_cmd "$bind" "$port")"

  print_forward_info "$cluster"
  echo "    Token: ${SCRIPT_DIR}/get_dashboard_key.sh ${cluster}"
  echo "    Ctrl+C to stop"
  echo

  exec ssh -F "$SSH_CONFIG" -t "$host" "$cmd"
}

forward_dashboard_all() {
  local cluster failed=0

  trap 'cleanup_forwards; exit 0' INT TERM

  echo "==> starting dashboard port-forward on mgmt (.132) for: $*"
  echo "    Token: ${SCRIPT_DIR}/get_dashboard_key.sh"
  echo

  for cluster in "$@"; do
    print_forward_info "$cluster"
    if ! start_forward_background "$cluster"; then
      failed=1
    fi
    echo
  done

  if [[ "$failed" -ne 0 ]]; then
    cleanup_forwards
    exit 1
  fi

  echo "All forwards running. Ctrl+C to stop."
  while true; do sleep 3600; done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)
      LOCAL_PORT="$2"
      shift 2
      ;;
    -b|--bind)
      BIND_ADDRESS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_FORWARD_CLUSTERS[@]}")
elif [[ $# -eq 1 && $1 == all ]]; then
  clusters=("${ALL_FORWARD_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    if [[ "$cluster" == all ]]; then
      clusters=("${ALL_FORWARD_CLUSTERS[@]}")
      break
    fi
    clusters+=("$cluster")
  done
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

if [[ -n "$BIND_ADDRESS" && ${#clusters[@]} -gt 1 ]]; then
  echo "error: --bind applies to one cluster only" >&2
  exit 1
fi

if [[ ${#clusters[@]} -eq 1 ]]; then
  forward_dashboard_foreground "${clusters[0]}"
else
  forward_dashboard_all "${clusters[@]}"
fi
