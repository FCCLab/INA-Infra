#!/usr/bin/env bash
# Bootstrap external workload nodes: SSH key, hostname, passwordless sudo, netplan (mgmt + k8s IPs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
NETPLAN_DIR="${NETPLAN_DIR:-$SCRIPT_DIR/netplan}"
NETPLAN_FILE="${NETPLAN_FILE:-55-k8s.yaml}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
IDENTITY_FILE="${IDENTITY_FILE:-}"

declare -a NODE_NAMES=()
declare -A NODE_INIT_HOST=()
declare -A NODE_MGMT_IP=()
declare -A NODE_USER=()
declare -A NODE_PASSWORD=()

cleanup() {
  unset NODE_PASSWORD
}
trap cleanup EXIT

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y] <name> <username> <ip> [<name> <username> <ip> ...]

For each workload node:
  1. Prompt for password (if not set in environment)
  2. Install local SSH public key (passwordless login) via <ip>
  3. Set hostname to <name>
  4. Enable passwordless sudo
  5. Upload netplan (${NETPLAN_FILE}: mgmt 10.1.132.x + k8s 10.1.137.x)
  6. Pin DNS (netplan nameserver or 8.8.8.8) in resolv.conf + systemd-resolved
  7. Add/update utils/ssh_config/config to use mgmt IP from netplan

Examples:
  $(basename "$0") gh81 fcp 10.1.101.211
  $(basename "$0") -y gh81 fcp 10.1.101.211 gh82 fcp 10.1.101.212
  GH81_USER=fcp GH81_PASS=secret $(basename "$0") -y gh81 fcp 10.1.101.211

Environment (optional, skip prompts; prefix = uppercase <name>, '-' → '_'):
  GH81_USER GH81_PASS
  EDGE_2_USER EDGE_2_PASS
  SSH_CONFIG     SSH config file (default: utils/ssh_config/config)
  NETPLAN_DIR    Netplan source tree (default: workloads/netplan/<name>/)
  SSH_PUBKEY     Public key file (default: from SSH config / ~/.ssh/id_rsa.pub)
  IDENTITY_FILE  Private key for SSH (default: from SSH config / ~/.ssh/id_rsa)
EOF
}

default_identity() {
  local line key
  if [[ -n "$IDENTITY_FILE" ]]; then
    printf '%s' "$IDENTITY_FILE"
    return 0
  fi
  if [[ -f "$SSH_CONFIG" ]]; then
    line="$(awk '/^[Hh]ost / { in_default=0 } /^[Hh]ost \*$/ { in_default=1 } in_default && /^[[:space:]]*IdentityFile / { print $2; exit }' "$SSH_CONFIG")"
    if [[ -n "$line" ]]; then
      key="${line/#\~/$HOME}"
      printf '%s' "$key"
      return 0
    fi
  fi
  printf '%s/.ssh/id_rsa' "$HOME"
}

default_pubkey() {
  local key
  if [[ -n "$SSH_PUBKEY" ]]; then
    printf '%s' "$SSH_PUBKEY"
    return 0
  fi
  key="$(default_identity)"
  printf '%s.pub' "${key%.pub}"
}

ssh_opts() {
  local -a opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
  local id
  id="$(default_identity)"
  if [[ -f "$id" ]]; then
    opts+=(-i "$id")
  fi
  printf '%s\n' "${opts[@]}"
}

# ssh-copy-id uses -i for the public key to install; pass private key via -o IdentityFile.
ssh_copy_id_opts() {
  local -a opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
  local id
  id="$(default_identity)"
  if [[ -f "$id" ]]; then
    opts+=(-o "IdentityFile=${id}")
  fi
  printf '%s\n' "${opts[@]}"
}

node_env_prefix() {
  # Bash env vars cannot contain '-'; edge-2 -> EDGE_2
  local name="${1^^}"
  name="${name//-/_}"
  printf '%s' "$name"
}

mgmt_ip_from_netplan() {
  local file="$1"
  grep -E '^[[:space:]]*-[[:space:]]*10\.1\.132\.' "$file" | head -1 | awk '{print $2}' | sed 's#/24##'
}

dns_from_netplan() {
  local file="$1"
  grep -A5 'nameservers:' "$file" | grep -E '^[[:space:]]*-' | head -1 | awk '{print $2}'
}

# Always pin DNS (default 8.8.8.8): static /etc/resolv.conf + systemd-resolved drop-in.
sync_resolv_conf() {
  local user="$1" host="$2" netplan_src="$3"
  local dns
  dns="$(dns_from_netplan "$netplan_src")"
  dns="${dns:-8.8.8.8}"

  run_ssh "$user" "$host" "sudo bash -lc $(printf '%q' "
set -euo pipefail
DNS='${dns}'
rm -f /etc/resolv.conf
printf 'nameserver %s\n' \"\${DNS}\" > /etc/resolv.conf
chmod 644 /etc/resolv.conf
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/99-nephio-dns.conf <<EOF
[Resolve]
DNS=\${DNS}
FallbackDNS=
Domains=
DNSStubListener=no
EOF
if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
  systemctl restart systemd-resolved || true
fi
grep -q \"nameserver \${DNS}\" /etc/resolv.conf
")"

  info "DNS set to ${dns} (/etc/resolv.conf + systemd-resolved)"
  if run_ssh "$user" "$host" "grep -q 'nameserver ${dns}' /etc/resolv.conf && getent hosts google.com >/dev/null"; then
    info "DNS ${dns} ok (resolv + lookup)"
  elif run_ssh "$user" "$host" "grep -q 'nameserver ${dns}' /etc/resolv.conf"; then
    info "resolv.conf has ${dns} (lookup skipped/failed)"
  else
    err "failed to set DNS ${dns} on ${host}"
    return 1
  fi
}

read_node_credentials() {
  local node="$1" init_ip="$2" prefix user_var pass_var user pass

  prefix="$(node_env_prefix "$node")"
  user_var="${prefix}_USER"
  pass_var="${prefix}_PASS"
  user="${NODE_USER[$node]:-${!user_var:-}}"
  pass="${!pass_var:-}"

  if [[ -z "$user" ]]; then
    read -rp "${node} username: " user
  fi
  if [[ -z "$pass" ]]; then
    read -rsp "${user} password: " pass
    echo >&2
  fi

  NODE_USER[$node]="$user"
  NODE_PASSWORD[$node]="$pass"
  NODE_INIT_HOST[$node]="$init_ip"
}

ssh_works() {
  local user="$1" host="$2"
  local -a opts
  mapfile -t opts < <(ssh_opts)
  ssh "${opts[@]}" -o BatchMode=yes "${user}@${host}" "true" 2>/dev/null
}

run_ssh() {
  local user="$1" host="$2"
  shift 2
  local -a opts
  mapfile -t opts < <(ssh_opts)
  ssh "${opts[@]}" "${user}@${host}" "$@"
}

run_scp() {
  local src="$1" user="$2" host="$3" dest="$4"
  local -a opts
  mapfile -t opts < <(ssh_opts)
  scp "${opts[@]}" "$src" "${user}@${host}:${dest}"
}

copy_ssh_key() {
  local node="$1" pubkey="$2"
  local user="${NODE_USER[$node]}"
  local host="${NODE_INIT_HOST[$node]}"
  local pass="${NODE_PASSWORD[$node]}"
  local -a opts copy_cmd

  echo
  echo "========================================"
  echo " SSH key: ${node} (${user}@${host})"
  echo "========================================"

  if ssh_works "$user" "$host"; then
    info "passwordless SSH already works on ${host}"
    return 0
  fi

  mapfile -t opts < <(ssh_copy_id_opts)
  copy_cmd=(ssh-copy-id -f "${opts[@]}" -i "$pubkey" "${user}@${host}")

  if command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$pass" sshpass -e "${copy_cmd[@]}"
  else
    err "sshpass required for password-based ssh-copy-id (install sshpass or pre-configure keys)"
    return 1
  fi

  if ssh_works "$user" "$host"; then
    info "key installed on ${host}"
    return 0
  fi

  err "passwordless SSH still failing on ${host}"
  return 1
}

remote_sudo() {
  local user="$1" host="$2" pass="$3" cmd="$4"

  if run_ssh "$user" "$host" "sudo -n true" 2>/dev/null; then
    run_ssh "$user" "$host" "sudo -n bash -lc $(printf '%q' "$cmd")"
    return 0
  fi

  printf '%s\n' "$pass" | run_ssh "$user" "$host" \
    "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
}

setup_passwordless_sudo() {
  local node="$1"
  local user="${NODE_USER[$node]}"
  local host="${NODE_INIT_HOST[$node]}"
  local pass="${NODE_PASSWORD[$node]}"

  echo
  echo "========================================"
  echo " Passwordless sudo: ${node} (${user}@${host})"
  echo "========================================"

  if run_ssh "$user" "$host" "sudo -n true" 2>/dev/null; then
    info "already passwordless"
    return 0
  fi

  remote_sudo "$user" "$host" "$pass" \
    "printf '%s\n' '${user} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/${user} && chmod 440 /etc/sudoers.d/${user}"

  if run_ssh "$user" "$host" "sudo -n true" 2>/dev/null; then
    info "passwordless sudo enabled"
    return 0
  fi

  err "passwordless sudo verification failed on ${host}"
  return 1
}

set_hostname() {
  local node="$1"
  local user="${NODE_USER[$node]}"
  local host="${NODE_INIT_HOST[$node]}"
  local pass="${NODE_PASSWORD[$node]}"

  echo
  echo "========================================"
  echo " Hostname: ${node} (${user}@${host})"
  echo "========================================"

  remote_sudo "$user" "$host" "$pass" "
    hostnamectl set-hostname '${node}'
    if grep -q '^127.0.1.1' /etc/hosts; then
      sed -i 's/^127.0.1.1.*/127.0.1.1\t${node}/' /etc/hosts
    else
      printf '127.0.1.1\t%s\n' '${node}' >> /etc/hosts
    fi
  "

  if run_ssh "$user" "$host" "hostname -s" 2>/dev/null | grep -qx "$node"; then
    info "hostname set to ${node}"
    return 0
  fi

  err "hostname verification failed on ${host}"
  return 1
}

deploy_netplan() {
  local node="$1"
  local user="${NODE_USER[$node]}"
  local host="${NODE_INIT_HOST[$node]}"
  local netplan_src="${NETPLAN_DIR}/${node}/${NETPLAN_FILE}"
  local remote_tmp="/tmp/${NETPLAN_FILE}.$$"
  local mgmt_ip="${NODE_MGMT_IP[$node]}"

  echo
  echo "========================================"
  echo " Netplan: ${node} (mgmt ${mgmt_ip})"
  echo "========================================"

  if [[ ! -f "$netplan_src" ]]; then
    err "missing ${netplan_src}"
    return 1
  fi

  run_scp "$netplan_src" "$user" "$host" "$remote_tmp"
  run_ssh "$user" "$host" "sudo install -m 600 '$remote_tmp' '/etc/netplan/${NETPLAN_FILE}' && rm -f '$remote_tmp'"
  run_ssh "$user" "$host" "sudo netplan apply"
  sync_resolv_conf "$user" "$host" "$netplan_src" || return 1

  run_ssh "$user" "$host" "sudo bash -lc \"
    if ! grep -qE '^${mgmt_ip}[[:space:]]+${node}\$' /etc/hosts; then
      echo '${mgmt_ip} ${node}' >> /etc/hosts
    fi
  \""

  info "netplan applied; checking mgmt IP ${mgmt_ip}"
  if run_ssh "$user" "$host" "ip -4 -o addr show | grep -q '${mgmt_ip}/'"; then
    info "mgmt IP ${mgmt_ip} is configured"
    NODE_INIT_HOST[$node]="$mgmt_ip"
    return 0
  fi

  err "mgmt IP ${mgmt_ip} not found after netplan apply (check interface name in ${netplan_src})"
  return 1
}

ensure_ssh_config() {
  local node="$1"
  local user="${NODE_USER[$node]}"
  local mgmt_ip="${NODE_MGMT_IP[$node]}"
  local identity id_line block

  identity="$(default_identity)"
  id_line="    IdentityFile ${identity/#$HOME/\~}"

  if [[ ! -f "$SSH_CONFIG" ]]; then
    err "SSH config not found: $SSH_CONFIG"
    return 1
  fi

  if grep -qE "^Host ${node}$" "$SSH_CONFIG"; then
    info "SSH config already has Host ${node}; updating HostName to ${mgmt_ip}"
    python3 -c '
import sys, re
path, node, ip = sys.argv[1:4]
with open(path, "r") as f:
    content = f.read()
pattern = r"(Host\s+" + re.escape(node) + r"\s*\n(?:\s+[^\n]*\n)*?\s*HostName\s+)[^\n]+"
new_content, count = re.subn(pattern, r"\g<1>" + ip, content)
if count > 0:
    with open(path, "w") as f:
        f.write(new_content)
' "$SSH_CONFIG" "$node" "$mgmt_ip"
    return 0
  fi

  block="$(cat <<EOF

Host ${node}
    HostName ${mgmt_ip}
    User ${user}
    Port 22
${id_line}
EOF
)"

  printf '%s\n' "$block" >> "$SSH_CONFIG"
  info "added Host ${node} -> ${mgmt_ip} (${user}) in ${SSH_CONFIG}"
}

setup_node() {
  local node="$1" init_ip="$2" pubkey="$3"
  local netplan_src="${NETPLAN_DIR}/${node}/${NETPLAN_FILE}"
  local mgmt_ip

  if [[ ! -f "$netplan_src" ]]; then
    err "missing netplan for ${node}: ${netplan_src}"
    return 1
  fi

  mgmt_ip="$(mgmt_ip_from_netplan "$netplan_src")"
  if [[ -z "$mgmt_ip" ]]; then
    err "could not read mgmt IP from ${netplan_src}"
    return 1
  fi
  NODE_MGMT_IP[$node]="$mgmt_ip"

  read_node_credentials "$node" "$init_ip"
  copy_ssh_key "$node" "$pubkey" || return 1
  set_hostname "$node" || return 1
  setup_passwordless_sudo "$node" || return 1
  deploy_netplan "$node" || return 1
  ensure_ssh_config "$node" || return 1

  local user="${NODE_USER[$node]}"
  if ssh_works "$user" "$mgmt_ip"; then
    info "${node}: SSH ok via mgmt IP ${mgmt_ip}"
  else
    err "${node}: SSH via mgmt IP ${mgmt_ip} failed (netplan may need a reboot)"
    return 1
  fi
}

main() {
  local assume_yes=0
  local -a init_ips=()
  local pubkey failed=0 i node init_ip

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes) assume_yes=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ $# -lt 3 ]]; then
          err "expected <name> <username> <ip> triplet, got: $*"
          usage >&2
          exit 1
        fi
        NODE_NAMES+=("$1")
        NODE_USER["$1"]="$2"
        init_ips+=("$3")
        shift 3
        ;;
    esac
  done

  if [[ ${#NODE_NAMES[@]} -eq 0 ]]; then
    usage >&2
    exit 1
  fi

  command -v ssh-copy-id >/dev/null 2>&1 || {
    err "ssh-copy-id not found (install openssh-client)"
    exit 1
  }

  pubkey="$(default_pubkey)"
  if [[ ! -f "$pubkey" ]]; then
    err "public key not found: $pubkey"
    exit 1
  fi

  log "pubkey: ${pubkey}"
  for i in "${!NODE_NAMES[@]}"; do
    log "  ${NODE_NAMES[$i]} @ ${init_ips[$i]} (${NODE_USER[${NODE_NAMES[$i]}]}) -> mgmt from ${NETPLAN_DIR}/${NODE_NAMES[$i]}/${NETPLAN_FILE}"
  done

  if [[ "$assume_yes" != "1" ]]; then
    read -rp "Configure ${#NODE_NAMES[@]} node(s)? [y/N] " ans
    if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
      echo "Aborted."
      exit 1
    fi
  fi

  for i in "${!NODE_NAMES[@]}"; do
    node="${NODE_NAMES[$i]}"
    init_ip="${init_ips[$i]}"
    if ! setup_node "$node" "$init_ip" "$pubkey"; then
      failed=1
    fi
  done

  echo
  if (( failed )); then
    err "one or more nodes failed"
    exit 1
  fi
  log "done — use: ssh -F ${SSH_CONFIG} ${NODE_NAMES[0]}"
}

main "$@"
