#!/usr/bin/env bash
# Reset Kubernetes on testbed nodes (kubeadm reset + CNI/kubeconfig cleanup).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
RESET_LOCAL_KUBECONFIG="${RESET_LOCAL_KUBECONFIG:-1}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"
SSH_USER="${SSH_USER:-fcp}"

# Workers before control planes; mgmt worker before mgmt CP.
ALL_K8S_HOSTS=(
  central-1 regional-1 edge-1 ue-1
  central-0 regional-0 edge-0 ue-0
  mgmt-1 mgmt-0
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y] [host|cluster ...]

Run kubeadm reset and remove local Kubernetes state on testbed nodes.
With no host/cluster arguments, resets all workload and mgmt nodes.

Workers are reset before control planes. Optionally removes local kubeconfigs
under ~/.kube when RESET_LOCAL_KUBECONFIG=1 (default).

Examples:
  $(basename "$0") -y                  # all nodes, no prompt
  $(basename "$0") -y central          # central-1 then central-0
  $(basename "$0") -y central-0        # single host

Clusters: mgmt, central, regional, edge, ue

Environment:
  SSH_CONFIG              SSH config (default: utils/ssh_config/config)
  RESET_LOCAL_KUBECONFIG  Remove ~/.kube/config* after reset (default: 1)
  SUDO_PASSWORD           Sudo password for hosts without NOPASSWD (prompted if needed)
EOF
}

remote_sudo() {
  local host="$1" cmd="$2"
  if ssh_cmd -o RequestTTY=no "$host" "sudo -n true" 2>/dev/null; then
    ssh_cmd -o RequestTTY=no "$host" "sudo -n bash -lc $(printf '%q' "$cmd")"
  elif [[ -n "$SUDO_PASSWORD" ]]; then
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" \
      "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
  else
    echo "sudo password for ${SSH_USER}@${host}:" >&2
    read -rsp "Password: " SUDO_PASSWORD
    echo >&2
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" \
      "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
  fi
}

prompt_sudo_if_needed() {
  local host="$1"
  if ssh_cmd -o RequestTTY=no "$host" "sudo -n true" 2>/dev/null; then
    return 0
  fi
  if [[ -n "$SUDO_PASSWORD" ]]; then
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" "sudo -S -v" 2>/dev/null && return 0
  fi
  read -rsp "sudo password for ${SSH_USER}@${host}: " SUDO_PASSWORD
  echo
  printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd -o RequestTTY=no "$host" "sudo -S -v" 2>/dev/null || {
    echo "error: invalid sudo password on ${host}" >&2
    SUDO_PASSWORD=""
    return 1
  }
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

hosts_for_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt) printf '%s\n' mgmt-1 mgmt-0 ;;
    *)
      printf '%s\n' "${CLUSTER_WORKER_HOST[$cluster]}"
      printf '%s\n' "${CLUSTER_CP_HOST[$cluster]}"
      ;;
  esac
}

resolve_hosts() {
  local arg hosts=() host
  for arg in "$@"; do
    case "$arg" in
      mgmt|central|regional|edge|ue)
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
    hosts=("${ALL_K8S_HOSTS[@]}")
  fi

  # De-dupe while preserving order.
  local seen=() h
  for h in "${hosts[@]}"; do
    [[ " ${seen[*]:-} " == *" $h "* ]] && continue
    seen+=("$h")
  done
  printf '%s\n' "${seen[@]}"
}

reset_host() {
  local host="$1"
  echo
  echo "========================================"
  echo " Reset: ${host}"
  echo "========================================"
  prompt_sudo_if_needed "$host" || return 1

  local reset_body
  read -r -d '' reset_body <<'REMOTE' || true
set -euo pipefail

if command -v kubeadm >/dev/null 2>&1 && [[ -f /etc/kubernetes/kubelet.conf || -f /etc/kubernetes/admin.conf ]]; then
  echo "==> kubeadm reset"
  kubeadm reset -f
else
  echo "==> kubeadm not initialized (or already reset); cleaning leftovers"
fi

echo "==> remove CNI and kubelet state"
rm -rf /etc/cni/net.d /var/lib/cni /var/lib/kubelet/* /etc/kubernetes
rm -rf /root/.kube/config
rm -rf /home/*/.kube/config 2>/dev/null || true

echo "==> flush iptables (ignore errors)"
for cmd in \
  "iptables -F" "iptables -t nat -F" "iptables -t mangle -F" "iptables -X" \
  "ip6tables -F" "ip6tables -t nat -F" "ip6tables -t mangle -F" "ip6tables -X"; do
  $cmd 2>/dev/null || true
done

systemctl restart kubelet 2>/dev/null || true
systemctl restart containerd 2>/dev/null || true

echo "==> done on $(hostname)"
REMOTE

  remote_sudo "$host" "$reset_body"
}

clean_local_kubeconfigs() {
  local cluster kcfg
  echo
  echo "==> remove local kubeconfigs"
  if [[ -f "${HOME}/.kube/config" ]]; then
    mv "${HOME}/.kube/config" "${HOME}/.kube/config.bak.reset.$(date +%Y%m%d%H%M%S)"
    echo "    backed up ~/.kube/config"
  fi
  for cluster in mgmt "${ALL_CLUSTERS[@]}"; do
    if [[ "$cluster" == "mgmt" ]]; then
      continue
    fi
    kcfg="${HOME}/.kube/$(kubeconfig_file "$cluster")"
    if [[ -f "$kcfg" ]]; then
      rm -f "$kcfg"
      echo "    removed ${kcfg}"
    fi
  done
}

assume_yes=0
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) args+=("$1"); shift ;;
  esac
done

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

mapfile -t hosts < <(resolve_hosts "${args[@]}")

echo "Hosts to reset (${#hosts[@]}):"
printf '  %s\n' "${hosts[@]}"

if [[ "$assume_yes" != "1" ]]; then
  read -rp "Proceed with kubeadm reset on these nodes? [y/N] " ans
  if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

failed=0
for host in "${hosts[@]}"; do
  if ! reset_host "$host"; then
    echo "error: reset failed on ${host}" >&2
    failed=1
  fi
done

if [[ "$RESET_LOCAL_KUBECONFIG" == "1" && ${#args[@]} -eq 0 ]]; then
  clean_local_kubeconfigs
fi

echo
if [[ "$failed" -eq 0 ]]; then
  echo "All resets completed. Re-bootstrap with:"
  echo "  ./scripts/setup_ip.sh"
  echo "  ./scripts/bringup_cluster.sh"
else
  echo "Some resets failed." >&2
fi

exit "$failed"
