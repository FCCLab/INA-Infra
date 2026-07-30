#!/usr/bin/env bash
# Nephio hypervisor: VLAN subinterfaces on eno1 as L2 uplinks for site bridges.
#
#   eno1.132 -> br-mgmt         (10.1.132.0/24 mgmt)
#   eno1.135 -> br-int-central   (10.1.137.0/24 site)
#   eno1.136 -> br-int-regional
#   eno1.137 -> br-int-edge
#
# Removes direct eno1 -> br-mgmt and eno2 -> br-int-edge. br-int-ue stays host-only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PHYSICAL="${PHYSICAL:-eno1}"
REMOVE_ENO2="${REMOVE_ENO2:-eno2}"
BRIDGE_PREFIX="${BRIDGE_PREFIX:-24}"

# vlan_id:bridge:host_ip
UPLINKS=(
  "132:br-mgmt:10.1.132.10"
  "135:br-int-central:10.1.137.10"
  "136:br-int-regional:10.1.137.11"
  "137:br-int-edge:10.1.137.12"
)

MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO=(sudo)
    else
      err "run as root or install sudo"
      exit 1
    fi
  else
    SUDO=()
  fi
}

run() { "${SUDO[@]}" "$@"; }

vlan_iface() { printf '%s.%s' "$PHYSICAL" "$1"; }

iface_master() {
  local iface="$1"
  local master_link="/sys/class/net/${iface}/master"
  [[ -e "$master_link" ]] || return 0
  basename "$(readlink "$master_link")"
}

bridge_exists() { ip link show "$1" &>/dev/null; }

configure_bridge_l2() {
  local br="$1"
  if [[ -f "/sys/class/net/${br}/bridge/stp_state" ]]; then
    echo 0 | run tee "/sys/class/net/${br}/bridge/stp_state" >/dev/null 2>&1 || true
  fi
  for knob in nf_call_iptables nf_call_ip6tables nf_call_arptables; do
    if [[ -f "/sys/class/net/${br}/bridge/${knob}" ]]; then
      echo 0 | run tee "/sys/class/net/${br}/bridge/${knob}" >/dev/null 2>&1 || true
    fi
  done
}

unmanage_nm() {
  local dev="$1"
  command -v nmcli >/dev/null 2>&1 || return 0
  if nmcli -t -f DEVICE device status 2>/dev/null | grep -qx "$dev"; then
    log "NetworkManager: disconnect and unmanage $dev"
    run nmcli device disconnect "$dev" 2>/dev/null || true
    run nmcli device set "$dev" managed no 2>/dev/null || true
  fi
}

detach_from_bridge() {
  local iface="$1"
  ip link show "$iface" &>/dev/null || return 0
  local master
  master="$(iface_master "$iface")"
  [[ -n "$master" ]] || return 0
  log "detach $iface from $master"
  run ip link set "$iface" down 2>/dev/null || true
  run ip link set "$iface" nomaster 2>/dev/null || true
  run ip link set "$iface" up 2>/dev/null || true
}

ensure_bridge() {
  local br="$1" ip="$2"
  if ! bridge_exists "$br"; then
    log "create bridge $br"
    run ip link add name "$br" type bridge
  else
    info "bridge $br exists"
  fi
  configure_bridge_l2 "$br"
  run ip link set "$br" up
  if ! ip -4 addr show dev "$br" | grep -q "inet ${ip}/${BRIDGE_PREFIX}"; then
    log "assign ${ip}/${BRIDGE_PREFIX} on $br"
    run ip addr flush dev "$br" 2>/dev/null || true
    run ip addr add "${ip}/${BRIDGE_PREFIX}" dev "$br"
  else
    info "$br already has ${ip}/${BRIDGE_PREFIX}"
  fi
}

ensure_vlan() {
  local vid="$1"
  local iface
  iface="$(vlan_iface "$vid")"
  if ip link show "$iface" &>/dev/null; then
    info "VLAN $iface exists"
    return 0
  fi
  log "create $iface (id $vid)"
  run modprobe 8021q 2>/dev/null || true
  run ip link add link "$PHYSICAL" name "$iface" type vlan id "$vid"
}

attach_vlan_to_bridge() {
  local vid="$1" br="$2"
  local iface
  iface="$(vlan_iface "$vid")"
  local master
  master="$(iface_master "$iface")"
  if [[ "$master" == "$br" ]]; then
    info "$iface already on $br"
    return 0
  fi
  if [[ -n "$master" ]]; then
    err "$iface is on $master; detach first"
    exit 1
  fi
  log "attach $iface -> $br"
  run ip link set "$iface" up
  run ip link set "$iface" master "$br"
  run ip link set "$iface" up
}

prepare_physical() {
  if ! ip link show "$PHYSICAL" &>/dev/null; then
    err "physical NIC $PHYSICAL not found"
    exit 1
  fi
  unmanage_nm "$PHYSICAL"
  detach_from_bridge "$PHYSICAL"
  if ip -4 addr show dev "$PHYSICAL" 2>/dev/null | grep -q 'inet '; then
    log "flush IPv4 on $PHYSICAL (VLAN uplinks carry traffic)"
    run ip addr flush dev "$PHYSICAL" 2>/dev/null || true
  fi
  run ip link set "$PHYSICAL" up
}

ensure_mgmt_route() {
  if ip route show "$MGMT_CIDR" 2>/dev/null | grep -q "dev br-mgmt"; then
    info "route $MGMT_CIDR via br-mgmt: ok"
    return 0
  fi
  log "route $MGMT_CIDR dev br-mgmt"
  run ip route add "$MGMT_CIDR" dev br-mgmt 2>/dev/null || true
}

cmd_remove_eno2() {
  ip link show "$REMOVE_ENO2" &>/dev/null || {
    info "$REMOVE_ENO2 not present (skip)"
    return 0
  }
  detach_from_bridge "$REMOVE_ENO2"
  if command -v nmcli >/dev/null 2>&1; then
    run nmcli device set "$REMOVE_ENO2" managed yes 2>/dev/null || true
  fi
  info "$REMOVE_ENO2 detached (no longer a bridge port)"
}

cmd_setup() {
  need_root
  local entry vid br ip

  log "Step 1 — remove $REMOVE_ENO2 from br-int-edge"
  cmd_remove_eno2

  log "Step 2 — prepare $PHYSICAL (no bridge membership, no IP)"
  prepare_physical

  log "Step 3 — VLAN uplinks on $PHYSICAL"
  for entry in "${UPLINKS[@]}"; do
    IFS=: read -r vid br ip <<<"$entry"
    ensure_bridge "$br" "$ip"
    ensure_vlan "$vid"
    attach_vlan_to_bridge "$vid" "$br"
  done

  ensure_mgmt_route

  log "done"
  cmd_status
}

cmd_status() {
  local entry vid br ip iface master
  log "physical $PHYSICAL"
  if ip link show "$PHYSICAL" &>/dev/null; then
    ip -br link show "$PHYSICAL" | sed 's/^/    /'
    info "master: $(iface_master "$PHYSICAL" || echo none)"
  else
    info "(missing)"
  fi

  for entry in "${UPLINKS[@]}"; do
    IFS=: read -r vid br ip <<<"$entry"
    iface="$(vlan_iface "$vid")"
    log "$iface -> $br (${ip}/${BRIDGE_PREFIX})"
    if ip link show "$iface" &>/dev/null; then
      master="$(iface_master "$iface")"
      info "exists, master=${master:-none}"
    else
      info "(missing — run: $0 setup)"
    fi
    if bridge_exists "$br"; then
      ip -4 -o addr show dev "$br" 2>/dev/null | awk '{print "    bridge IP: " $4}' || true
      printf '    ports: '
      bridge link show dev "$br" 2>/dev/null | awk '{print $1}' | cut -d@ -f1 | tr '\n' ' '
      echo
    fi
  done
}

cmd_down() {
  need_root
  local entry vid iface
  for entry in "${UPLINKS[@]}"; do
    IFS=: read -r vid _br _ip <<<"$entry"
    iface="$(vlan_iface "$vid")"
    detach_from_bridge "$iface"
    if ip link show "$iface" &>/dev/null; then
      log "delete $iface"
      run ip link del "$iface" 2>/dev/null || true
    fi
  done
  log "VLAN uplinks removed; bridges unchanged"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [setup|status|down|remove-eno2]

VLAN L2 uplinks on $PHYSICAL for Nephio bridges (replaces direct $PHYSICAL -> br-mgmt
and $REMOVE_ENO2 -> br-int-edge):

  eno1.132 -> br-mgmt         (10.1.132.0/24)
  eno1.135 -> br-int-central  (10.1.137.0/24 site)
  eno1.136 -> br-int-regional
  eno1.137 -> br-int-edge

  setup         default — detach eno2, create VLANs, attach to bridges
  status        show VLAN and bridge port state
  down          detach and delete VLAN subinterfaces (bridges kept)
  remove-eno2   detach $REMOVE_ENO2 only

After setup, run mgmt VM wiring if needed:
  sudo ${SCRIPT_DIR}/setup_mgmt_bridge.sh setup

Environment:
  PHYSICAL       physical NIC (default: eno1)
  REMOVE_ENO2    NIC to detach from br-int-edge (default: eno2)
EOF
}

main() {
  local cmd="${1:-setup}"
  shift || true
  case "$cmd" in
    setup) cmd_setup "$@" ;;
    status) cmd_status "$@" ;;
    down) cmd_down "$@" ;;
    remove-eno2) need_root; cmd_remove_eno2 ;;
    -h|--help|help) usage ;;
    *)
      err "unknown command: $cmd"
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
