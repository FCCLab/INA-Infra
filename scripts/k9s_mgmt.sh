#!/usr/bin/env bash
# Run k9s on the mgmt/operator host against all clusters.
# Workload APIs live on 10.1.137.0/24 (not routed from .132); this script SSH-tunnels
# each control-plane :6443 to a local port and rewrites kubeconfig servers accordingly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
STATE_DIR="${K9S_MGMT_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/k9s-mgmt}"
MERGED_KUBECONFIG="${K9S_MGMT_KUBECONFIG:-$STATE_DIR/kubeconfig}"
BASE_KUBECONFIG="${K9S_MGMT_BASE_KUBECONFIG:-${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge}"

# Local listen ports for SSH -L (mgmt uses native kubeconfig, no tunnel).
declare -A TUNNEL_PORT=(
  [central]=16443
  [regional]=16444
  [edge]=16445
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [command]

Multi-context k9s from mgmt when workload APIs on 10.1.137.x are unreachable.

Commands:
  start     Start SSH API tunnels + write merged kubeconfig (default before run)
  stop      Stop tunnels
  status    Show tunnel / API health
  run       start (if needed) then exec k9s (default)
  kubeconfig  Print path to merged kubeconfig

Examples:
  $(basename "$0")              # start tunnels + k9s (switch context with :ctx)
  $(basename "$0") start
  $(basename "$0") status
  KUBECONFIG=\$($(basename "$0") kubeconfig) kubectl get nodes --context central@central

In k9s: press ':' then 'ctx' to switch mgmt@mgmt / central@central / ...

Environment:
  SSH_CONFIG
  K9S_MGMT_STATE_DIR      default: \$XDG_RUNTIME_DIR/k9s-mgmt or /tmp/k9s-mgmt
  K9S_MGMT_BASE_KUBECONFIG  colon-separated kubeconfigs to merge
EOF
}

tunnel_pid_file() { printf '%s/tunnel-%s.pid' "$STATE_DIR" "$1"; }
tunnel_log_file() { printf '%s/tunnel-%s.log' "$STATE_DIR" "$1"; }

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR" 2>/dev/null || true
}

stop_tunnel() {
  local cluster="$1" pid_file pid
  pid_file="$(tunnel_pid_file "$cluster")"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
  # Reap orphans matching this local port.
  local port="${TUNNEL_PORT[$cluster]}"
  pkill -f "ssh .* -L 127.0.0.1:${port}:127.0.0.1:6443 " 2>/dev/null || true
}

stop_all_tunnels() {
  local c
  for c in "${ALL_CLUSTERS[@]}"; do
    stop_tunnel "$c"
  done
  echo "stopped API tunnels"
}

tunnel_running() {
  local cluster="$1" pid_file pid port
  pid_file="$(tunnel_pid_file "$cluster")"
  port="${TUNNEL_PORT[$cluster]}"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  ss -lnt "sport = :${port}" 2>/dev/null | grep -q LISTEN
}

start_tunnel() {
  local cluster="$1"
  local host port pid_file log_file attempt

  host="$(cluster_cp_host "$cluster")"
  port="${TUNNEL_PORT[$cluster]}"
  pid_file="$(tunnel_pid_file "$cluster")"
  log_file="$(tunnel_log_file "$cluster")"

  if tunnel_running "$cluster"; then
    echo "    [${cluster}] tunnel already up :${port} → ${host}:6443"
    return 0
  fi

  stop_tunnel "$cluster"
  : >"$log_file"

  # Forward to loopback on the CP (apiserver listens on *:6443).
  ssh -f -N \
    -F "$SSH_CONFIG" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "127.0.0.1:${port}:127.0.0.1:6443" \
    "$host" >>"$log_file" 2>&1

  for attempt in $(seq 1 20); do
    if ss -lnt "sport = :${port}" 2>/dev/null | grep -q LISTEN; then
      # ssh -f parent may have exited; find the listening ssh.
      pgrep -af "ssh .* -L 127.0.0.1:${port}:127.0.0.1:6443 " \
        | awk 'NR==1 { print $1 }' >"$pid_file" || true
      echo "    [${cluster}] tunnel :${port} → ${host}:6443"
      return 0
    fi
    sleep 0.15
  done

  echo "error: [${cluster}] tunnel failed; see ${log_file}" >&2
  tail -5 "$log_file" >&2 || true
  return 1
}

write_merged_kubeconfig() {
  local cluster port tmp
  tmp="$(mktemp)"

  if [[ ! -f "${HOME}/.kube/config" ]]; then
    echo "error: missing ${HOME}/.kube/config (mgmt)" >&2
    return 1
  fi

  KUBECONFIG="$BASE_KUBECONFIG" kubectl config view --flatten >"$tmp"

  # Point workload clusters at local tunnels; skip TLS name check (cert SAN is .137).
  for cluster in "${ALL_CLUSTERS[@]}"; do
    port="${TUNNEL_PORT[$cluster]}"
    KUBECONFIG="$tmp" kubectl config set-cluster "$cluster" \
      --server="https://127.0.0.1:${port}" \
      --insecure-skip-tls-verify=true >/dev/null
  done

  mv "$tmp" "$MERGED_KUBECONFIG"
  chmod 600 "$MERGED_KUBECONFIG"
  echo "    kubeconfig: ${MERGED_KUBECONFIG}"
}

probe_context() {
  local ctx="$1"
  if KUBECONFIG="$MERGED_KUBECONFIG" kubectl --context "$ctx" --request-timeout=5s \
      get --raw=/readyz >/dev/null 2>&1; then
    printf 'ok'
  else
    printf 'FAIL'
  fi
}

cmd_start() {
  local c failed=0
  ensure_state_dir
  if [[ ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    return 1
  fi
  echo "==> starting workload API tunnels (mgmt uses native .132 API)"
  for c in "${ALL_CLUSTERS[@]}"; do
    start_tunnel "$c" || failed=1
  done
  write_merged_kubeconfig || failed=1
  if [[ "$failed" -ne 0 ]]; then
    return 1
  fi
  echo "==> contexts: mgmt@mgmt + central/regional/edge via 127.0.0.1:16443-16445"
}

cmd_status() {
  local c port host ctx
  ensure_state_dir
  if [[ ! -f "$MERGED_KUBECONFIG" ]]; then
    echo "kubeconfig: missing (run: $(basename "$0") start)"
  else
    echo "kubeconfig: $MERGED_KUBECONFIG"
  fi
  echo
  printf '%-10s %-8s %-22s %s\n' CLUSTER TUNNEL TARGET API
  ctx="mgmt@mgmt"
  if [[ -f "$MERGED_KUBECONFIG" ]]; then
    printf '%-10s %-8s %-22s %s\n' mgmt n/a "10.1.132.200:6443" "$(probe_context "$ctx")"
  else
    printf '%-10s %-8s %-22s %s\n' mgmt n/a "10.1.132.200:6443" "?"
  fi
  for c in "${ALL_CLUSTERS[@]}"; do
    port="${TUNNEL_PORT[$c]}"
    host="$(cluster_cp_host "$c")"
    ctx="$(kube_context "$c")"
    if tunnel_running "$c"; then
      printf '%-10s %-8s %-22s %s\n' "$c" ":${port}" "${host}:6443" "$(probe_context "$ctx")"
    else
      printf '%-10s %-8s %-22s %s\n' "$c" down "${host}:6443" "-"
    fi
  done
}

cmd_run() {
  cmd_start
  echo
  echo "==> k9s (':' then 'ctx' to switch cluster)"
  export KUBECONFIG="$MERGED_KUBECONFIG"
  exec k9s "$@"
}

main() {
  local cmd="${1:-run}"
  case "$cmd" in
    -h|--help|help) usage; exit 0 ;;
    start) cmd_start ;;
    stop) stop_all_tunnels ;;
    status) cmd_status ;;
    kubeconfig)
      ensure_state_dir
      [[ -f "$MERGED_KUBECONFIG" ]] || cmd_start >/dev/null
      printf '%s\n' "$MERGED_KUBECONFIG"
      ;;
    run)
      shift || true
      cmd_run "$@"
      ;;
    *)
      # Allow: k9s_mgmt.sh --context central@central
      cmd_run "$@"
      ;;
  esac
}

main "$@"
