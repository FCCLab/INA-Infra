#!/usr/bin/env bash
# Install local SSH public key on testbed nodes (auto-detected from SSH config).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_USER="${SSH_USER:-fcp}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
SSH_PASSWORD="${SSH_PASSWORD:-}"

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

Install your SSH public key on testbed nodes for passwordless login.
Target hosts are automatically detected from the SSH config file:
  ${SSH_CONFIG}

Commands:
  copy     Run ssh-copy-id on each host (default)
  status   Test BatchMode SSH to each host

Examples:
  $(basename "$0") -y
  $(basename "$0") copy mgmt central
  $(basename "$0") status
  $(basename "$0") copy gpu-gh81 gpu-gh82

Clusters: mgmt, central, regional, edge

Environment:
  SSH_CONFIG     SSH config (default: utils/ssh_config/config)
  SSH_USER       Remote user (default: fcp)
  SSH_PUBKEY     Public key file (default: ~/.ssh/id_rsa.pub)
  SSH_PASSWORD   Remote login password (optional; uses sshpass if available)

Requires: ssh, ssh-copy-id
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

# Auto-detect host list dynamically from SSH config file
detect_hosts_from_config() {
  local cfg="$1"
  if [[ ! -f "$cfg" ]]; then
    err "SSH config file not found: $cfg"
    return 1
  fi
  grep -E '^[[:space:]]*Host[[:space:]]+' "$cfg" | grep -vE '(\*|github|ue-)' | awk '{print $2}'
}

hosts_for_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt)
      grep -E '^[[:space:]]*Host[[:space:]]+mgmt' "$SSH_CONFIG" | awk '{print $2}'
      ;;
    central)
      grep -E '^[[:space:]]*Host[[:space:]]+.*(central|gh81)' "$SSH_CONFIG" | awk '{print $2}'
      ;;
    regional)
      grep -E '^[[:space:]]*Host[[:space:]]+.*(regional|gh82)' "$SSH_CONFIG" | awk '{print $2}'
      ;;
    edge)
      grep -E '^[[:space:]]*Host[[:space:]]+.*(edge|a40|usrp)' "$SSH_CONFIG" | awk '{print $2}'
      ;;
    *)
      printf '%s\n' "$cluster"
      ;;
  esac
}

resolve_hosts() {
  local arg hosts=() host
  for arg in "$@"; do
    case "$arg" in
      mgmt|central|regional|edge)
        while IFS= read -r host; do
          [[ -n "$host" ]] && hosts+=("$host")
        done < <(hosts_for_cluster "$arg")
        ;;
      gh81) hosts+=("gpu-gh81") ;;
      gh82) hosts+=("gpu-gh82") ;;
      edge-3) hosts+=("gpu-a40") ;;
      central-0) hosts+=("cpu-central-0") ;;
      central-1) hosts+=("cpu-central-1") ;;
      regional-0) hosts+=("cpu-regional-0") ;;
      regional-1) hosts+=("cpu-regional-1") ;;
      edge-0) hosts+=("cpu-edge-0") ;;
      edge-1) hosts+=("cpu-edge-1") ;;
      *)
        hosts+=("$arg")
        ;;
    esac
  done

  # If no host/cluster arguments provided, auto-detect all hosts from config file
  if [[ ${#hosts[@]} -eq 0 ]]; then
    while IFS= read -r host; do
      [[ -n "$host" ]] && hosts+=("$host")
    done < <(detect_hosts_from_config "$SSH_CONFIG")
  fi

  local seen=() h
  for h in "${hosts[@]}"; do
    [[ " ${seen[*]:-} " == *" $h "* ]] && continue
    seen+=("$h")
  done
  printf '%s\n' "${seen[@]}"
}

default_pubkey() {
  if [[ -n "$SSH_PUBKEY" ]]; then
    printf '%s' "$SSH_PUBKEY"
    return 0
  fi
  if [[ -f "$HOME/.ssh/id_rsa.pub" ]]; then
    printf '%s/.ssh/id_rsa.pub' "$HOME"
    return 0
  fi
  if [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
    printf '%s/.ssh/id_ed25519.pub' "$HOME"
    return 0
  fi
  printf '%s/.ssh/id_rsa.pub' "$HOME"
}

ssh_works() {
  local host="$1"
  ssh_cmd -o BatchMode=yes -o ConnectTimeout=5 "$host" "true" 2>/dev/null
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
    info "key installed successfully"
    return 0
  fi

  err "passwordless SSH still failing on ${host}"
  return 1
}

status_host() {
  local host="$1"

  printf '%-16s ... ' "$host"
  if ssh_works "$host"; then
    printf '\033[32m[OK - Passwordless]\033[0m (%s)\n' "$(ssh_cmd -o BatchMode=yes -o ConnectTimeout=5 "$host" "hostname" 2>/dev/null || echo 'connected')"
    return 0
  else
    printf '\033[31m[FAILED - Password Required / Unreachable]\033[0m\n'
    return 1
  fi
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

  mapfile -t hosts < <(resolve_hosts "${args[@]+"${args[@]}"}")
  pubkey="$(default_pubkey)"

  log "Config file: $SSH_CONFIG"
  log "Action: $cmd"
  log "Public Key: $pubkey"
  log "Auto-detected Targets (${#hosts[@]}): ${hosts[*]}"

  if [[ "$cmd" == "status" ]]; then
    echo
    for host in "${hosts[@]}"; do
      if ! status_host "$host"; then
        failed=1
      fi
    done
    echo
    if (( failed )); then
      info "Run '$0 copy' to install SSH keys to failing nodes."
    fi
    exit 0
  fi

  if [[ "$assume_yes" != "1" && "$cmd" == "copy" ]]; then
    echo
    read -rp "Install SSH public key on these nodes? [Y/n] " ans
    if [[ "${ans,,}" == "n" || "${ans,,}" == "no" ]]; then
      echo "Aborted."
      exit 1
    fi
  fi

  for host in "${hosts[@]}"; do
    if ! copy_id_to_host "$host" "$pubkey"; then
      failed=1
    fi
  done

  echo
  if (( failed )); then
    err "one or more hosts failed"
    exit 1
  fi
  log "All done! Passwordless SSH configured."
}

main "$@"
