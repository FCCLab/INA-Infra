#!/usr/bin/env bash
# Nephio testbed: per-site Linux bridges + vm-sw site switches.
# Topology: testbed/readme.md
#
#   step 1: host bridges (br-int-* / br-ext-* + 10.1.137.x)
#   step 2: libvirt vm-sw VMs join those bridges
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_SW_DIR="${SCRIPT_DIR}/vm-sw"

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
  log "Topology (testbed/readme.md)"
  cat <<EOF
  Central:  VM eth0 -> $BR_INT_CENTRAL -> $SW_CENTRAL -> $BR_EXT_CR
  Regional: VM eth0 -> $BR_INT_REGIONAL -> $SW_REGIONAL -> $BR_EXT_CR, $BR_EXT_RE
  Edge:     VM eth0 -> $BR_INT_EDGE -> $SW_EDGE -> $BR_EXT_RE, $BR_EXT_EU
  UE:       VM eth0 -> $BR_INT_UE -> $SW_UE -> $BR_EXT_EU
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

print_vm_status() {
  log "Site switches (vm-sw)"
  if [[ -x "${VM_SW_DIR}/bringup.sh" ]]; then
    (cd "$VM_SW_DIR" && ./bringup.sh status) || true
  fi
}

cmd_up_bridges() {
  log "Step 1: bridges"
  create_internal_bridges
  create_interconnect_bridges
}

cmd_up_vms() {
  log "Step 2: vm-sw"
  local missing=0
  local br
  for br in "${ALL_BRIDGES[@]}"; do
    ip link show "$br" &>/dev/null || missing=1
  done
  if (( missing )); then
    log "Bridges missing — running step 1"
    cmd_up_bridges
  fi
  start_site_switches
}

cmd_up() {
  local mode="${1:-all}"
  need_root

  case "$mode" in
    bridges) cmd_up_bridges ;;
    vms) cmd_up_vms ;;
    all)
      cmd_up_bridges
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
  echo
  print_vm_status
}

parse_up_mode() {
  local bridges=0 vms=0
  for arg in "$@"; do
    case "$arg" in
      --bridges) bridges=1 ;;
      --vms|--vm-sw) vms=1 ;;
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

  if (( bridges && vms )); then
    echo "Use only one of --bridges or --vms, or omit both for full bringup." >&2
    exit 1
  fi
  if (( bridges )); then
    echo bridges
  elif (( vms )); then
    echo vms
  else
    echo all
  fi
}

cmd_down() {
  local wipe=0 arg
  for arg in "$@"; do
    case "$arg" in
      --wipe) wipe=1 ;;
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
  print_vm_status
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  up [--bridges | --vms]  Bring up testbed (default: both steps)
  down [--wipe]            Remove vm-sw VMs and host bridges (keeps vm disks by default)
  status                  Show topology and state

Staged bringup:
  Step 1  up --bridges     Create br-int-* / br-ext-* + 10.1.137.x on host
  Step 2  up --vms         libvirt vm-sw VMs (creates bridges first if missing)
  up                       Run step 1 then step 2

Site switches: ${ALL_SITE_SWITCHES[*]}
Requires: ip, bridge, libvirt (virsh, virt-install), sudo (unless root)
EOF
}

main() {
  local cmd="${1:-up}"
  shift || true

  case "$cmd" in
    up) cmd_up "$(parse_up_mode "$@")" ;;
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
