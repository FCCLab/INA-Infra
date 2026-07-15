#!/usr/bin/env bash
# Nephio host mgmt bridge: br-mgmt with eno1 enslaved directly (no macvlan).
#
# Single setup: prepare eno1 (L2 port), create br-mgmt, attach eno1 + VM NICs,
# assign host IP on br-mgmt, verify L2/routing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BRIDGE="${BRIDGE:-br-mgmt}"
PARENT="${PARENT:-eno1}"
LEGACY_MACVLAN="${LEGACY_MACVLAN:-mcv-mgmt}"

MGMT_CIDR="${MGMT_CIDR:-10.1.132.0/24}"
MGMT_PREFIX="${MGMT_PREFIX:-24}"
MGMT_GW="${MGMT_GW:-10.1.132.1}"
BR_MGMT_IP="${BR_MGMT_IP:-10.1.132.10}"
VERIFY_VM_IP="${VERIFY_VM_IP:-10.1.132.200}"
VERIFY_GW="${VERIFY_GW:-1}"

VM_PREFIX="${NEPHIO_VM_PREFIX:-Nephio-}"

declare -A VM_MGMT_MAC=(
  ["${VM_PREFIX}MGMT-0"]="52:54:00:bb:ee:cc"
  ["${VM_PREFIX}MGMT-1"]="52:54:00:ad:55:7e"
  ["${VM_PREFIX}Central-0"]="52:54:00:b0:84:4c"
  ["${VM_PREFIX}Central-1"]="52:54:00:7c:13:66"
  ["${VM_PREFIX}Regional-0"]="52:54:00:75:f6:a3"
  ["${VM_PREFIX}Regional-1"]="52:54:00:0a:8c:61"
  ["${VM_PREFIX}Edge-0"]="52:54:00:1b:63:b2"
  ["${VM_PREFIX}Edge-1"]="52:54:00:68:6a:dd"
  ["${VM_PREFIX}UE-0"]="52:54:00:9b:0e:bc"
  ["${VM_PREFIX}UE-1"]="52:54:00:dc:d6:54"
)

ALL_MGMT_VMS=(
  "${VM_PREFIX}MGMT-0" "${VM_PREFIX}MGMT-1"
  "${VM_PREFIX}Central-0" "${VM_PREFIX}Central-1"
  "${VM_PREFIX}Regional-0" "${VM_PREFIX}Regional-1"
  "${VM_PREFIX}Edge-0" "${VM_PREFIX}Edge-1"
  "${VM_PREFIX}UE-0" "${VM_PREFIX}UE-1"
)

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

bridge_ports() {
  ip -o link show master "$BRIDGE" 2>/dev/null \
    | awk -F': ' '{print $2}' | cut -d@ -f1 | sort -u
}

ping_via_bridge() {
  local target="$1"
  ping -c 1 -W 2 -I "$BRIDGE" "$target" &>/dev/null
}

parent_exists() { ip link show "$PARENT" &>/dev/null; }
bridge_exists() { ip link show "$BRIDGE" &>/dev/null; }
legacy_macvlan_exists() { ip link show "$LEGACY_MACVLAN" &>/dev/null; }

iface_master() {
  local iface="$1"
  local master_link="/sys/class/net/${iface}/master"
  [[ -e "$master_link" ]] || return 0
  basename "$(readlink "$master_link")"
}

parent_ipv4_in_mgmt_subnet() {
  ip -4 -o addr show dev "$PARENT" 2>/dev/null \
    | awk '$4 ~ /^10\.1\.132\./ { split($4, a, "/"); print a[1]; exit }'
}

parent_upper_count() {
  ip -o link show | awk -v p="$PARENT" '$0 ~ "@" p ":" { c++ } END { print c+0 }'
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

unmanage_parent_nm() {
  command -v nmcli >/dev/null 2>&1 || return 0
  if nmcli -t -f DEVICE device status 2>/dev/null | grep -qx "$PARENT"; then
    log "NetworkManager: disconnect and unmanage $PARENT"
    run nmcli device disconnect "$PARENT" 2>/dev/null || true
    run nmcli device set "$PARENT" managed no 2>/dev/null || true
  fi
}

remove_legacy_macvlan() {
  legacy_macvlan_exists || return 0
  log "remove legacy macvlan $LEGACY_MACVLAN"
  run ip link set "$LEGACY_MACVLAN" down 2>/dev/null || true
  run ip link set "$LEGACY_MACVLAN" nomaster 2>/dev/null || true
  run ip link del "$LEGACY_MACVLAN" 2>/dev/null || true
}

prepare_parent() {
  local migrated_ip=""

  if ! parent_exists; then
    err "parent interface $PARENT not found"
    exit 1
  fi

  unmanage_parent_nm
  remove_legacy_macvlan

  local uppers
  uppers="$(parent_upper_count)"
  if (( uppers > 0 )); then
    err "$PARENT has $uppers macvtap/macvlan child(ren) — detach direct $PARENT VM NICs first"
    ip -o link show | awk -v p="$PARENT" '$0 ~ "@" p ":" { print "    " $2 }' >&2
    exit 1
  fi

  migrated_ip="$(parent_ipv4_in_mgmt_subnet || true)"
  if [[ -n "$migrated_ip" && "$BR_MGMT_IP" == "10.1.132.10" ]]; then
    BR_MGMT_IP="$migrated_ip"
    info "migrating $PARENT address $migrated_ip/${MGMT_PREFIX} -> $BRIDGE"
  fi

  if ip -4 addr show dev "$PARENT" 2>/dev/null | grep -q 'inet '; then
    log "flush IPv4 on $PARENT (bridge port; IP lives on $BRIDGE)"
    run ip addr flush dev "$PARENT" 2>/dev/null || true
  fi

  run ip link set "$PARENT" up
}

assign_bridge_ip() {
  local br="$1" ip="$2" prefix="$3"
  run ip link set "$br" up
  if ip -4 addr show dev "$br" | grep -q "inet ${ip}/${prefix}"; then
    info "$br already has ${ip}/${prefix}"
    return 0
  fi
  run ip addr flush dev "$br" 2>/dev/null || true
  log "assign ${ip}/${prefix} on $br"
  run ip addr add "${ip}/${prefix}" dev "$br"
}

ensure_mgmt_route() {
  if ip route show "$MGMT_CIDR" 2>/dev/null | grep -q "dev $BRIDGE"; then
    info "route $MGMT_CIDR already uses $BRIDGE"
    return 0
  fi
  if ip route show "$MGMT_CIDR" 2>/dev/null | grep -q "dev $PARENT"; then
    log "replace $MGMT_CIDR route: $PARENT -> $BRIDGE"
    run ip route del "$MGMT_CIDR" dev "$PARENT" 2>/dev/null || true
  fi
  log "route $MGMT_CIDR dev $BRIDGE"
  run ip route add "$MGMT_CIDR" dev "$BRIDGE" 2>/dev/null || true
}

attach_parent() {
  local master
  master="$(iface_master "$PARENT")"
  if [[ "$master" == "$BRIDGE" ]]; then
    info "$PARENT already on $BRIDGE"
    return 0
  fi
  if [[ -n "$master" ]]; then
    err "$PARENT is a port on $master; run: $0 down"
    exit 1
  fi
  log "attach $PARENT -> $BRIDGE"
  run ip link set "$PARENT" master "$BRIDGE"
  run ip link set "$PARENT" up
}

ensure_bridge() {
  if ! bridge_exists; then
    log "create bridge $BRIDGE"
    run ip link add name "$BRIDGE" type bridge
  else
    info "bridge $BRIDGE already exists"
  fi

  configure_bridge_l2 "$BRIDGE"
  remove_legacy_macvlan
  attach_parent
  run ip link set "$BRIDGE" up
  assign_bridge_ip "$BRIDGE" "$BR_MGMT_IP" "$MGMT_PREFIX"
  ensure_mgmt_route
}

ensure_virsh() {
  command -v virsh >/dev/null 2>&1 || {
    err "virsh not found (install libvirt-clients)"
    exit 1
  }
}

vm_exists() { virsh dominfo "$1" &>/dev/null 2>&1; }
vm_running() { [[ "$(virsh domstate "$1" 2>/dev/null)" == "running" ]]; }

vm_on_bridge() {
  local vm="$1" br="$2"
  virsh domiflist "$vm" 2>/dev/null | awk -v br="$br" 'NR > 2 && $3 == br { found=1 } END { exit !found }'
}

vm_direct_on_parent() {
  local vm="$1"
  virsh domiflist "$vm" 2>/dev/null | awk -v p="$PARENT" 'NR > 2 && $2 == "direct" && $3 == p { print $1, $5; exit }'
}

detach_direct_parent_nic() {
  local vm="$1" line iface mac
  line="$(vm_direct_on_parent "$vm" || true)"
  [[ -n "$line" ]] || return 0
  iface="${line%% *}"
  mac="${line##* }"
  log "detach $vm direct $PARENT NIC $iface (mac $mac)"
  if vm_running "$vm"; then
    run virsh detach-interface "$vm" --type direct --mac "$mac" --config --live
  else
    run virsh detach-interface "$vm" --type direct --mac "$mac" --config
  fi
}

attach_vm_mgmt() {
  local vm="$1" mac="$2"
  local -a live_args=()

  if vm_on_bridge "$vm" "$BRIDGE"; then
    info "$vm already on $BRIDGE"
    return 0
  fi

  vm_exists "$vm" || {
    info "skip $vm (not defined in libvirt)"
    return 0
  }

  detach_direct_parent_nic "$vm"

  if vm_running "$vm"; then
    live_args=(--live)
  fi

  log "attach $vm -> $BRIDGE (mac $mac)"
  run virsh attach-interface "$vm" \
    --type bridge \
    --source "$BRIDGE" \
    --model virtio \
    --mac "$mac" \
    --config \
    "${live_args[@]}"
}

attach_all_vms() {
  ensure_virsh
  local vm mac
  for vm in "${ALL_MGMT_VMS[@]}"; do
    mac="${VM_MGMT_MAC[$vm]:-}"
    [[ -n "$mac" ]] || continue
    attach_vm_mgmt "$vm" "$mac"
  done
}

maybe_apply_guest_netplan() {
  local apply="${APPLY_NETPLAN:-0}"
  [[ "$apply" == 1 ]] || return 0
  if [[ ! -x "$SCRIPT_DIR/setup_ip.sh" ]]; then
    info "skip guest netplan (missing setup_ip.sh)"
    return 0
  fi
  log "apply guest netplan (setup_ip.sh)"
  if ! "$SCRIPT_DIR/setup_ip.sh"; then
    err "setup_ip.sh failed (SSH to guests required); re-run when VMs are reachable"
    return 1
  fi
}

cmd_verify() {
  need_root
  local ok=0

  if bridge_exists && [[ "$(iface_master "$PARENT")" == "$BRIDGE" ]]; then
    info "$PARENT enslaved to $BRIDGE: ok"
  else
    err "$PARENT not a port on $BRIDGE"
    ok=1
  fi

  if legacy_macvlan_exists; then
    err "legacy macvlan $LEGACY_MACVLAN still present"
    ok=1
  fi

  if ip -4 addr show dev "$BRIDGE" 2>/dev/null | grep -q "inet ${BR_MGMT_IP}/${MGMT_PREFIX}"; then
    info "host IP ${BR_MGMT_IP}/${MGMT_PREFIX} on $BRIDGE: ok"
  else
    err "host IP missing on $BRIDGE"
    ok=1
  fi

  if ip route show "$MGMT_CIDR" 2>/dev/null | grep -q "dev $BRIDGE"; then
    info "route $MGMT_CIDR dev $BRIDGE: ok"
  else
    err "route $MGMT_CIDR not via $BRIDGE"
    ok=1
  fi

  if [[ -n "$(parent_ipv4_in_mgmt_subnet || true)" ]]; then
    err "$PARENT still has an IP in $MGMT_CIDR (should be L2-only)"
    ok=1
  else
    info "$PARENT has no mgmt IPv4: ok"
  fi

  local -a ports
  mapfile -t ports < <(bridge_ports)
  if ((${#ports[@]} == 0)); then
    err "no ports on $BRIDGE"
    ok=1
  else
    info "bridge ports (${#ports[@]}): ${ports[*]}"
  fi

  if ping_via_bridge "$VERIFY_VM_IP"; then
    info "ping VM $VERIFY_VM_IP via $BRIDGE: ok"
  else
    err "ping VM $VERIFY_VM_IP via $BRIDGE: failed (check guest enp1s0 / setup_ip.sh)"
    ok=1
  fi

  if ping_via_bridge "$MGMT_GW"; then
    info "ping gateway $MGMT_GW via $BRIDGE: ok"
  elif [[ "$VERIFY_GW" == 1 ]]; then
    err "ping gateway $MGMT_GW via $BRIDGE: failed"
    ok=1
  else
    info "ping gateway $MGMT_GW via $BRIDGE: skipped"
  fi

  if command -v virsh >/dev/null 2>&1; then
    local vm attached=0 missing=0
    for vm in "${ALL_MGMT_VMS[@]}"; do
      vm_exists "$vm" || continue
      if vm_on_bridge "$vm" "$BRIDGE"; then
        attached=$((attached + 1))
      else
        missing=$((missing + 1))
        info "VM not on $BRIDGE: $vm"
      fi
    done
    info "VMs on $BRIDGE: $attached attached, $missing missing"
    (( missing > 0 )) && ok=1
  fi

  return "$ok"
}

cmd_setup() {
  local attach_vms=1 apply_netplan=0
  for arg in "$@"; do
    case "$arg" in
      --no-vms) attach_vms=0 ;;
      --apply-netplan) apply_netplan=1 ;;
      -h|--help) usage; exit 0 ;;
      *) err "unknown option: $arg"; usage >&2; exit 1 ;;
    esac
  done

  need_root
  APPLY_NETPLAN="$apply_netplan"

  log "Step 1 — prepare $PARENT (L2 bridge port, no IP on parent)"
  prepare_parent

  log "Step 2 — $BRIDGE + direct $PARENT port"
  ensure_bridge

  if (( attach_vms )); then
    log "Step 3 — attach Nephio VM mgmt NICs to $BRIDGE"
    attach_all_vms
  fi

  if [[ "$apply_netplan" == 1 ]]; then
    log "Step 4 — guest netplan"
    maybe_apply_guest_netplan || true
  fi

  echo
  log "Step final — verify"
  if cmd_verify; then
    log "setup complete"
  else
    err "setup finished with verification warnings"
    cmd_status
    exit 1
  fi
  echo
  cmd_status
}

cmd_up() { cmd_setup "$@"; }

cmd_down() {
  need_root

  remove_legacy_macvlan

  if [[ "$(iface_master "$PARENT")" == "$BRIDGE" ]]; then
    log "detach $PARENT from $BRIDGE"
    run ip link set "$PARENT" down 2>/dev/null || true
    run ip link set "$PARENT" nomaster 2>/dev/null || true
    run ip link set "$PARENT" up 2>/dev/null || true
  fi

  if bridge_exists; then
    log "remove $BRIDGE"
    run ip route del "$MGMT_CIDR" dev "$BRIDGE" 2>/dev/null || true
    run ip addr flush dev "$BRIDGE" 2>/dev/null || true
    run ip link set "$BRIDGE" down 2>/dev/null || true
    run ip link del "$BRIDGE" type bridge 2>/dev/null || true
  fi

  if command -v nmcli >/dev/null 2>&1; then
    run nmcli device set "$PARENT" managed yes 2>/dev/null || true
  fi

  log "teardown complete"
}

cmd_status() {
  need_root

  log "parent $PARENT"
  if parent_exists; then
    ip -br addr show "$PARENT" 2>/dev/null | sed 's/^/    /' || ip -br link show "$PARENT" | sed 's/^/    /'
    info "master: $(iface_master "$PARENT" || echo none)"
    info "macvtap/macvlan children on $PARENT: $(parent_upper_count)"
  else
    info "(missing)"
  fi

  if legacy_macvlan_exists; then
    log "legacy macvlan $LEGACY_MACVLAN (remove with setup)"
    ip -br link show "$LEGACY_MACVLAN" | sed 's/^/    /'
  fi

  log "bridge $BRIDGE"
  if bridge_exists; then
    ip -br addr show "$BRIDGE" 2>/dev/null | sed 's/^/    /' || true
    local -a ports
    mapfile -t ports < <(bridge_ports)
    if ((${#ports[@]})); then
      info "ports (${#ports[@]}): ${ports[*]}"
    else
      info "ports: (none)"
    fi
    info "route: $(ip route show "$MGMT_CIDR" 2>/dev/null | head -1 || echo none)"
  else
    info "(missing)"
  fi

  if command -v virsh >/dev/null 2>&1; then
    log "VM mgmt attachments"
    local vm
    for vm in "${ALL_MGMT_VMS[@]}"; do
      if ! vm_exists "$vm"; then
        info "  $vm: (undefined)"
        continue
      fi
      if vm_on_bridge "$vm" "$BRIDGE"; then
        info "  $vm: attached"
      else
        info "  $vm: not attached"
      fi
    done
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [setup|up|down|status|verify] [options]

Single-shot Nephio mgmt bridge setup on the hypervisor.

  setup, up   Full setup (default):
                1. Remove legacy macvlan ($LEGACY_MACVLAN) if present
                2. Flush mgmt IP from $PARENT; enslave $PARENT -> $BRIDGE
                3. Assign ${BR_MGMT_IP}/${MGMT_PREFIX} on $BRIDGE
                4. Route $MGMT_CIDR via $BRIDGE
                5. Attach all ${VM_PREFIX}* VM mgmt NICs (detach direct $PARENT first)

  down        Detach $PARENT, remove bridge and mgmt route
  status      Show bridge, routes, VM attachments
  verify      Check configuration (exit 1 on failure)

Options (setup/up):
  --no-vms           Host bridge only; skip libvirt VM attach
  --apply-netplan    Run scripts/setup_ip.sh after attach (needs SSH to guests)

Requires: no macvtap/macvlan children on $PARENT (use bridge NICs on $BRIDGE).

Environment:
  BRIDGE       default: br-mgmt
  PARENT       default: eno1
  BR_MGMT_IP   default: 10.1.132.10 (migrates from $PARENT if present)
  MGMT_CIDR    default: 10.1.132.0/24
  MGMT_GW      default: 10.1.132.1
  VERIFY_VM_IP default: 10.1.132.200
  VERIFY_GW    default: 1
  NEPHIO_VM_PREFIX  default: Nephio-

Docs: nephio/docs/mgmt.md
EOF
}

main() {
  local cmd="${1:-setup}"
  shift || true

  case "$cmd" in
    setup|up) cmd_setup "$@" ;;
    down) cmd_down "$@" ;;
    status) cmd_status ;;
    verify) need_root; cmd_verify ;;
    -h|--help|help) usage ;;
    *)
      err "unknown command: $cmd"
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
