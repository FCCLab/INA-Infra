#!/usr/bin/env bash
# Install Avahi/mDNS on a testbed host and set its hostname to the SSH alias.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <server-name>

SSH to <server-name> (see utils/ssh_config/config), set hostname to <server-name>,
and install Avahi/mDNS.

Examples:
  $(basename "$0") regional-1
  $(basename "$0") central-0

Environment:
  SSH_CONFIG   SSH config file (default: utils/ssh_config/config)
  DNS_SERVER   Resolver for apt on remote host (default: 10.1.132.200, Pi-hole on mgmt-0)
EOF
}

configure_dns() {
  local dns="${DNS_SERVER:-10.1.132.200}"

  echo "Configuring DNS (${dns})..."
  mkdir -p /etc/systemd/resolved.conf.d
  cat >/etc/systemd/resolved.conf.d/nephio-dns.conf <<EOF
[Resolve]
DNS=${dns}
FallbackDNS=8.8.8.8
Domains=~.
EOF
  if systemctl is-active systemd-resolved &>/dev/null; then
    systemctl restart systemd-resolved
  fi

  if ! getent ahostsv4 archive.ubuntu.com &>/dev/null; then
    echo "Falling back to static /etc/resolv.conf"
    rm -f /etc/resolv.conf
    echo "nameserver ${dns}" >/etc/resolv.conf
  fi

  if ! getent ahostsv4 archive.ubuntu.com &>/dev/null; then
    echo "error: DNS still not working (tried ${dns})" >&2
    exit 1
  fi
}

remote_install() {
  local NEW_HOSTNAME="$1"
  local CURRENT_HOSTNAME

  if [[ "$EUID" -ne 0 ]]; then
    echo "Please run as root (use sudo)." >&2
    exit 1
  fi

  CURRENT_HOSTNAME="$(hostname)"

  echo "=== Starting mDNS and Hostname Setup ==="

  echo "Changing hostname from '$CURRENT_HOSTNAME' to '$NEW_HOSTNAME'..."
  hostnamectl set-hostname "$NEW_HOSTNAME"

  if grep -q '^127.0.1.1' /etc/hosts; then
    sed -i "s/^127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/g" /etc/hosts
  else
    echo -e "127.0.1.1\t$NEW_HOSTNAME" >>/etc/hosts
  fi

  configure_dns

  echo "Updating package lists..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null

  echo "Installing Avahi daemon and mDNS resolver (libnss-mdns)..."
  apt-get install -y avahi-daemon avahi-utils libnss-mdns

  if ! grep -q 'mdns4_minimal' /etc/nsswitch.conf; then
    sed -i 's/^hosts:.*/hosts:          files mdns4_minimal [NOTFOUND=return] dns mdns4/' /etc/nsswitch.conf
  fi

  local AVAHI_CONF=/etc/avahi/avahi-daemon.conf
  echo "Configuring Avahi to advertise on all interfaces..."
  sed -i 's/^allow-interfaces=/#allow-interfaces=/' "$AVAHI_CONF"
  sed -i 's/^deny-interfaces=/#deny-interfaces=/' "$AVAHI_CONF"

  echo "Enabling and starting Avahi service..."
  systemctl enable avahi-daemon
  systemctl restart avahi-daemon

  echo "=== Setup Complete! ==="
  echo "Your new hostname is: $NEW_HOSTNAME"
  echo "You can now reach this device at: $NEW_HOSTNAME.local"
  echo "--------------------------------------------------------"
  echo "Verify locally:  avahi-resolve-host-name -4 ${NEW_HOSTNAME}.local"
  echo "Verify remotely: ping -4 <other-host>.local   (from a peer with mDNS enabled)"
  echo "NOTE: Reboot if hostname or nsswitch changes do not take effect immediately."
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${INSTALL_MDNS_REMOTE:-}" == "1" ]]; then
  if [[ -z "${1:-}" ]]; then
    echo "Usage: INSTALL_MDNS_REMOTE=1 $0 <new-hostname>" >&2
    exit 1
  fi
  remote_install "$1"
  exit 0
fi

if [[ -z "${1:-}" ]]; then
  usage >&2
  exit 1
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

SERVER_NAME="$1"
REMOTE_SCRIPT="/tmp/install_mDNS.$$"

echo "Connecting to $SERVER_NAME and setting hostname to $SERVER_NAME..."
scp -q -F "$SSH_CONFIG" "$0" "${SERVER_NAME}:${REMOTE_SCRIPT}"
ssh -F "$SSH_CONFIG" -t "$SERVER_NAME" \
  "sudo DNS_SERVER='${DNS_SERVER:-10.1.132.200}' INSTALL_MDNS_REMOTE=1 bash '$REMOTE_SCRIPT' '$SERVER_NAME'; ec=\$?; rm -f '$REMOTE_SCRIPT'; exit \$ec"
