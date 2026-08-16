#!/usr/bin/env bash
# Check k9s installation and Kubernetes access on testbed nodes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/bin}"
K9S_VERSION_EXPECT="${K9S_VERSION:-v0.51.0}"
STRICT="${STRICT:-0}"

ALL_HOSTS=(
  mgmt-0 mgmt-1
  cpu-central-0 cpu-central-1
  cpu-regional-0 cpu-regional-1
  cpu-edge-0 cpu-edge-1
)

declare -A HOST_CLUSTER=(
  [mgmt-0]=mgmt
  [mgmt-1]=mgmt
  [cpu-central-0]=central
  [cpu-central-1]=central
  [central-0]=central
  [central-1]=central
  [cpu-regional-0]=regional
  [cpu-regional-1]=regional
  [regional-0]=regional
  [regional-1]=regional
  [cpu-edge-0]=edge
  [cpu-edge-1]=edge
  [edge-0]=edge
  [edge-1]=edge
)

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [--local] [host|cluster ...]

Check k9s binary, version, and kubectl cluster reachability on testbed nodes.

With no arguments, checks all nodes via SSH. Use --local to check only this machine.

Per host:
  - SSH connectivity
  - k9s in PATH or ${INSTALL_DIR}/k9s
  - k9s version (expected ${K9S_VERSION_EXPECT})
  - kubectl + kubeconfig (if present)
  - node Ready count (control-plane hosts with admin.conf)

Examples:
  $(basename "$0")
  $(basename "$0") mgmt central
  $(basename "$0") --local
  $(basename "$0") cpu-central-0 cpu-regional-0
  STRICT=1 $(basename "$0")

Clusters: mgmt, central, regional, edge

Environment:
  SSH_CONFIG         SSH config (default: utils/ssh_config/config)
  INSTALL_DIR        k9s install path on remotes (default: ~/.local/bin)
  K9S_VERSION        Expected version tag (default: v0.51.0)
  STRICT             Set to 1 to exit 1 if k9s missing or wrong version
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

hosts_for_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt) printf '%s\n' mgmt-0 mgmt-1 ;;
    *)
      printf '%s\n' "${CLUSTER_CP_HOST[$cluster]}"
      printf '%s\n' "${CLUSTER_WORKER_HOST[$cluster]}"
      ;;
  esac
}

resolve_hosts() {
  local arg hosts=() host
  for arg in "$@"; do
    case "$arg" in
      mgmt|central|regional|edge)
        while IFS= read -r host; do
          hosts+=("$host")
        done < <(hosts_for_cluster "$arg")
        ;;
      *)
        hosts+=("$arg")
        ;;
    esac
  done

  if [[ ${#hosts[@]} -eq 0 ]]; then
    hosts=("${ALL_HOSTS[@]}")
  fi

  local seen=() h
  for h in "${hosts[@]}"; do
    [[ " ${seen[*]:-} " == *" $h "* ]] && continue
    seen+=("$h")
  done
  printf '%s\n' "${seen[@]}"
}

k9s_version_local() {
  local bin="$1"
  "$bin" version -s 2>/dev/null | awk '/^Version/ { print $2; exit }'
}

find_k9s_local() {
  local p
  if command -v k9s >/dev/null 2>&1; then
    command -v k9s
    return 0
  fi
  if [[ -x "${INSTALL_DIR}/k9s" ]]; then
    printf '%s/k9s' "$INSTALL_DIR"
    return 0
  fi
  return 1
}

kubectl_summary_local() {
  local cfg="$1" ctx="${2:-}"
  local -a kcmd=(kubectl --kubeconfig="$cfg")
  [[ -n "$ctx" ]] && kcmd+=(--context="$ctx")
  if ! "${kcmd[@]}" cluster-info >/dev/null 2>&1; then
    printf 'unreachable'
    return 0
  fi
  local ready total
  ready="$("${kcmd[@]}" get nodes --no-headers 2>/dev/null | awk '$2 ~ /Ready/ { c++ } END { print c+0 }')"
  total="$("${kcmd[@]}" get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  printf '%s/%s Ready' "${ready:-0}" "${total:-0}"
}

check_local() {
  local bin version cluster ctx cfg summary

  echo
  echo "========================================"
  echo " local ($(hostname -s 2>/dev/null || hostname))"
  echo "========================================"

  if ! bin="$(find_k9s_local)"; then
    err "k9s: not installed"
    return 1
  fi
  version="$(k9s_version_local "$bin")"
  info "k9s: $bin"
  info "version: ${version:-unknown}"
  if [[ -n "$version" && "$version" != "$K9S_VERSION_EXPECT" ]]; then
    info "expected: $K9S_VERSION_EXPECT"
  fi

  for cluster in mgmt "${ALL_CLUSTERS[@]}"; do
    cfg="$(local_kubeconfig_path "$cluster")"
    [[ -f "$cfg" ]] || continue
    ctx="$(kube_context "$cluster")"
    summary="$(kubectl_summary_local "$cfg" "$ctx")"
    info "kubeconfig ${cluster}: $cfg ($summary)"
  done

  if [[ -z "$version" ]]; then
    return 1
  fi
  if [[ "$STRICT" == "1" && "$version" != "$K9S_VERSION_EXPECT" ]]; then
    return 1
  fi
  return 0
}

remote_check_script() {
  local install_dir="$1" expected="$2"
  cat <<REMOTE
set -euo pipefail
install_dir="${install_dir}"
expected="${expected}"
k9s_bin=""
if command -v k9s >/dev/null 2>&1; then
  k9s_bin="\$(command -v k9s)"
elif [[ -x "\${install_dir}/k9s" ]]; then
  k9s_bin="\${install_dir}/k9s"
fi
if [[ -z "\$k9s_bin" ]]; then
  echo "k9s: missing"
else
  ver="\$("\$k9s_bin" version -s 2>/dev/null | awk '/^Version/ { print \$2; exit }')"
  echo "k9s: \$k9s_bin"
  echo "version: \${ver:-unknown}"
  if [[ -n "\$ver" && "\$ver" != "\$expected" ]]; then
    echo "expected: \$expected"
  fi
fi
if command -v kubectl >/dev/null 2>&1; then
  echo "kubectl: yes"
else
  echo "kubectl: no"
fi
kube_cfg=""
if [[ -f "\${HOME}/.kube/config" ]]; then
  kube_cfg="\${HOME}/.kube/config"
elif [[ -f /etc/kubernetes/admin.conf ]]; then
  kube_cfg="/etc/kubernetes/admin.conf"
fi
if [[ -n "\$kube_cfg" ]]; then
  echo "kubeconfig: \$kube_cfg"
  if [[ "\$kube_cfg" == /etc/kubernetes/* ]]; then
    nodes="\$(sudo kubectl --kubeconfig="\$kube_cfg" get nodes --no-headers 2>/dev/null | awk '\$2 ~ /Ready/ { c++ } END { print c+0 }')"
    total="\$(sudo kubectl --kubeconfig="\$kube_cfg" get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  else
    nodes="\$(kubectl --kubeconfig="\$kube_cfg" get nodes --no-headers 2>/dev/null | awk '\$2 ~ /Ready/ { c++ } END { print c+0 }')"
    total="\$(kubectl --kubeconfig="\$kube_cfg" get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  fi
  echo "nodes: \${nodes:-0}/\${total:-0} Ready"
else
  echo "kubeconfig: none"
fi
REMOTE
}

check_remote_host() {
  local host="$1" line failed=0 has_k9s=0 version=""

  echo
  echo "========================================"
  echo " ${host}"
  echo "========================================"

  if ! ssh_cmd -o BatchMode=yes -o ConnectTimeout=10 "$host" "true" 2>/dev/null; then
    err "SSH: failed"
    return 1
  fi
  info "SSH: ok"

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    case "$line" in
      k9s:\ missing)
        err "$line"
        failed=1
        ;;
      k9s:\ /*|k9s:\ *)
        info "$line"
        has_k9s=1
        ;;
      version:*)
        version="${line#version: }"
        info "$line"
        if [[ "$STRICT" == "1" && "$version" != "$K9S_VERSION_EXPECT" ]]; then
          failed=1
        fi
        ;;
      *)
        info "$line"
        ;;
    esac
  done < <(ssh_cmd -o RequestTTY=no -o ConnectTimeout=10 "$host" "bash -s" < <(remote_check_script "$INSTALL_DIR" "$K9S_VERSION_EXPECT"))

  if (( ! has_k9s )); then
    failed=1
  fi

  if [[ -n "${HOST_CLUSTER[$host]:-}" ]]; then
    info "suggested: k9s --context $(kube_context "${HOST_CLUSTER[$host]}")"
  fi

  return "$failed"
}

main() {
  local local_only=0 failed=0
  local -a args=() hosts=() host

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --local) local_only=1; shift ;;
      -h|--help|help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done

  if [[ "$local_only" == "1" ]]; then
    check_local || failed=1
    echo
    if (( failed )); then
      err "local check failed (install: scripts/install_k9s.sh)"
      exit 1
    fi
    log "local check passed"
    exit 0
  fi

  if [[ ${#args[@]} -gt 0 ]]; then
    mapfile -t hosts < <(resolve_hosts "${args[@]}")
  else
    mapfile -t hosts < <(resolve_hosts)
  fi

  if [[ ! -f "$SSH_CONFIG" ]]; then
    err "SSH config not found: $SSH_CONFIG"
    exit 1
  fi

  log "hosts (${#hosts[@]}):"
  printf '    %s\n' "${hosts[@]}"

  for host in "${hosts[@]}"; do
    if ! check_remote_host "$host"; then
      failed=1
    fi
  done

  echo
  if (( failed )); then
    err "one or more checks failed (install: scripts/install_k9s.sh)"
    exit 1
  fi
  log "all checks passed"
}

main "$@"
