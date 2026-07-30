#!/usr/bin/env bash
# Nephio testbed: per-site Linux bridges + vm-sw site switches + eno1 VLAN uplinks.
# Topology: docs/topology.md · nephio/docs/testbed.md
#
#   step 1: host bridges (br-int-* / br-ext-* + 10.1.137.x)
#   step 1b: eno1.{132,135,136,137} -> br-mgmt / br-int-{central,regional,edge}
#   step 2: libvirt vm-sw VMs join those bridges
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VM_SW_DIR="${SCRIPT_DIR}/vm-sw"
UPLINK_SCRIPT="${UPLINK_SCRIPT:-${REPO_ROOT}/scripts/setup_eno1_vlan_uplinks.sh}"

PHYSICAL_NIC="${PHYSICAL_NIC:-eno1}"
LEGACY_EDGE_NIC="${LEGACY_EDGE_NIC:-eno2}"
BR_MGMT="br-mgmt"

BR_INT_CENTRAL="br-int-central"
BR_INT_REGIONAL="br-int-regional"
BR_INT_EDGE="br-int-edge"
BR_INT_UE="br-int-ue"

BR_EXT_CR="br-ext-cr"
BR_EXT_RE="br-ext-re"
BR_EXT_EU="br-ext-eu"

BRIDGE_SUBNET_CIDR="10.1.137.0/24"
BRIDGE_PREFIX="24"
BR_INT_CENTRAL_IP="10.1.137.10"
BR_INT_REGIONAL_IP="10.1.137.11"
BR_INT_EDGE_IP="10.1.137.12"
BR_INT_UE_IP="10.1.137.13"
BR_EXT_CR_IP="10.1.137.20"
BR_EXT_RE_IP="10.1.137.21"
BR_EXT_EU_IP="10.1.137.22"

SW_CENTRAL="vm-sw-central"
SW_REGIONAL="vm-sw-regional"
SW_EDGE="vm-sw-edge"
SW_UE="vm-sw-ue"

ALL_SITE_SWITCHES=("$SW_CENTRAL" "$SW_REGIONAL" "$SW_EDGE" "$SW_UE")

ALL_BRIDGES=(
  "$BR_INT_CENTRAL" "$BR_INT_REGIONAL" "$BR_INT_EDGE" "$BR_INT_UE"
  "$BR_EXT_CR" "$BR_EXT_RE" "$BR_EXT_EU"
)

# VLAN uplinks on PHYSICAL_NIC (802.1Q); br-int-ue stays host-only.
VLAN_UPLINKS=(
  "132:${BR_MGMT}"
  "135:${BR_INT_CENTRAL}"
  "136:${BR_INT_REGIONAL}"
  "137:${BR_INT_EDGE}"
)

LEGACY_CONTAINERS=(
  container-sw-central container-sw-regional container-sw-edge container-sw-ue
)

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO=(sudo)
    else
      echo "Run as root or install sudo." >&2
      exit 1
    fi
  else
    SUDO=()
  fi
}

run() { "${SUDO[@]}" "$@"; }

assign_bridge_ip() {
  local br="$1" ip="$2"
  run ip link set "$br" up
  if ip -4 addr show dev "$br" | grep -q "inet ${ip}/${BRIDGE_PREFIX}"; then
    info "$br already has ${ip}/${BRIDGE_PREFIX}"
    return 0
  fi
  run ip addr flush dev "$br" 2>/dev/null || true
  log "Assign ${ip}/${BRIDGE_PREFIX} on $br (${BRIDGE_SUBNET_CIDR})"
  run ip addr add "${ip}/${BRIDGE_PREFIX}" dev "$br"
}

configure_bridge_l2() {
  local br="$1"
  ip link show "$br" &>/dev/null || return 0
  if [[ -f "/sys/class/net/${br}/bridge/stp_state" ]]; then
    echo 0 | run tee "/sys/class/net/${br}/bridge/stp_state" >/dev/null 2>&1 || true
  fi
  for knob in nf_call_iptables nf_call_ip6tables nf_call_arptables; do
    if [[ -f "/sys/class/net/${br}/bridge/${knob}" ]]; then
      echo 0 | run tee "/sys/class/net/${br}/bridge/${knob}" >/dev/null 2>&1 || true
    fi
  done
}

create_linux_bridge() {
  local br="$1" ip="$2" note="${3:-}"

  if ! ip link show "$br" &>/dev/null; then
    log "Creating bridge $br${note:+ ($note)}"
    run ip link add name "$br" type bridge
  else
    info "bridge $br already exists"
  fi
  configure_bridge_l2 "$br"
  assign_bridge_ip "$br" "$ip"
}

delete_bridge_ports() {
  local br="$1"
  ip link show "$br" &>/dev/null || return 0
  local ports
  ports="$(bridge link show dev "$br" 2>/dev/null | awk '{print $1}' | cut -d@ -f1 || true)"
  for port in $ports; do
    [[ -n "$port" ]] || continue
    run ip link del "$port" 2>/dev/null || true
  done
}

delete_bridge() {
  local br="$1"
  ip link show "$br" &>/dev/null || return 0
  delete_bridge_ports "$br"
  run ip link set "$br" down 2>/dev/null || true
  run ip link del "$br" type bridge 2>/dev/null || true
}

create_internal_bridges() {
  log "Step 1 — internal site bridges (${BRIDGE_SUBNET_CIDR})"
  create_linux_bridge "$BR_INT_CENTRAL" "$BR_INT_CENTRAL_IP" "Central-0 / Central-1"
  create_linux_bridge "$BR_INT_REGIONAL" "$BR_INT_REGIONAL_IP" "Regional-0 / Regional-1"
  create_linux_bridge "$BR_INT_EDGE" "$BR_INT_EDGE_IP" "Edge-0 / Edge-1"
  create_linux_bridge "$BR_INT_UE" "$BR_INT_UE_IP" "UE-0 / UE-1"
}

create_interconnect_bridges() {
  log "Step 1 — interconnect bridges (${BRIDGE_SUBNET_CIDR})"
  create_linux_bridge "$BR_EXT_CR" "$BR_EXT_CR_IP" "central lower <-> regional upper"
  create_linux_bridge "$BR_EXT_RE" "$BR_EXT_RE_IP" "regional lower <-> edge upper"
  create_linux_bridge "$BR_EXT_EU" "$BR_EXT_EU_IP" "edge lower <-> UE upper"
}

setup_vlan_uplinks() {
  if [[ ! -x "$UPLINK_SCRIPT" ]]; then
    echo "Missing VLAN uplink script: $UPLINK_SCRIPT" >&2
    exit 1
  fi
  log "Step 1b — VLAN uplinks on ${PHYSICAL_NIC} (detach ${LEGACY_EDGE_NIC})"
  PHYSICAL="$PHYSICAL_NIC" REMOVE_ENO2="$LEGACY_EDGE_NIC" "$UPLINK_SCRIPT" setup
}

teardown_vlan_uplinks() {
  [[ -x "$UPLINK_SCRIPT" ]] || return 0
  log "Removing VLAN uplinks on ${PHYSICAL_NIC}"
  PHYSICAL="$PHYSICAL_NIC" "$UPLINK_SCRIPT" down || true
}

start_site_switches() {
  log "Step 2 — vm-sw site switches"
  if [[ ! -x "${VM_SW_DIR}/bringup.sh" ]]; then
    echo "Missing ${VM_SW_DIR}/bringup.sh" >&2
    exit 1
  fi
  (cd "$VM_SW_DIR" && ./bringup.sh up)
}

stop_site_switches() {
  if [[ -x "${VM_SW_DIR}/bringup.sh" ]]; then
    (cd "$VM_SW_DIR" && ./bringup.sh down "$@") || true
  fi
}

stop_legacy_containers() {
  command -v docker >/dev/null 2>&1 || return 0
  local c
  for c in "${LEGACY_CONTAINERS[@]}"; do
    docker rm -f "$c" 2>/dev/null || true
  done
  if [[ -d "${SCRIPT_DIR}/container-sw" ]]; then
    (cd "${SCRIPT_DIR}/container-sw" && docker compose down 2>/dev/null) || true
  fi
}

remove_legacy_docker_networks() {
  command -v docker >/dev/null 2>&1 || return 0
  local br
  for br in "${ALL_BRIDGES[@]}"; do
    docker network rm "$br" 2>/dev/null || true
  done
}

print_topology() {
  log "Topology (docs/topology.md)"
  cat <<EOF
  Physical (${PHYSICAL_NIC} 802.1Q trunk):
    ${PHYSICAL_NIC}.132 -> $BR_MGMT (10.1.132.0/24 mgmt)
    ${PHYSICAL_NIC}.135 -> $BR_INT_CENTRAL
    ${PHYSICAL_NIC}.136 -> $BR_INT_REGIONAL
    ${PHYSICAL_NIC}.137 -> $BR_INT_EDGE
    $BR_INT_UE: host-only (no VLAN uplink)

  Central:  VM enp7s0 -> $BR_INT_CENTRAL -> $SW_CENTRAL -> $BR_EXT_CR
  Regional: VM enp7s0 -> $BR_INT_REGIONAL -> $SW_REGIONAL -> $BR_EXT_CR, $BR_EXT_RE
  Edge:     VM enp7s0 -> $BR_INT_EDGE -> $SW_EDGE -> $BR_EXT_RE, $BR_EXT_EU
  UE:       VM enp7s0 -> $BR_INT_UE -> $SW_UE -> $BR_EXT_EU
  Mgmt:     vm-sw inf-mgmt -> libvirt default NAT (virbr0, ~192.168.122.0/24)
EOF
}

print_bridge_status() {
  log "Bridge status (${BRIDGE_SUBNET_CIDR})"
  for br in "${ALL_BRIDGES[@]}"; do
    if ip link show "$br" &>/dev/null; then
      local addr
      addr="$(ip -4 -o addr show dev "$br" 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
      info "$br: ${addr:-no IPv4} ports: $(bridge link show dev "$br" 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
    else
      info "$br: (missing)"
    fi
  done
}

print_uplink_status() {
  log "VLAN uplinks (${PHYSICAL_NIC})"
  if [[ -x "$UPLINK_SCRIPT" ]]; then
    PHYSICAL="$PHYSICAL_NIC" REMOVE_ENO2="$LEGACY_EDGE_NIC" "$UPLINK_SCRIPT" status || true
    return 0
  fi
  local entry vid br iface master
  for entry in "${VLAN_UPLINKS[@]}"; do
    IFS=: read -r vid br <<<"$entry"
    iface="${PHYSICAL_NIC}.${vid}"
    if ip link show "$iface" &>/dev/null; then
      master="$(readlink -f "/sys/class/net/${iface}/master" 2>/dev/null | xargs basename 2>/dev/null || true)"
      info "$iface -> ${master:-none} (expected $br)"
    else
      info "$iface -> (missing)"
    fi
  done
}

print_vm_status() {
  log "Site switches (vm-sw)"
  if [[ -x "${VM_SW_DIR}/bringup.sh" ]]; then
    (cd "$VM_SW_DIR" && ./bringup.sh status) || true
  fi
}

cmd_up_bridges() {
  local skip_uplinks="${1:-0}"
  log "Step 1: bridges"
  create_internal_bridges
  create_interconnect_bridges
  if (( ! skip_uplinks )); then
    setup_vlan_uplinks
  fi
}

cmd_up_vms() {
  local skip_uplinks="${1:-0}"
  log "Step 2: vm-sw"
  local missing=0
  local br
  for br in "${ALL_BRIDGES[@]}"; do
    ip link show "$br" &>/dev/null || missing=1
  done
  if (( missing )); then
    log "Bridges missing — running step 1"
    cmd_up_bridges "$skip_uplinks"
  fi
  start_site_switches
}

cmd_up() {
  local mode="${1:-all}"
  local skip_uplinks="${2:-0}"
  need_root

  case "$mode" in
    bridges) cmd_up_bridges "$skip_uplinks" ;;
    vms) cmd_up_vms "$skip_uplinks" ;;
    uplinks)
      setup_vlan_uplinks
      ;;
    all)
      cmd_up_bridges "$skip_uplinks"
      start_site_switches
      ;;
    *)
      echo "Unknown up mode: $mode" >&2
      exit 1
      ;;
  esac

  echo
  print_topology
  echo
  print_bridge_status
  if (( ! skip_uplinks )) || [[ "$mode" == uplinks ]]; then
    echo
    print_uplink_status
  fi
  echo
  print_vm_status
}

parse_up_mode() {
  local bridges=0 vms=0 uplinks=0 skip_uplinks=0
  for arg in "$@"; do
    case "$arg" in
      --bridges) bridges=1 ;;
      --vms|--vm-sw) vms=1 ;;
      --uplinks) uplinks=1 ;;
      --no-uplinks) skip_uplinks=1 ;;
      --containers)
        echo "Unknown option: --containers (use --vms or --vm-sw)" >&2
        exit 1
        ;;
      *)
        echo "Unknown option for up: $arg" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  local selected=0
  (( bridges )) && selected=$((selected + 1))
  (( vms )) && selected=$((selected + 1))
  (( uplinks )) && selected=$((selected + 1))
  if (( selected > 1 )); then
    echo "Use only one of --bridges, --uplinks, or --vms, or omit all for full bringup." >&2
    exit 1
  fi

  local mode=all
  if (( bridges )); then
    mode=bridges
  elif (( vms )); then
    mode=vms
  elif (( uplinks )); then
    mode=uplinks
  fi

  printf '%s:%s\n' "$mode" "$skip_uplinks"
}

cmd_down() {
  local wipe=0 skip_uplinks=0 arg
  for arg in "$@"; do
    case "$arg" in
      --wipe) wipe=1 ;;
      --no-uplinks) skip_uplinks=1 ;;
      *)
        echo "Unknown option for down: $arg" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  need_root
  if (( wipe )); then
    stop_site_switches --wipe
  else
    stop_site_switches
  fi
  stop_legacy_containers
  remove_legacy_docker_networks

  if (( ! skip_uplinks )); then
    teardown_vlan_uplinks
  fi

  log "Removing host bridges"
  for br in "${ALL_BRIDGES[@]}"; do
    delete_bridge "$br"
  done

  log "Teardown complete"
}

cmd_status() {
  print_topology
  echo
  print_bridge_status
  echo
  print_uplink_status
  echo
  print_vm_status
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  up [--bridges | --uplinks | --vms] [--no-uplinks]
      Bring up testbed (default: bridges + VLAN uplinks + vm-sw)
  down [--wipe] [--no-uplinks]
      Remove vm-sw VMs and host bridges (keeps vm disks by default)
  status                  Show topology and state

Staged bringup:
  Step 1   up --bridges     Create br-int-* / br-ext-* + 10.1.137.x on host
  Step 1b  up --uplinks     ${PHYSICAL_NIC}.{132,135,136,137} -> site bridges
                           (detach ${LEGACY_EDGE_NIC}; see scripts/setup_eno1_vlan_uplinks.sh)
  Step 2   up --vms         libvirt vm-sw VMs (creates bridges first if missing)
  up                      Run step 1 + 1b + 2

  --no-uplinks            Skip VLAN uplink setup/teardown (host-only L2 lab)

Site switches: ${ALL_SITE_SWITCHES[*]}
Requires: ip, bridge, 8021q, libvirt (virsh, virt-install), sudo (unless root)
EOF
}

main() {
  local cmd="${1:-up}"
  shift || true

  case "$cmd" in
    up)
      local parsed mode skip_uplinks
      parsed="$(parse_up_mode "$@")"
      mode="${parsed%%:*}"
      skip_uplinks="${parsed##*:}"
      cmd_up "$mode" "$skip_uplinks"
      ;;
    down) cmd_down "$@" ;;
    status) cmd_status ;;
    -h|--help|help) usage ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
