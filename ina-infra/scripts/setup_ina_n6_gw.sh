#!/usr/bin/env bash
# Create per-site host macvlan N6 gateway for INA-Infra Multus (default 10.1.140.0/24).
#
#   central-0 → 10.1.140.1, regional-0 → 10.1.140.2, edge-0 → 10.1.140.3
#
# Does not modify the OAI .139 shim (oai-n6-gw). Uses iface ina-n6-gw by default.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
PARENT="${INA_N6_GW_PARENT:-$SITE_IFACE}"
SHIM="${INA_N6_GW_IFACE:-ina-n6-gw}"
MGMT_IF="${INA_N6_MGMT_IFACE:-enp1s0}"
MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"
PREFIX="${INA_MACVLAN_PREFIX:-10.1.140}"
CIDR="${PREFIX}.0/24"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-1}"

ina_n6_gw_ip() {
  case "$1" in
    central) printf '%s.1' "$PREFIX" ;;
    regional) printf '%s.2' "$PREFIX" ;;
    edge) printf '%s.3' "$PREFIX" ;;
    *) printf '%s.1' "$PREFIX" ;;
  esac
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [host ...]

On each site CP (default: central-0 regional-0 edge-0):
  Create ${SHIM}@${PARENT} with site GW on ${CIDR}, NAT toward ${MGMT_CIDR}.

Examples:
  $(basename "$0")
  INA_MACVLAN_PREFIX=10.1.140 $(basename "$0") central-0
EOF
}

ssh_cmd() { ssh -F "$SSH_CONFIG" "$@"; }

site_from_host() {
  case "$1" in
    central-*) printf 'central' ;;
    regional-*) printf 'regional' ;;
    edge-*) printf 'edge' ;;
    *) printf '' ;;
  esac
}

remote_setup_body() {
  local gw_ip="$1"
  cat <<REMOTE
set -euo pipefail
PARENT='${PARENT}'
SHIM='${SHIM}'
GW_IP='${gw_ip}'
GW_CIDR="\${GW_IP}/24"
MGMT_IF='${MGMT_IF}'
MGMT_CIDR='${MGMT_CIDR}'
SRC_CIDR='${CIDR}'
INSTALL_SYSTEMD='${INSTALL_SYSTEMD}'

if ! ip link show "\$PARENT" &>/dev/null; then
  echo "error: parent interface \$PARENT not found" >&2
  exit 1
fi

if ip link show "\$SHIM" &>/dev/null; then
  while read -r old; do
    [[ -z "\$old" ]] && continue
    [[ "\$old" == "\$GW_CIDR" ]] && continue
    ip addr del "\$old" dev "\$SHIM" 2>/dev/null || true
  done < <(ip -4 -o addr show dev "\$SHIM" | awk '{print \$4}')
else
  ip link add "\$SHIM" link "\$PARENT" type macvlan mode bridge
fi
if ! ip -4 addr show dev "\$SHIM" | grep -q " \${GW_IP}/"; then
  ip addr add "\$GW_CIDR" dev "\$SHIM"
fi
ip link set "\$SHIM" up
sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -C FORWARD -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT
iptables -C FORWARD -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s "\$SRC_CIDR" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "\$SRC_CIDR" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE

if [[ "\$INSTALL_SYSTEMD" == "1" ]]; then
  cat >/etc/systemd/system/ina-n6-gw.service <<'UNIT'
[Unit]
Description=INA-Infra UPF N6 macvlan gateway toward mgmt
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/ina-n6-gw-setup.sh

[Install]
WantedBy=multi-user.target
UNIT

  cat >/usr/local/sbin/ina-n6-gw-setup.sh <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
PARENT='${PARENT}'
SHIM='${SHIM}'
GW_IP='${gw_ip}'
GW_CIDR="\${GW_IP}/24"
MGMT_IF='${MGMT_IF}'
MGMT_CIDR='${MGMT_CIDR}'
SRC_CIDR='${CIDR}'
if ! ip link show "\$SHIM" &>/dev/null; then
  ip link add "\$SHIM" link "\$PARENT" type macvlan mode bridge
fi
if ! ip -4 addr show dev "\$SHIM" | grep -q " \${GW_IP}/"; then
  ip addr add "\$GW_CIDR" dev "\$SHIM"
fi
ip link set "\$SHIM" up
sysctl -w net.ipv4.ip_forward=1 >/dev/null
iptables -C FORWARD -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT 2>/dev/null || \\
  iptables -I FORWARD 1 -i "\$SHIM" -o "\$MGMT_IF" -j ACCEPT
iptables -C FORWARD -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \\
  iptables -I FORWARD 1 -i "\$MGMT_IF" -o "\$SHIM" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s "\$SRC_CIDR" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \\
  iptables -t nat -A POSTROUTING -s "\$SRC_CIDR" -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE
SCRIPT
  chmod 755 /usr/local/sbin/ina-n6-gw-setup.sh
  systemctl daemon-reload
  systemctl enable --now ina-n6-gw.service
fi

echo "==> \$(hostname -s): \$SHIM \${GW_CIDR} up; NAT \${SRC_CIDR} -> \${MGMT_CIDR}"
ip -4 addr show "\$SHIM" | sed 's/^/    /'
REMOTE
}

setup_host() {
  local host="$1" site gw_ip
  site="$(site_from_host "$host")"
  [[ -n "$site" ]] || { echo "error: bad host $host" >&2; return 1; }
  gw_ip="$(ina_n6_gw_ip "$site")"
  echo
  echo "========================================"
  echo " INA N6 GW: ${host} (site=${site} gw=${gw_ip})"
  echo "========================================"
  ssh_cmd -o RequestTTY=no "$host" "sudo bash -lc $(printf '%q' "$(remote_setup_body "$gw_ip")")"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi
  local hosts=("$@")
  if [[ ${#hosts[@]} -eq 0 ]]; then
    hosts=(central-0 regional-0 edge-0)
  fi
  [[ -f "$SSH_CONFIG" ]] || { echo "error: missing $SSH_CONFIG" >&2; exit 1; }
  local failed=0 host
  for host in "${hosts[@]}"; do
    setup_host "$host" || failed=1
  done
  [[ "$failed" -eq 0 ]] || exit 1
  echo
  echo "Done. INA Multus ${CIDR} N6 GWs on ${SHIM}."
}

main "$@"
