#!/usr/bin/env bash
# Attach host NIC eno1 to the Nephio edge internal bridge (br-int-edge).
#
# eno1 cannot be bridged while libvirt macvtap NICs are using it (RTNETLINK: busy).
# VM mgmt NICs use macvtap@eno1; site NICs already reach br-int-edge via vnet*.
set -euo pipefail

BRIDGE="${BRIDGE:-br-int-edge}"
IFACE="${IFACE:-eno1}"

log() { printf '==> %s\n' "$*"; }
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
  local master_link="/sys/class/net/${IFACE}/master"
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

cmd_status() {
  need_root
  log "bridge $BRIDGE"
  if bridge_exists; then
    ip -4 -o addr show dev "$BRIDGE" 2>/dev/null | awk '{print "    " $4}' || true
    printf '    ports: '
    run bridge link show dev "$BRIDGE" 2>/dev/null | awk '{print $1}' | tr '\n' ' '
    echo
  else
    echo "    (missing — run bringup/00_testbed/bringup_switches.sh up --bridges)"
  fi

  log "interface $IFACE"
  local master
  master="$(iface_master)"
  if [[ -n "$master" ]]; then
    echo "    master: $master"
  else
    echo "    master: (none)"
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
    echo "    attach blocked until macvtaps are gone (stop those VMs or detach NICs)"
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
      info_stop="$vm"
      log "  virsh destroy $info_stop"
      run virsh destroy "$info_stop"
    fi
  done
}

unmanage_nm() {
  command -v nmcli >/dev/null 2>&1 || return 0
  if nmcli -t -f DEVICE,STATE device status 2>/dev/null | grep -q "^${IFACE}:"; then
    log "disconnecting NetworkManager on $IFACE"
    run nmcli device disconnect "$IFACE" 2>/dev/null || true
    run nmcli device set "$IFACE" managed no 2>/dev/null || true
  fi
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

  need_root

  if ! bridge_exists; then
    err "bridge $BRIDGE not found"
    exit 1
  fi

  local master
  master="$(iface_master)"
  if [[ "$master" == "$BRIDGE" ]]; then
    log "$IFACE already attached to $BRIDGE"
    exit 0
  fi
  if [[ -n "$master" && "$master" != "$BRIDGE" ]]; then
    err "$IFACE is already a port on $master; remove it first"
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

  log "attach $IFACE -> $BRIDGE"
  run ip link set "$IFACE" down
  run ip addr flush dev "$IFACE" 2>/dev/null || true
  run ip link set "$IFACE" master "$BRIDGE"
  run ip link set "$IFACE" up
  run ip link set "$BRIDGE" up

  log "done"
  bridge link show dev "$BRIDGE" 2>/dev/null | grep -F "$IFACE" || true
}

cmd_detach() {
  need_root
  local master
  master="$(iface_master)"
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

usage() {
  cat <<EOF
Usage: $(basename "$0") <attach|detach|status> [options]

Attach physical NIC $IFACE to Nephio bridge $BRIDGE (10.1.137.12/24).

  status          Show bridge ports and what blocks attach
  attach          Add $IFACE to $BRIDGE (fails if macvtap VMs use $IFACE)
  attach --force  Stop running VMs with direct $IFACE NICs, then attach
  detach          Remove $IFACE from $BRIDGE

Note: Nephio-Edge-{0,1} site traffic already uses $BRIDGE via libvirt vnet*.
      $IFACE is currently used for VM mgmt macvtap NICs — bridging it disconnects that path.

Environment: BRIDGE, IFACE
EOF
}

main() {
  local cmd="${1:-status}"
  shift || true
  case "$cmd" in
    attach) cmd_attach "$@" ;;
    detach) cmd_detach "$@" ;;
    status) cmd_status ;;
    -h|--help|help) usage ;;
    *)
      err "unknown command: $cmd"
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
