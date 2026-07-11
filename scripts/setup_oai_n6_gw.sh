#!/usr/bin/env bash
# Create host macvlan gateway 10.1.139.1 so UPF N6 can reach mgmt (10.1.132.0/24).
#
# Multus N6 uses gateway 10.1.139.1, but that address is not on the host by default
# (OAI macvlan is pods-only). This shim + NAT bridges N6 (.139) to mgmt (.132),
# e.g. OpenSpeedTest at 10.1.132.11.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
PARENT="${OAI_N6_GW_PARENT:-$SITE_IFACE}"
SHIM="${OAI_N6_GW_IFACE:-oai-n6-gw}"
GW_CIDR="${OAI_N6_GW_CIDR:-${OAI_MACVLAN_GW}/24}"
MGMT_IF="${OAI_N6_MGMT_IFACE:-enp1s0}"
MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"
N6_SRC="${OAI_N6_SRC:-$(oai_macvlan_ip central 4)}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-1}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [host ...]

On each host (default: central-0 central-1):
  1. Create macvlan ${SHIM}@${PARENT} with ${GW_CIDR}
  2. Enable IPv4 forwarding
  3. FORWARD ${SHIM} <-> ${MGMT_IF}
  4. MASQUERADE ${N6_SRC} -> ${MGMT_CIDR} out ${MGMT_IF}
  5. Optionally install systemd unit for reboot persistence

Examples:
  $(basename "$0")
  $(basename "$0") central-0
  INSTALL_SYSTEMD=0 $(basename "$0") central-0

Environment:
  OAI_N6_GW_PARENT   Parent NIC (default: ${SITE_IFACE})
  OAI_N6_GW_IFACE    Shim name (default: oai-n6-gw)
  OAI_N6_GW_CIDR     Gateway CIDR (default: ${OAI_MACVLAN_GW}/24)
  OAI_N6_SRC         UPF N6 IP to SNAT (default: $(oai_macvlan_ip central 4))
  MGMT_CIDR          Mgmt destination (default: 10.1.132.0/24)
  INSTALL_SYSTEMD    Install oneshot unit (default: 1)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

remote_setup_body() {
  cat <<REMOTE
set -euo pipefail
PARENT='${PARENT}'
SHIM='${SHIM}'
GW_CIDR='${GW_CIDR}'
MGMT_IF='${MGMT_IF}'
MGMT_CIDR='${MGMT_CIDR}'
N6_SRC='${N6_SRC}'
INSTALL_SYSTEMD='${INSTALL_SYSTEMD}'

if ! ip link show "\$PARENT" &>/dev/null; then
  echo "error: parent interface \$PARENT not found" >&2
  exit 1
fi

if ! ip link show "\$SHIM" &>/dev/null; then
  ip link add "\$SHIM" link "\$PARENT" type macvlan mode bridge
fi
if ! ip -4 addr show dev "\$SHIM" | grep -q " \${GW_CIDR%%/*}/"; then
  ip addr add "\$GW_CIDR" dev "\$SHIM"
fi
ip link set "\$SHIM" up
sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -C FORWARD -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT
iptables -C FORWARD -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s "\${N6_SRC}/32" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "\${N6_SRC}/32" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE

if [[ "\$INSTALL_SYSTEMD" == "1" ]]; then
  cat >/etc/systemd/system/oai-n6-gw.service <<'UNIT'
[Unit]
Description=OAI UPF N6 macvlan gateway (10.1.139.1) toward mgmt
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/oai-n6-gw-setup.sh

[Install]
WantedBy=multi-user.target
UNIT

  cat >/usr/local/sbin/oai-n6-gw-setup.sh <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
PARENT='${PARENT}'
SHIM='${SHIM}'
GW_CIDR='${GW_CIDR}'
MGMT_IF='${MGMT_IF}'
MGMT_CIDR='${MGMT_CIDR}'
N6_SRC='${N6_SRC}'
if ! ip link show "\$SHIM" &>/dev/null; then
  ip link add "\$SHIM" link "\$PARENT" type macvlan mode bridge
fi
if ! ip -4 addr show dev "\$SHIM" | grep -q " \${GW_CIDR%%/*}/"; then
  ip addr add "\$GW_CIDR" dev "\$SHIM"
fi
ip link set "\$SHIM" up
sysctl -w net.ipv4.ip_forward=1 >/dev/null
iptables -C FORWARD -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT 2>/dev/null || \\
  iptables -I FORWARD 1 -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT
iptables -C FORWARD -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \\
  iptables -I FORWARD 1 -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s "\${N6_SRC}/32" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \\
  iptables -t nat -A POSTROUTING -s "\${N6_SRC}/32" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE
SCRIPT
  chmod 755 /usr/local/sbin/oai-n6-gw-setup.sh
  systemctl daemon-reload
  systemctl enable --now oai-n6-gw.service
fi

echo "==> \$(hostname -s): \$SHIM \${GW_CIDR} up; NAT \${N6_SRC} -> \${MGMT_CIDR} via \${MGMT_IF}"
ip -4 addr show "\$SHIM" | sed 's/^/    /'
REMOTE
}

setup_host() {
  local host="$1"
  echo
  echo "========================================"
  echo " OAI N6 GW: ${host}"
  echo "========================================"
  ssh_cmd -o RequestTTY=no "$host" "sudo bash -lc $(printf '%q' "$(remote_setup_body)")"
}

main() {
  local hosts=("$@")

  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  if [[ ${#hosts[@]} -eq 0 ]]; then
    hosts=(central-0 central-1)
  fi

  if [[ ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    exit 1
  fi

  local failed=0 host
  for host in "${hosts[@]}"; do
    if ! setup_host "$host"; then
      echo "error: setup failed on ${host}" >&2
      failed=1
    fi
  done

  echo
  if [[ "$failed" -eq 0 ]]; then
    echo "Done. From UPF netns: ping -I 10.1.139.14 10.1.132.11"
  else
    exit 1
  fi
}

main "$@"
