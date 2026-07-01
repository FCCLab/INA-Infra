#!/usr/bin/env bash
# Attach Nephio workload VMs to testbed site bridges (br-int-*).
# Topology: testbed/readme.md
#
#   Nephio-Central-{0,1}  eth* -> br-int-central
#   Nephio-Regional-{0,1} eth* -> br-int-regional
#   Nephio-Edge-{0,1}     eth* -> br-int-edge
#   Nephio-UE-{0,1}       eth* -> br-int-ue
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VM_PREFIX="${NEPHIO_VM_PREFIX:-Nephio-}"

BR_INT_CENTRAL="br-int-central"
BR_INT_REGIONAL="br-int-regional"
BR_INT_EDGE="br-int-edge"
BR_INT_UE="br-int-ue"

ALL_SITES=(central regional edge ue)

declare -A SITE_BRIDGE=(
  [central]="$BR_INT_CENTRAL"
  [regional]="$BR_INT_REGIONAL"
  [edge]="$BR_INT_EDGE"
  [ue]="$BR_INT_UE"
)

declare -A SITE_VMS=(
  [central]="Central-0 Central-1"
  [regional]="Regional-0 Regional-1"
  [edge]="Edge-0 Edge-1"
  [ue]="UE-0 UE-1"
)

log() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }

ensure_virsh() {
  command -v virsh >/dev/null 2>&1 || {
    echo "virsh not found (install libvirt-clients)." >&2
    exit 1
  }
}

vm_name() {
  printf '%s%s' "$VM_PREFIX" "$1"
}

# Stable per-site / per-node MAC (52:54:00 is QEMU/KVM OUI).
# Site byte matches bridge host octet: central=10 (0x0a) … ue=13 (0x0d).
site_mac() {
  local site="$1" node="$2"
  local site_byte node_byte
  case "$site" in
    central) site_byte=0x0a ;;
    regional) site_byte=0x0b ;;
    edge) site_byte=0x0c ;;
    ue) site_byte=0x0d ;;
    *)
      echo "unknown site: $site" >&2
      return 1
      ;;
  esac
  node_byte=$((node + 1))
  printf '52:54:00:%02x:00:%02x' "$site_byte" "$node_byte"
}

bridge_exists() {
  ip link show "$1" &>/dev/null
}

vm_exists() {
  virsh dominfo "$1" &>/dev/null 2>&1
}

vm_running() {
  [[ "$(virsh domstate "$1" 2>/dev/null)" == "running" ]]
}

vm_attached_to_bridge() {
  local vm="$1" bridge="$2"
  virsh domiflist "$vm" 2>/dev/null | awk 'NR>2 && $3 == "'"$bridge"'" { found=1 } END { exit !found }'
}

ensure_bridge() {
  local bridge="$1"
  if bridge_exists "$bridge"; then
    return 0
  fi
  echo "Bridge $bridge not found. Run: sudo ${SCRIPT_DIR}/bringup_switches.sh up --bridges" >&2
  exit 1
}

attach_vm_nic() {
  local vm="$1" bridge="$2" mac="$3"
  local -a live_args=()

  if vm_attached_to_bridge "$vm" "$bridge"; then
    info "$vm already attached to $bridge"
    return 0
  fi

  if vm_running "$vm"; then
    live_args=(--live)
  fi

  log "Attach $vm -> $bridge (mac $mac)"
  virsh attach-interface "$vm" \
    --type bridge \
    --source "$bridge" \
    --model virtio \
    --mac "$mac" \
    --config \
    "${live_args[@]}"
}

detach_vm_nic() {
  local vm="$1" bridge="$2"
  local iface type source

  if ! vm_attached_to_bridge "$vm" "$bridge"; then
    info "$vm has no NIC on $bridge"
    return 0
  fi

  iface="$(virsh domiflist "$vm" 2>/dev/null | awk -v br="$bridge" 'NR>2 && $3 == br { print $1; exit }')"
  [[ -n "$iface" ]] || return 0

  local mac
  mac="$(virsh domiflist "$vm" | awk -v ifc="$iface" 'NR>2 && $1 == ifc { print $5; exit }')"
  log "Detach $vm NIC $iface ($bridge, mac $mac)"
  if vm_running "$vm"; then
    virsh detach-interface "$vm" --type bridge --mac "$mac" --config --live
  else
    virsh detach-interface "$vm" --type bridge --mac "$mac" --config
  fi
}

attach_site() {
  local site="$1" bridge vm node vm_short mac
  bridge="${SITE_BRIDGE[$site]}"
  ensure_bridge "$bridge"

  for vm_short in ${SITE_VMS[$site]}; do
    vm="$(vm_name "$vm_short")"
    node="${vm_short##*-}"
    if ! vm_exists "$vm"; then
      info "skip $vm (undefined in libvirt)"
      continue
    fi
    mac="$(site_mac "$site" "$node")"
    attach_vm_nic "$vm" "$bridge" "$mac"
  done
}

detach_site() {
  local site="$1" bridge vm vm_short
  bridge="${SITE_BRIDGE[$site]}"

  for vm_short in ${SITE_VMS[$site]}; do
    vm="$(vm_name "$vm_short")"
    vm_exists "$vm" || continue
    detach_vm_nic "$vm" "$bridge"
  done
}

status_site() {
  local site="$1" bridge vm vm_short
  bridge="${SITE_BRIDGE[$site]}"
  printf '%s (%s):\n' "$site" "$bridge"
  for vm_short in ${SITE_VMS[$site]}; do
    vm="$(vm_name "$vm_short")"
    if ! vm_exists "$vm"; then
      info "  $vm: (undefined)"
      continue
    fi
    if vm_attached_to_bridge "$vm" "$bridge"; then
      info "  $vm: attached"
    else
      info "  $vm: not attached"
    fi
  done
}

parse_sites() {
  local -a sites=() arg site
  if (($# == 0)); then
    sites=("${ALL_SITES[@]}")
  else
    for arg in "$@"; do
      IFS=',' read -ra parts <<<"$arg"
      for site in "${parts[@]}"; do
        site="${site// /}"
        site="${site,,}"
        case "$site" in
          central|regional|edge|ue) sites+=("$site") ;;
          "")
            ;;
          *)
            echo "Unknown site: $site (expected central, regional, edge, or ue)" >&2
            exit 1
            ;;
        esac
      done
    done
  fi
  if ((${#sites[@]} == 0)); then
    sites=("${ALL_SITES[@]}")
  fi
  printf '%s\n' "${sites[@]}"
}

cmd_attach() {
  ensure_virsh
  local -a sites
  mapfile -t sites < <(parse_sites "$@")
  local site
  for site in "${sites[@]}"; do
    attach_site "$site"
  done
}

cmd_detach() {
  ensure_virsh
  local -a sites
  mapfile -t sites < <(parse_sites "$@")
  local site
  for site in "${sites[@]}"; do
    detach_site "$site"
  done
}

cmd_status() {
  ensure_virsh
  local -a sites
  mapfile -t sites < <(parse_sites "$@")
  local site
  for site in "${sites[@]}"; do
    status_site "$site"
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <attach|detach|status> [sites...]

Attach Nephio workload VM NICs to testbed internal bridges:

  central  -> $BR_INT_CENTRAL  (${VM_PREFIX}Central-0, ${VM_PREFIX}Central-1)
  regional -> $BR_INT_REGIONAL (${VM_PREFIX}Regional-0, ${VM_PREFIX}Regional-1)
  edge     -> $BR_INT_EDGE     (${VM_PREFIX}Edge-0, ${VM_PREFIX}Edge-1)
  ue       -> $BR_INT_UE       (${VM_PREFIX}UE-0, ${VM_PREFIX}UE-1)

Sites may be listed as separate args or comma-separated:
  $(basename "$0") attach central regional edge ue
  $(basename "$0") attach central,regional,edge,ue

Omit sites to operate on all four. Requires host bridges from:
  sudo ${SCRIPT_DIR}/bringup_switches.sh up --bridges

Environment:
  NEPHIO_VM_PREFIX   libvirt domain prefix (default: Nephio-)
EOF
}

main() {
  local cmd="${1:-attach}"
  shift || true

  case "$cmd" in
    attach) cmd_attach "$@" ;;
    detach) cmd_detach "$@" ;;
    status) cmd_status "$@" ;;
    -h|--help|help) usage ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
