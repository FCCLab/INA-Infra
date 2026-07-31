#!/usr/bin/env bash
# Install the local SSH public key on Nephio testbed nodes (ssh-copy-id wrapper).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_USER="${SSH_USER:-fcp}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
SSH_PASSWORD="${SSH_PASSWORD:-}"

ALL_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1
  regional-0 regional-1
  edge-0 edge-1

)

cleanup() {
  unset SSH_PASSWORD
}
trap cleanup EXIT

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y] [copy|status] [host|cluster ...]

Install your SSH public key on mgmt and workload nodes for passwordless login.

  copy     Run ssh-copy-id on each host (default)
  status   Test BatchMode SSH to each host

With no host arguments, targets all nodes:
  mgmt-0 mgmt-1 central-{0,1} regional-{0,1} edge-{0,1}

Examples:
  $(basename "$0") -y
  $(basename "$0") copy mgmt central
  $(basename "$0") status edge-0

Clusters: mgmt, central, regional, edge

Environment:
  SSH_CONFIG     SSH config (default: utils/ssh_config/config)
  SSH_USER       Remote user (default: fcp)
  SSH_PUBKEY     Public key file (default: IdentityFile from config, else ~/.ssh/id_rsa.pub)
  SSH_PASSWORD   Remote login password for ssh-copy-id (optional; uses sshpass if set)

Requires: ssh, ssh-copy-id
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

default_pubkey() {
  local key line
  if [[ -n "$SSH_PUBKEY" ]]; then
    printf '%s' "$SSH_PUBKEY"
    return 0
  fi
  if [[ -f "$SSH_CONFIG" ]]; then
    line="$(awk '/^[Hh]ost / { in_default=0 } /^[Hh]ost \*$/ { in_default=1 } in_default && /^[[:space:]]*IdentityFile / { print $2; exit }' "$SSH_CONFIG")"
    if [[ -z "$line" ]]; then
      line="$(awk -v user="$SSH_USER" '
        $1 == "Host" && $2 != "*" { host=$2 }
        $1 == "User" && $2 == user && host != "" { found=host }
        found != "" && $1 == "IdentityFile" { print $2; exit }
      ' "$SSH_CONFIG")"
    fi
    if [[ -n "$line" ]]; then
      key="${line/#\~/$HOME}"
      printf '%s.pub' "${key%.pub}"
      return 0
    fi
  fi
  printf '%s/.ssh/id_rsa.pub' "$HOME"
}

ssh_works() {
  local host="$1"
  ssh_cmd -o BatchMode=yes -o ConnectTimeout=10 "$host" "true" 2>/dev/null
}

copy_id_to_host() {
  local host="$1" pubkey="$2"

  echo
  echo "========================================"
  echo " ${host}"
  echo "========================================"

  if ssh_works "$host"; then
    info "passwordless SSH already works"
    return 0
  fi

  if [[ ! -f "$pubkey" ]]; then
    err "public key not found: $pubkey"
    return 1
  fi

  local -a copy_cmd=(ssh-copy-id -f -o StrictHostKeyChecking=accept-new -F "$SSH_CONFIG" -i "$pubkey" "$host")

  if [[ -n "$SSH_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SSH_PASSWORD" sshpass -e "${copy_cmd[@]}"
  else
    "${copy_cmd[@]}"
  fi

  if ssh_works "$host"; then
    info "key installed"
    return 0
  fi

  err "passwordless SSH still failing on ${host}"
  return 1
}

status_host() {
  local host="$1"

  echo
  echo "========================================"
  echo " ${host}"
  echo "========================================"

  if ssh_works "$host"; then
    info "SSH: ok (BatchMode)"
    ssh_cmd -o BatchMode=yes -o ConnectTimeout=10 "$host" "hostname" 2>/dev/null | sed 's/^/    /'
    return 0
  fi

  err "SSH: failed (need ssh-copy-id?)"
  return 1
}

main() {
  local assume_yes=0 cmd=copy
  local -a args=() hosts=() host
  local pubkey failed=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes) assume_yes=1; shift ;;
      copy|status) cmd="$1"; shift ;;
      -h|--help|help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done

  command -v ssh-copy-id >/dev/null 2>&1 || {
    err "ssh-copy-id not found (install openssh-client)"
    exit 1
  }

  if [[ ! -f "$SSH_CONFIG" ]]; then
    err "SSH config not found: $SSH_CONFIG"
    exit 1
  fi

  mapfile -t hosts < <(resolve_hosts "${args[@]}")
  pubkey="$(default_pubkey)"

  log "command: $cmd"
  log "pubkey: $pubkey"
  log "hosts (${#hosts[@]}):"
  printf '    %s\n' "${hosts[@]}"

  if [[ "$assume_yes" != "1" && "$cmd" == "copy" ]]; then
    read -rp "Install SSH public key on these nodes? [y/N] " ans
    if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
      echo "Aborted."
      exit 1
    fi
  fi

  for host in "${hosts[@]}"; do
    case "$cmd" in
      copy)
        if ! copy_id_to_host "$host" "$pubkey"; then
          failed=1
        fi
        ;;
      status)
        if ! status_host "$host"; then
          failed=1
        fi
        ;;
    esac
  done

  echo
  if (( failed )); then
    err "one or more hosts failed"
    exit 1
  fi
  log "done"
}

main "$@"
