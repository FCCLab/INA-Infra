#!/usr/bin/env bash
# Attach host NIC eno2 to Nephio edge site bridge br-int-edge (10.1.137.12/24).
#
# Edge workload VMs (Nephio-Edge-{0,1}) reach br-int-edge via libvirt vnet* on enp7s0.
# Enslaving eno2 extends that L2 to the physical wire (parallel to eno1 -> br-mgmt).
set -euo pipefail

BRIDGE="${BRIDGE:-br-int-edge}"
IFACE="${IFACE:-eno2}"
BRIDGE_IP="${BRIDGE_IP:-10.1.137.12}"
BRIDGE_PREFIX="${BRIDGE_PREFIX:-24}"
VERIFY_EDGE_IP="${VERIFY_EDGE_IP:-10.1.137.130}"

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

bridge_exists() {
  ip link show "$BRIDGE" &>/dev/null
}

iface_master() {
  local iface="$1"
  local master_link="/sys/class/net/${iface}/master"
  [[ -e "$master_link" ]] || return 0
  basename "$(readlink "$master_link")"
}

macvtap_ports() {
  ip -o link show | awk -v iface="$IFACE" '
    $0 ~ "@" iface ":" {
      name = $2
      sub(/@.*/, "", name)
      sub(/:.*/, "", name)
      print name
    }'
}

vms_using_iface() {
  command -v virsh >/dev/null 2>&1 || return 0
  local vm
  for vm in $(virsh list --all --name 2>/dev/null); do
    virsh domiflist "$vm" 2>/dev/null | awk -v vm="$vm" -v dev="$IFACE" \
      'NR > 2 && $3 == dev { print vm; exit }'
  done
}

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
  command -v nmcli >/dev/null 2>&1 || return 0
  if nmcli -t -f DEVICE device status 2>/dev/null | grep -qx "$IFACE"; then
    log "NetworkManager: disconnect and unmanage $IFACE"
    run nmcli device disconnect "$IFACE" 2>/dev/null || true
    run nmcli device set "$IFACE" managed no 2>/dev/null || true
  fi
}

ensure_bridge() {
  if ! bridge_exists; then
    log "create bridge $BRIDGE"
    run ip link add name "$BRIDGE" type bridge
  else
    info "bridge $BRIDGE exists"
  fi
  configure_bridge_l2 "$BRIDGE"
  run ip link set "$BRIDGE" up
  if ! ip -4 addr show dev "$BRIDGE" | grep -q "inet ${BRIDGE_IP}/${BRIDGE_PREFIX}"; then
    log "assign ${BRIDGE_IP}/${BRIDGE_PREFIX} on $BRIDGE"
    run ip addr flush dev "$BRIDGE" 2>/dev/null || true
    run ip addr add "${BRIDGE_IP}/${BRIDGE_PREFIX}" dev "$BRIDGE"
  else
    info "$BRIDGE already has ${BRIDGE_IP}/${BRIDGE_PREFIX}"
  fi
}

migrate_iface_ip_to_bridge() {
  local addr
  addr="$(ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | head -1 || true)"
  [[ -n "$addr" ]] || return 0
  log "migrate $addr from $IFACE to $BRIDGE"
  run ip addr del "$addr" dev "$IFACE" 2>/dev/null || true
  if ! ip -4 addr show dev "$BRIDGE" | grep -qF "$addr"; then
    run ip addr add "$addr" dev "$BRIDGE" 2>/dev/null || true
  fi
}

cmd_status() {
  log "bridge $BRIDGE"
  if bridge_exists; then
    ip -4 -o addr show dev "$BRIDGE" 2>/dev/null | awk '{print "    " $4}' || true
    printf '    ports: '
    bridge link show dev "$BRIDGE" 2>/dev/null | awk '{print $1}' | cut -d@ -f1 | tr '\n' ' ' || true
    echo
  else
    echo "    (missing — run: $0 setup)"
  fi

  log "interface $IFACE"
  if ip link show "$IFACE" &>/dev/null; then
    local master
    master="$(iface_master "$IFACE")"
    if [[ -n "$master" ]]; then
      echo "    master: $master"
    else
      echo "    master: (none)"
    fi
    ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print "    addr: " $4}' || true
  else
    echo "    (interface not found)"
  fi

  local -a taps vms
  mapfile -t taps < <(macvtap_ports)
  if ((${#taps[@]})); then
    echo "    macvtaps (${#taps[@]}): ${taps[*]}"
    mapfile -t vms < <(vms_using_iface | sort -u)
    if ((${#vms[@]})); then
      echo "    libvirt VMs with direct $IFACE NICs:"
      printf '      %s\n' "${vms[@]}"
    fi
    echo "    attach blocked until macvtaps are gone"
  else
    echo "    macvtaps: none (ok to attach)"
  fi
}

stop_vms_on_iface() {
  local vm
  mapfile -t vms < <(vms_using_iface | sort -u)
  if ((${#vms[@]} == 0)); then
    return 0
  fi
  log "stopping VMs with direct $IFACE NICs"
  for vm in "${vms[@]}"; do
    if virsh domstate "$vm" 2>/dev/null | grep -q running; then
      log "  virsh destroy $vm"
      run virsh destroy "$vm"
    fi
  done
}

cmd_attach() {
  local force=0
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      -h|--help) usage; exit 0 ;;
      *) err "unknown option: $arg"; usage >&2; exit 1 ;;
    esac
  done

  if ! ip link show "$IFACE" &>/dev/null; then
    err "interface $IFACE not found"
    exit 1
  fi

  ensure_bridge

  local master
  master="$(iface_master "$IFACE")"
  if [[ "$master" == "$BRIDGE" ]]; then
    log "$IFACE already attached to $BRIDGE"
    exit 0
  fi
  if [[ -n "$master" && "$master" != "$BRIDGE" ]]; then
    err "$IFACE is already a port on $master; detach it first"
    exit 1
  fi

  local -a taps
  mapfile -t taps < <(macvtap_ports)
  if ((${#taps[@]})); then
    if (( ! force )); then
      err "$IFACE has ${#taps[@]} macvtap port(s) — kernel returns 'Device or resource busy'"
      echo >&2
      cmd_status >&2
      echo >&2
      err "stop VMs using $IFACE, or re-run: $0 attach --force"
      exit 1
    fi
    stop_vms_on_iface
    mapfile -t taps < <(macvtap_ports)
    if ((${#taps[@]})); then
      err "macvtap still present after stopping VMs: ${taps[*]}"
      exit 1
    fi
  fi

  unmanage_nm
  migrate_iface_ip_to_bridge

  log "attach $IFACE -> $BRIDGE"
  run ip link set "$IFACE" down
  run ip addr flush dev "$IFACE" 2>/dev/null || true
  run ip link set "$IFACE" master "$BRIDGE"
  run ip link set "$IFACE" up
  run ip link set "$BRIDGE" up

  log "done"
  run bridge link show dev "$BRIDGE" 2>/dev/null | grep -F "$IFACE" || true
}

cmd_detach() {
  if ! ip link show "$IFACE" &>/dev/null; then
    err "interface $IFACE not found"
    exit 1
  fi
  local master
  master="$(iface_master "$IFACE")"
  if [[ "$master" != "$BRIDGE" ]]; then
    log "$IFACE is not attached to $BRIDGE (master=${master:-none})"
    exit 0
  fi
  log "detach $IFACE from $BRIDGE"
  run ip link set "$IFACE" down
  run ip link set "$IFACE" nomaster
  run ip link set "$IFACE" up
  if command -v nmcli >/dev/null 2>&1; then
    run nmcli device set "$IFACE" managed yes 2>/dev/null || true
  fi
  log "done"
}

cmd_setup() {
  local force=0 verify=1
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      --no-verify) verify=0 ;;
      -h|--help) usage; exit 0 ;;
      *) err "unknown option: $arg"; usage >&2; exit 1 ;;
    esac
  done

  if (( force )); then
    cmd_attach --force
  else
    cmd_attach
  fi

  if (( verify )); then
    log "verify L2 to Edge-0 site IP $VERIFY_EDGE_IP"
    if ping -c 1 -W 2 -I "$BRIDGE" "$VERIFY_EDGE_IP" &>/dev/null; then
      info "ping $VERIFY_EDGE_IP via $BRIDGE: ok"
    else
      err "ping $VERIFY_EDGE_IP via $BRIDGE: failed (check Nephio-Edge-0 enp7s0 / setup_ip.sh)"
      exit 1
    fi
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [attach|detach|setup|status] [options]

Connect physical NIC $IFACE to Nephio edge site bridge $BRIDGE (${BRIDGE_IP}/${BRIDGE_PREFIX}).

With no command, runs **setup** (create bridge if needed, attach $IFACE, verify ping).

  setup           ensure_bridge + attach (+ ping Edge-0 site IP) — default
  attach          Enslave $IFACE to $BRIDGE
  attach --force  Stop VMs with direct $IFACE NICs, then attach
  detach          Remove $IFACE from $BRIDGE
  status          Show bridge ports and attach blockers

Nephio-Edge-{0,1} site NICs already use $BRIDGE via libvirt vnet* (enp7s0).
$IFACE becomes an L2 uplink for that bridge (same pattern as eno1 -> br-mgmt).

Examples:
  sudo $0
  sudo $0 status
  sudo $0 attach --force

Environment:
  BRIDGE           default: br-int-edge
  IFACE            default: eno2
  BRIDGE_IP        default: 10.1.137.12
  VERIFY_EDGE_IP   default: 10.1.137.130 (Edge-0 site, enp7s0)
EOF
}

main() {
  local cmd="${1:-setup}"
  shift || true
  case "$cmd" in
    status) cmd_status ;;
    attach) need_root; cmd_attach "$@" ;;
    detach) need_root; cmd_detach ;;
    setup) need_root; cmd_setup "$@" ;;
    -h|--help|help) usage ;;
    *)
      err "unknown command: $cmd"
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
