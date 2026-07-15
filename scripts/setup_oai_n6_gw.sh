#!/usr/bin/env bash
# Create per-site host macvlan N6 gateway so UPF can reach mgmt (10.1.132.0/24).
#
# Shared L2 10.1.139.0/24 cannot use one .139.1 on every node (ARP clash). Each
# site CP gets a unique shim:
#   central-0 → 10.1.139.1, regional-0 → 10.1.139.2, edge-0 → 10.1.139.3
#
# Also injects 10.1.132.0/24 via <site-gw> dev n6 into running upf-slice-* pods.
#
# Multus N6 NADs (render_oai_slice_deployment_gitops.sh) must use the same GW.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
PARENT="${OAI_N6_GW_PARENT:-$SITE_IFACE}"
SHIM="${OAI_N6_GW_IFACE:-oai-n6-gw}"
MGMT_IF="${OAI_N6_MGMT_IFACE:-enp1s0}"
MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-1}"
UPF_NS="${OAI_UPF_NS:-oai-upf}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [host ...]

On each site control-plane host (default: central-0 regional-0 edge-0):
  1. Create macvlan ${SHIM}@${PARENT} with that site's N6 GW IP
  2. Enable IPv4 forwarding + FORWARD ${SHIM} <-> ${MGMT_IF}
  3. MASQUERADE 10.1.139.0/24 -> ${MGMT_CIDR} out ${MGMT_IF}
  4. Inject ${MGMT_CIDR} via site-GW into running UPF pods
  5. Optionally install systemd unit for reboot persistence

Examples:
  $(basename "$0")
  $(basename "$0") central-0
  INSTALL_SYSTEMD=0 $(basename "$0") regional-0

Environment:
  OAI_N6_GW_PARENT   Parent NIC (default: ${SITE_IFACE})
  OAI_N6_GW_IFACE    Shim name (default: oai-n6-gw)
  MGMT_CIDR          Mgmt destination (default: ${MGMT_CIDR})
  INSTALL_SYSTEMD    Install oneshot unit (default: 1)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

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
INSTALL_SYSTEMD='${INSTALL_SYSTEMD}'

if ! ip link show "\$PARENT" &>/dev/null; then
  echo "error: parent interface \$PARENT not found" >&2
  exit 1
fi

# Drop stale address if this host previously claimed another site's GW.
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
# Cover all UPF N6 (and any SNAT to .139) toward mgmt.
iptables -t nat -C POSTROUTING -s 10.1.139.0/24 -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s 10.1.139.0/24 -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE

if [[ "\$INSTALL_SYSTEMD" == "1" ]]; then
  cat >/etc/systemd/system/oai-n6-gw.service <<'UNIT'
[Unit]
Description=OAI UPF N6 macvlan gateway toward mgmt
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
GW_IP='${gw_ip}'
GW_CIDR="\${GW_IP}/24"
MGMT_IF='${MGMT_IF}'
MGMT_CIDR='${MGMT_CIDR}'
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
iptables -t nat -C POSTROUTING -s 10.1.139.0/24 -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE 2>/dev/null || \\
  iptables -t nat -A POSTROUTING -s 10.1.139.0/24 -d "\$MGMT_CIDR" -o "\$MGMT_IF" -j MASQUERADE
SCRIPT
  chmod 755 /usr/local/sbin/oai-n6-gw-setup.sh
  systemctl daemon-reload
  systemctl enable --now oai-n6-gw.service
fi

echo "==> \$(hostname -s): \$SHIM \${GW_CIDR} up; NAT 10.1.139.0/24 -> \${MGMT_CIDR} via \${MGMT_IF}"
ip -4 addr show "\$SHIM" | sed 's/^/    /'
REMOTE
}

inject_upf_routes() {
  local host="$1"
  local gw_ip="$2"
  echo "==> ${host}: inject ${MGMT_CIDR} via ${gw_ip} into UPF pods"
  ssh_cmd -o RequestTTY=no "$host" bash -s -- "$UPF_NS" "$MGMT_CIDR" "$gw_ip" <<'EOF'
set -euo pipefail
ns="$1"
cidr="$2"
gw="$3"
pods=$(kubectl -n "$ns" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep '^upf-slice-' || true)
if [[ -z "$pods" ]]; then
  echo "    (no upf-slice-* pods)"
  exit 0
fi
while read -r pod; do
  [[ -z "$pod" ]] && continue
  if ! kubectl -n "$ns" get pod "$pod" -o jsonpath='{.status.phase}' 2>/dev/null | grep -q Running; then
    echo "    skip $pod (not Running)"
    continue
  fi
  if kubectl -n "$ns" exec "$pod" -c debug -- sh -c \
      "ip link show n6 >/dev/null 2>&1 && ip route replace ${cidr} via ${gw} dev n6" 2>/dev/null; then
    echo "    $pod: route ${cidr} via ${gw} dev n6"
  else
    echo "    $pod: WARNING — could not add route (no n6/debug?)"
  fi
done <<< "$pods"
EOF
}

setup_host() {
  local host="$1"
  local site gw_ip
  site="$(site_from_host "$host")"
  if [[ -z "$site" ]]; then
    echo "error: cannot map host ${host} to central|regional|edge" >&2
    return 1
  fi
  gw_ip="$(oai_n6_gw_ip "$site")"

  echo
  echo "========================================"
  echo " OAI N6 GW: ${host} (site=${site} gw=${gw_ip})"
  echo "========================================"
  ssh_cmd -o RequestTTY=no "$host" "sudo bash -lc $(printf '%q' "$(remote_setup_body "$gw_ip")")"
  inject_upf_routes "$host" "$gw_ip"
}

main() {
  local hosts=("$@")

  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  if [[ ${#hosts[@]} -eq 0 ]]; then
    # One shim per site on the control-plane node only (shared L2).
    hosts=(central-0 regional-0 edge-0)
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
    echo "Done. From UPF: ping -I n6 ${MGMT_API_IP}  (route ${MGMT_CIDR} via site N6 GW)"
    echo "If NADs still point at 10.1.139.1 everywhere, re-render + push slice GitOps."
  else
    exit 1
  fi
}

main "$@"
