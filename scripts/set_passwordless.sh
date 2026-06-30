#!/usr/bin/env bash
# Configure passwordless sudo for the SSH user on testbed nodes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_USER="${SSH_USER:-fcp}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"

ALL_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1
  regional-0 regional-1
  edge-0 edge-1
  ue-0 ue-1
)

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y] [host|cluster ...]

Write /etc/sudoers.d/${SSH_USER} with NOPASSWD:ALL on each node.
With no arguments, configures all mgmt and workload nodes.

Examples:
  $(basename "$0") -y
  $(basename "$0") mgmt
  $(basename "$0") central-0 regional-0
  SUDO_PASSWORD=secret $(basename "$0") -y mgmt-0

Clusters: mgmt, central, regional, edge, ue

Environment:
  SSH_CONFIG      SSH config (default: utils/ssh_config/config)
  SSH_USER        User granted NOPASSWD (default: fcp)
  SUDO_PASSWORD   Sudo password when NOPASSWD is not yet configured
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
  if ssh_cmd -o RequestTTY=no "$host" "sudo -n true" 2>/dev/null; then
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

set_passwordless_on_host() {
  local host="$1"

  echo
  echo "========================================"
  echo " Passwordless sudo: ${host}"
  echo "========================================"

  if ssh_cmd -o RequestTTY=no "$host" "sudo -n true" 2>/dev/null; then
    echo ">>> [${host}] already passwordless"
    return 0
  fi

  if ! ssh_cmd -o RequestTTY=no "$host" "true" 2>/dev/null; then
    echo "error: cannot SSH to ${host}" >&2
    return 1
  fi

  remote_sudo "$host" \
    "printf '%s\n' '${SSH_USER} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/${SSH_USER} && chmod 440 /etc/sudoers.d/${SSH_USER}"

  if ssh_cmd -o RequestTTY=no "$host" "sudo -n true" 2>/dev/null; then
    echo ">>> [${host}] passwordless sudo enabled"
    return 0
  fi

  echo "error: passwordless sudo verification failed on ${host}" >&2
  return 1
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

echo "Hosts (${#hosts[@]}):"
printf '  %s\n' "${hosts[@]}"

if [[ "$assume_yes" != "1" ]]; then
  read -rp "Configure passwordless sudo on these nodes? [y/N] " ans
  if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

failed=0
for host in "${hosts[@]}"; do
  if ! set_passwordless_on_host "$host"; then
    failed=1
  fi
done

exit "$failed"
