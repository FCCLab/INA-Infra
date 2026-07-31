#!/usr/bin/env bash
# Enable and start Kubernetes node services (containerd + kubelet) on testbed hosts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_USER="${SSH_USER:-fcp}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"

K8S_SERVICES="${K8S_SERVICES:-containerd kubelet}"

ALL_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1
  regional-0 regional-1
  edge-0 edge-1

)

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y] [up|status] [host|cluster ...]

Enable and start Kubernetes services on mgmt and workload nodes so clusters
come up after reboot (containerd then kubelet).

  up       Enable + start services (default)
  status   Show is-enabled / is-active per service

With no host arguments, runs on all nodes:
  mgmt-0 mgmt-1 central-{0,1} regional-{0,1} edge-{0,1}

Examples:
  $(basename "$0") -y
  $(basename "$0") up mgmt central
  $(basename "$0") status edge-0 edge-1

Clusters: mgmt, central, regional, edge

Environment:
  SSH_CONFIG     SSH config (default: utils/ssh_config/config)
  SSH_USER       SSH user (default: fcp)
  SUDO_PASSWORD  Sudo password if NOPASSWD is not configured
  K8S_SERVICES   Space-separated units (default: containerd kubelet)
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

remote_sudo() {
  local host="$1" cmd="$2"
  if ssh_cmd -o RequestTTY=no -o ConnectTimeout=10 "$host" "sudo -n true" 2>/dev/null; then
    ssh_cmd -o RequestTTY=no "$host" "sudo -n bash -lc $(printf '%q' "$cmd")"
  elif [[ -n "$SUDO_PASSWORD" ]]; then
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" \
      "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
  else
    read -rsp "sudo password for ${SSH_USER}@${host}: " SUDO_PASSWORD
    echo >&2
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" \
      "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
  fi
}

disable_swap_script() {
  cat <<'REMOTE'
if swapon --show 2>/dev/null | grep -q .; then
  echo "  swap: disabling (required for kubelet)"
  swapoff -a
  if grep -qE '^[^#].*[[:space:]]swap[[:space:]]' /etc/fstab 2>/dev/null; then
    sed -i '/[[:space:]]swap[[:space:]]/ s/^\([^#]\)/#\1/' /etc/fstab
    echo "  swap: commented out in /etc/fstab"
  fi
else
  echo "  swap: already off"
fi
REMOTE
}

remote_enable_script() {
  local services="$1"
  cat <<REMOTE
set -euo pipefail
$(disable_swap_script)
services=(${services})
for svc in "\${services[@]}"; do
  unit="\${svc}.service"
  if ! systemctl list-unit-files "\$unit" 2>/dev/null | awk '{print \$1}' | grep -qx "\$unit"; then
    echo "  \$svc: not installed"
    continue
  fi
  systemctl enable "\$svc"
  if [[ "\$svc" == "kubelet" ]]; then
    systemctl start containerd 2>/dev/null || true
  fi
  systemctl start "\$svc" || true
  printf '  %s: enabled=%s active=%s\\n' "\$svc" \\
    "\$(systemctl is-enabled "\$svc" 2>/dev/null || echo unknown)" \\
    "\$(systemctl is-active "\$svc" 2>/dev/null || echo unknown)"
done
REMOTE
}

remote_status_script() {
  local services="$1"
  cat <<REMOTE
set -euo pipefail
if swapon --show 2>/dev/null | grep -q .; then
  echo "  swap: ON (kubelet will fail until disabled)"
else
  echo "  swap: off"
fi
services=(${services})
for svc in "\${services[@]}"; do
  unit="\${svc}.service"
  if ! systemctl list-unit-files "\$unit" 2>/dev/null | awk '{print \$1}' | grep -qx "\$unit"; then
    echo "  \$svc: not installed"
    continue
  fi
  printf '  %s: enabled=%s active=%s\\n' "\$svc" \\
    "\$(systemctl is-enabled "\$svc" 2>/dev/null || echo unknown)" \\
    "\$(systemctl is-active "\$svc" 2>/dev/null || echo unknown)"
done
if [[ -f /etc/kubernetes/kubelet.conf ]]; then
  echo "  kubeadm: joined node"
elif [[ -f /etc/kubernetes/admin.conf ]]; then
  echo "  kubeadm: control plane"
else
  echo "  kubeadm: not configured"
fi
REMOTE
}

services_shell_array() {
  local -a arr=() s
  for s in $K8S_SERVICES; do
    arr+=("$s")
  done
  printf '%q ' "${arr[@]}"
}

run_on_host() {
  local host="$1" mode="$2" script failed=0

  echo
  echo "========================================"
  echo " ${host}"
  echo "========================================"

  if ! ssh_cmd -o RequestTTY=no -o ConnectTimeout=10 "$host" "true" 2>/dev/null; then
    err "cannot SSH to ${host}"
    return 1
  fi

  case "$mode" in
    up)
      script="$(remote_enable_script "$(services_shell_array)")"
      ;;
    status)
      script="$(remote_status_script "$(services_shell_array)")"
      ;;
    *)
      err "unknown mode: $mode"
      return 1
      ;;
  esac

  if ! remote_sudo "$host" "$script"; then
    err "remote command failed on ${host}"
    return 1
  fi
}

main() {
  local assume_yes=0 cmd=up
  local -a args=() hosts=() host

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes) assume_yes=1; shift ;;
      up|status) cmd="$1"; shift ;;
      -h|--help|help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done

  if [[ ! -f "$SSH_CONFIG" ]]; then
    err "SSH config not found: $SSH_CONFIG"
    exit 1
  fi

  mapfile -t hosts < <(resolve_hosts "${args[@]}")

  log "command: $cmd"
  log "services: $K8S_SERVICES"
  log "hosts (${#hosts[@]}):"
  printf '    %s\n' "${hosts[@]}"

  if [[ "$assume_yes" != "1" && "$cmd" == "up" ]]; then
    read -rp "Enable and start Kubernetes services on these nodes? [y/N] " ans
    if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
      echo "Aborted."
      exit 1
    fi
  fi

  local failed=0
  for host in "${hosts[@]}"; do
    if ! run_on_host "$host" "$cmd"; then
      failed=1
    fi
  done

  echo
  if (( failed )); then
    err "one or more hosts failed"
    exit 1
  fi
  log "done"
}

main "$@"
