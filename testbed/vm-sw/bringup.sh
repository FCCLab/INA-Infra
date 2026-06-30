#!/usr/bin/env bash
# vm-sw: per-site L2 switch VMs (libvirt) bridging host br-int-* / br-ext-* ports.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUEST_BRIDGE="${SCRIPT_DIR}/guest-bridge.sh"
GUEST_MGMT="${SCRIPT_DIR}/guest-mgmt.sh"
GUEST_LATENCY="${SCRIPT_DIR}/guest-latency.sh"
# System libvirt cannot read ~/…; store disks where qemu can access them.
IMAGE_DIR="${VM_SW_IMAGE_DIR:-/var/lib/libvirt/images/vm-sw}"
BASE_IMAGE="${VM_SW_BASE_IMAGE:-${IMAGE_DIR}/alpine-cloud.qcow2}"
ALPINE_MIRROR="${VM_SW_ALPINE_MIRROR:-https://dl-cdn.alpinelinux.org/alpine}"
ALPINE_RELEASE="${VM_SW_ALPINE_RELEASE:-latest-stable}"
# generic bios + cloud-init (NoCloud seed ISO via cloud-localds)
ALPINE_IMAGE_NAME="${VM_SW_ALPINE_IMAGE:-}"
ALPINE_IMAGE_URL="${VM_SW_ALPINE_IMAGE_URL:-}"

VM_RAM_MB="${VM_SW_RAM_MB:-512}"
VM_VCPUS="${VM_SW_VCPUS:-1}"
# Bump when the provisioning recipe changes incompatibly.
BUILD_RECIPE_VERSION=12

LIBVIRT_MGMT_NET="${VM_SW_MGMT_NETWORK:-default}"

SW_CENTRAL="vm-sw-central"
SW_REGIONAL="vm-sw-regional"
SW_EDGE="vm-sw-edge"
SW_UE="vm-sw-ue"

ALL_VMS=("$SW_CENTRAL" "$SW_REGIONAL" "$SW_EDGE" "$SW_UE")
JUST_BUILT_VMS=()

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

libvirt_qemu_user() {
  if getent passwd libvirt-qemu >/dev/null 2>&1; then
    echo libvirt-qemu
  elif getent passwd qemu >/dev/null 2>&1; then
    echo qemu
  else
    echo root
  fi
}

fix_image_permissions() {
  local user grp
  user="$(libvirt_qemu_user)"
  grp="$(id -gn "$user" 2>/dev/null || echo "$user")"
  run mkdir -p "$IMAGE_DIR"
  run chown -R "${user}:${grp}" "$IMAGE_DIR"
  run chmod 755 "$IMAGE_DIR"
  run find "$IMAGE_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
}

tool_package() {
  case "$1" in
    virsh) echo libvirt-clients ;;
    virt-install) echo virtinst ;;
    qemu-img) echo qemu-utils ;;
    cloud-localds) echo cloud-image-utils ;;
    curl) echo curl ;;
    *) return 1 ;;
  esac
}

ensure_tools() {
  local t pkg missing_pkgs=() pkgs=()
  for t in virsh virt-install qemu-img cloud-localds curl; do
    command -v "$t" >/dev/null 2>&1 && continue
    pkg="$(tool_package "$t")" || {
      echo "No package mapping for missing tool: $t" >&2
      exit 1
    }
    missing_pkgs+=("$t")
    pkgs+=("$pkg")
  done
  if ((${#pkgs[@]} == 0)); then
    return 0
  fi

  log "Installing vm-sw dependencies: ${missing_pkgs[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    run apt-get update -qq
    run apt-get install -y --no-install-recommends "${pkgs[@]}"
  elif command -v dnf >/dev/null 2>&1; then
    local dnf_pkgs=()
    for t in "${missing_pkgs[@]}"; do
      case "$t" in
        virsh) dnf_pkgs+=(libvirt-client) ;;
        virt-install) dnf_pkgs+=(virt-install) ;;
        qemu-img) dnf_pkgs+=(qemu-img) ;;
        cloud-localds) dnf_pkgs+=(cloud-utils) ;;
        curl) dnf_pkgs+=(curl) ;;
      esac
    done
    run dnf install -y "${dnf_pkgs[@]}"
  else
    echo "Missing tools (${missing_pkgs[*]}); install packages for your distro." >&2
    exit 1
  fi

  for t in "${missing_pkgs[@]}"; do
    command -v "$t" >/dev/null 2>&1 || {
      echo "Still missing $t after install." >&2
      exit 1
    }
  done
}

alpine_cloud_index_url() {
  printf '%s/%s/releases/cloud/' "$ALPINE_MIRROR" "$ALPINE_RELEASE"
}

resolve_alpine_image_name() {
  if [[ -n "$ALPINE_IMAGE_NAME" ]]; then
    printf '%s\n' "$ALPINE_IMAGE_NAME"
    return 0
  fi
  curl -fsSL "$(alpine_cloud_index_url)" \
    | grep -oE 'generic_alpine-[0-9.]+-x86_64-bios-cloudinit-r0\.qcow2' \
    | sort -V \
    | tail -1
}

resolve_alpine_image_url() {
  if [[ -n "$ALPINE_IMAGE_URL" ]]; then
    printf '%s\n' "$ALPINE_IMAGE_URL"
    return 0
  fi
  local name
  name="$(resolve_alpine_image_name)" || true
  if [[ -z "$name" ]]; then
    echo "Could not find generic Alpine cloud-init qcow2 under $(alpine_cloud_index_url)" >&2
    exit 1
  fi
  printf '%s/%s\n' "$(alpine_cloud_index_url)" "$name"
}

ensure_base_image() {
  fix_image_permissions
  if [[ -f "$BASE_IMAGE" ]]; then
    info "Base image $BASE_IMAGE"
    return 0
  fi
  local url
  url="$(resolve_alpine_image_url)"
  log "Downloading Alpine cloud image to $BASE_IMAGE"
  info "$(basename "$url")"
  run curl -fsSL -o "$BASE_IMAGE" "$url"
  fix_image_permissions
}

vm_disk() {
  printf '%s/%s.qcow2' "$IMAGE_DIR" "$1"
}

vm_seed() {
  printf '%s/%s-seed.iso' "$IMAGE_DIR" "$1"
}

vm_build_stamp() {
  printf '%s/%s.build' "$IMAGE_DIR" "$1"
}

vm_disk_built() {
  [[ -f "$(vm_disk "$1")" ]]
}

file_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

cloud_init_user_data() {
  local name="$1" sw_renames="$2" sw_mgmt_rename="${3:-}" sw_latency="${4:-}"
  local mgmt_write_files="" mgmt_runcmd="" mgmt_packages=""
  local latency_write_files="" latency_runcmd=""

  if [[ -n "$sw_mgmt_rename" ]]; then
    mgmt_packages="
  - openssh"
    mgmt_write_files="
  - path: /usr/local/sbin/guest-mgmt.sh
    permissions: '0755'
    content: |
$(sed 's/^/      /' "$GUEST_MGMT")
  - path: /etc/local.d/vm-sw-mgmt.start
    permissions: '0755'
    content: |
      #!/bin/sh
      export SW_MGMT_RENAME=\"${sw_mgmt_rename}\"
      /usr/local/sbin/guest-mgmt.sh"
    mgmt_runcmd="
  - /etc/local.d/vm-sw-mgmt.start
  - rc-update add sshd default 2>/dev/null || true
  - rc-service sshd start 2>/dev/null || true"
  fi

  if [[ -n "$sw_latency" ]]; then
    latency_write_files="
  - path: /usr/local/sbin/guest-latency.sh
    permissions: '0755'
    content: |
$(sed 's/^/      /' "$GUEST_LATENCY")
  - path: /etc/local.d/vm-sw-z-latency.start
    permissions: '0755'
    content: |
      #!/bin/sh
      # Runs after bridge + mgmt (alphabetically last vm-sw-*.start).
      PATH=\"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
      export PATH
      export SW_LATENCY=\"${sw_latency}\"
      apk add --no-cache iproute2 kmod 2>/dev/null || true
      modprobe ifb numifbs=2 2>/dev/null || modprobe ifb 2>/dev/null || true
      sleep 2
      SW_LATENCY_PHASE=bridge /usr/local/sbin/guest-latency.sh || true"
    latency_runcmd="
  - /etc/local.d/vm-sw-z-latency.start"
  fi

  cat <<EOF
#cloud-config
hostname: ${name}
manage_etc_hosts: true
ssh_pwauth: true
chpasswd:
  list: |
    sw:sw
  expire: false
users:
  - name: sw
    gecos: vm-sw
    lock_passwd: false
    plain_text_passwd: sw
    groups: wheel
    shell: /bin/ash
package_update: true
packages:
  - bridge-utils
  - iproute2
  - kmod
  - htop
  - nload
  - iftop${mgmt_packages}
bootcmd:
  - grep -q '^ttyS0:' /etc/inittab || echo 'ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100' >> /etc/inittab
  - kill -HUP 1 2>/dev/null || true
write_files:
  - path: /etc/securetty
    append: true
    content: |
      ttyS0
  - path: /usr/local/sbin/guest-bridge.sh
    permissions: '0755'
    content: |
$(sed 's/^/      /' "$GUEST_BRIDGE")
  - path: /etc/local.d/vm-sw-bridge.start
    permissions: '0755'
    content: |
      #!/bin/sh
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      export PATH
      export SW_RENAMES="${sw_renames}"
      export SW_LATENCY="${sw_latency}"
      /usr/local/sbin/guest-bridge.sh
  - path: /etc/local.d/vm-sw-console.start
    permissions: '0755'
    content: |
      #!/bin/sh
      grep -q '^ttyS0:' /etc/inittab || echo 'ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100' >> /etc/inittab
      kill -HUP 1 2>/dev/null || true${mgmt_write_files}${latency_write_files}
runcmd:
  - echo 'sw:sw' | chpasswd
  - rc-update add local default 2>/dev/null || true
  - /etc/local.d/vm-sw-console.start
  - /etc/local.d/vm-sw-bridge.start${mgmt_runcmd}${latency_runcmd}
EOF
}

vm_build_fingerprint() {
  local name="$1"
  shift
  local -a bridges=("$@")
  local sw_renames="${VM_RENAMES[$name]}"
  local sw_mgmt="${VM_MGMT_RENAME[$name]:-}"
  local sw_latency="${VM_LATENCY[$name]:-}"
  local base_hash guest_hash mgmt_hash latency_hash cloud_hash bridges_str="${bridges[*]}"

  base_hash="$(file_sha256 "$BASE_IMAGE")"
  guest_hash="$(file_sha256 "$GUEST_BRIDGE")"
  mgmt_hash=""
  latency_hash=""
  if [[ -f "$GUEST_MGMT" ]]; then
    mgmt_hash="$(file_sha256 "$GUEST_MGMT")"
  fi
  if [[ -f "$GUEST_LATENCY" ]]; then
    latency_hash="$(file_sha256 "$GUEST_LATENCY")"
  fi
  cloud_hash="$(cloud_init_user_data "$name" "$sw_renames" "$sw_mgmt" "$sw_latency" | sha256sum | awk '{print $1}')"

  printf '%s\n' \
    "$BUILD_RECIPE_VERSION" \
    "$base_hash" \
    "$guest_hash" \
    "$mgmt_hash" \
    "$latency_hash" \
    "$cloud_hash" \
    "$sw_renames" \
    "$sw_mgmt" \
    "$sw_latency" \
    "$bridges_str" \
    "$LIBVIRT_MGMT_NET" \
    "$VM_RAM_MB" \
    "$VM_VCPUS" \
    | sha256sum | awk '{print $1}'
}

vm_read_build_stamp() {
  local stamp
  stamp="$(vm_build_stamp "$1")"
  [[ -f "$stamp" ]] && cat "$stamp" || true
}

vm_write_build_stamp() {
  local name="$1" fp="$2"
  echo "$fp" >"$(vm_build_stamp "$name")"
  fix_image_permissions
}

vm_disk_current() {
  local name="$1"
  shift
  local -a bridges=("$@")
  local stored fp

  vm_disk_built "$name" || return 1
  stored="$(vm_read_build_stamp "$name")"
  [[ -n "$stored" ]] || return 1
  fp="$(vm_build_fingerprint "$name" "${bridges[@]}")"
  [[ "$stored" == "$fp" ]]
}

vm_wipe_artifacts() {
  local name="$1"
  rm -f "$(vm_disk "$name")" "$(vm_seed "$name")" "$(vm_build_stamp "$name")"
}

destroy_vm() {
  local name="$1"
  if vm_exists "$name"; then
    log "Destroying $name"
    run virsh destroy "$name" 2>/dev/null || true
    run virsh undefine "$name" 2>/dev/null || true
  fi
}

ensure_vm_image() {
  local name="$1"
  shift
  local -a bridges=("$@")
  local disk fp

  if vm_disk_current "$name" "${bridges[@]}"; then
    info "Reusing $name disk (build fingerprint unchanged)"
    return 0
  fi

  if vm_disk_built "$name"; then
    log "Build inputs changed for $name — rebuilding overlay (like docker rebuild)"
  fi

  destroy_vm "$name"
  vm_wipe_artifacts "$name"

  disk="$(vm_disk "$name")"
  log "Creating overlay $disk"
  run qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$disk"
  fix_image_permissions
  write_cloud_init "$name" "${VM_RENAMES[$name]}" "${VM_MGMT_RENAME[$name]:-}" "${VM_LATENCY[$name]:-}"
  fp="$(vm_build_fingerprint "$name" "${bridges[@]}")"
  vm_write_build_stamp "$name" "$fp"
  JUST_BUILT_VMS+=("$name")
}

write_cloud_init() {
  local name="$1" sw_renames="$2" sw_mgmt_rename="${3:-}" sw_latency="${4:-}"
  local seed_dir
  seed_dir="$(mktemp -d)"

  cat >"${seed_dir}/meta-data" <<EOF
instance-id: ${name}-$(date +%s)
local-hostname: ${name}
EOF

  cloud_init_user_data "$name" "$sw_renames" "$sw_mgmt_rename" "$sw_latency" >"${seed_dir}/user-data"

  run cloud-localds "$(vm_seed "$name")" "${seed_dir}/user-data" "${seed_dir}/meta-data"
  rm -rf "$seed_dir"
  fix_image_permissions
}

vm_exists() {
  virsh dominfo "$1" &>/dev/null
}

vm_running() {
  [[ "$(virsh domstate "$1" 2>/dev/null || true)" == "running" ]]
}

cloud_init_seed_attached() {
  local name="$1" seed
  seed="$(vm_seed "$name")"
  virsh domblklist "$name" --details 2>/dev/null | grep -qF "$seed"
}

detach_cloud_init_seed() {
  local name="$1"
  local seed target

  seed="$(vm_seed "$name")"
  vm_exists "$name" || return 0

  if cloud_init_seed_attached "$name"; then
    target="$(virsh domblklist "$name" 2>/dev/null | awk -v s="$seed" '$2 == s {print $1; exit}')"
    if [[ -n "$target" ]]; then
      log "Detaching cloud-init seed from $name ($target)"
      run virsh detach-disk "$name" "$target" --config --live 2>/dev/null \
        || run virsh detach-disk "$name" "$target" --config 2>/dev/null \
        || true
    fi
  fi

  if [[ -f "$seed" ]]; then
    rm -f "$seed"
    info "Removed $seed"
  fi
}

vm_just_built() {
  local name="$1" vm
  for vm in "${JUST_BUILT_VMS[@]}"; do
    [[ "$vm" == "$name" ]] && return 0
  done
  return 1
}

wait_first_boot() {
  local name="$1" max="${VM_SW_FIRST_BOOT_WAIT:-300}" i=0

  info "Waiting for $name first-boot (up to ${max}s)..."
  while (( i < max )); do
    if virsh domifaddr "$name" --source agent 2>/dev/null | grep -qE 'ipv4|ipv6'; then
      info "$name guest network reported after ${i}s"
      sleep 5
      return 0
    fi
    if virsh domifaddr "$name" 2>/dev/null | grep -qE 'ipv4|ipv6'; then
      info "$name libvirt lease after ${i}s"
      sleep 5
      return 0
    fi
    sleep 5
    i=$((i + 5))
  done
  info "$name first-boot wait timed out (${max}s); detaching seed anyway"
}

finalize_cloud_init_seeds() {
  local vm
  for vm in "${ALL_VMS[@]}"; do
    vm_exists "$vm" || continue
    if ! [[ -f "$(vm_seed "$vm")" ]] && ! cloud_init_seed_attached "$vm"; then
      continue
    fi
    if vm_just_built "$vm"; then
      wait_first_boot "$vm"
    fi
    detach_cloud_init_seed "$vm"
  done
}

vm_index() {
  local name="$1" i
  for i in "${!ALL_VMS[@]}"; do
    if [[ "${ALL_VMS[$i]}" == "$name" ]]; then
      echo "$i"
      return 0
    fi
  done
  echo 0
}

vm_nic_mac() {
  local name="$1" nic_idx="$2"
  local vm_idx
  vm_idx="$(vm_index "$name")"
  # Unique per vm-sw VM and NIC (52:54:00 is QEMU/KVM OUI)
  printf '52:54:00:%02x:%02x:%02x' \
    $((0x10 + vm_idx * 0x10 + nic_idx)) \
    $((0x20 + vm_idx * 0x10 + nic_idx)) \
    $((0x30 + vm_idx * 0x10 + nic_idx))
}

define_vm() {
  local name="$1"
  shift
  local -a bridges=("$@")
  local disk seed need_cloud_init=0

  if vm_exists "$name" && vm_disk_current "$name" "${bridges[@]}"; then
    info "$name already defined (disk up to date)"
    return 0
  fi

  ensure_vm_image "$name" "${bridges[@]}"

  if vm_exists "$name"; then
    destroy_vm "$name"
  fi

  disk="$(vm_disk "$name")"
  seed="$(vm_seed "$name")"
  if [[ -f "$seed" ]]; then
    need_cloud_init=1
  fi

  log "Defining $name (${#bridges[@]} NICs -> ${bridges[*]})"
  local -a net_args=() disk_args=(--disk "path=${disk},format=qcow2,bus=virtio")
  local br mac i=0
  for br in "${bridges[@]}"; do
    mac="$(vm_nic_mac "$name" "$i")"
    net_args+=(--network "bridge=${br},model=virtio,mac=${mac}")
    i=$((i + 1))
  done

  if [[ -n "${VM_MGMT_RENAME[$name]:-}" ]]; then
    mac="$(vm_nic_mac "$name" "$i")"
    net_args+=(--network "network=${LIBVIRT_MGMT_NET},model=virtio,mac=${mac}")
  fi

  if (( need_cloud_init )); then
    disk_args+=(--disk "path=${seed},device=cdrom")
  fi

  run virt-install \
    --name "$name" \
    --memory "$VM_RAM_MB" \
    --vcpus "$VM_VCPUS" \
    --import \
    "${disk_args[@]}" \
    "${net_args[@]}" \
    --os-variant detect=on,require=off \
    --noautoconsole \
    --graphics none \
    --console pty,target_type=serial
}

declare -A VM_RENAMES=(
  ["$SW_CENTRAL"]="eth0:inf-internal eth1:inf-lower"
  ["$SW_REGIONAL"]="eth0:inf-internal eth1:inf-upper eth2:inf-lower"
  ["$SW_EDGE"]="eth0:inf-internal eth1:inf-upper eth2:inf-lower"
  ["$SW_UE"]="eth0:inf-internal eth1:inf-upper"
)

declare -A VM_MGMT_RENAME=(
  ["$SW_CENTRAL"]="eth2:inf-mgmt"
  ["$SW_REGIONAL"]="eth3:inf-mgmt"
  ["$SW_EDGE"]="eth3:inf-mgmt"
  ["$SW_UE"]="eth2:inf-mgmt"
)

# netem on interconnect ports (iface:delay, ingress + egress via tc + ifb).
# Netem on inf-lower only (lower tier toward the next site down the chain).
# br-ext-cr: central inf-lower | br-ext-re: regional inf-lower
declare -A VM_LATENCY=(
  ["$SW_CENTRAL"]="inf-lower:10ms"
  ["$SW_REGIONAL"]="inf-lower:10ms"
)

ensure_libvirt_mgmt_network() {
  if ! virsh net-info "$LIBVIRT_MGMT_NET" &>/dev/null; then
    echo "Libvirt network '$LIBVIRT_MGMT_NET' not found (install libvirt default network)." >&2
    exit 1
  fi
  local active
  active="$(virsh net-info "$LIBVIRT_MGMT_NET" 2>/dev/null | awk -F: '/Active:/ {gsub(/^[ \t]+/,"",$2); print $2}')"
  if [[ "$active" != "yes" ]]; then
    log "Starting libvirt network $LIBVIRT_MGMT_NET"
    run virsh net-start "$LIBVIRT_MGMT_NET"
  fi
  run virsh net-autostart "$LIBVIRT_MGMT_NET" 2>/dev/null || true
}

cmd_up() {
  need_root
  JUST_BUILT_VMS=()
  ensure_tools
  ensure_base_image
  ensure_libvirt_mgmt_network

  define_vm "$SW_CENTRAL" br-int-central br-ext-cr
  define_vm "$SW_REGIONAL" br-int-regional br-ext-cr br-ext-re
  define_vm "$SW_EDGE" br-int-edge br-ext-re br-ext-eu
  define_vm "$SW_UE" br-int-ue br-ext-eu

  for vm in "${ALL_VMS[@]}"; do
    if ! vm_running "$vm"; then
      log "Starting $vm"
      run virsh start "$vm"
    fi
  done

  finalize_cloud_init_seeds
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
  for vm in "${ALL_VMS[@]}"; do
    if vm_exists "$vm"; then
      if (( ! wipe )); then
        log "Destroying $vm"
        run virsh destroy "$vm" 2>/dev/null || true
        run virsh undefine "$vm" 2>/dev/null || true
      else
        destroy_vm "$vm"
      fi
    fi
    if (( wipe )); then
      log "Removing artifacts for $vm"
      vm_wipe_artifacts "$vm"
    else
      info "Keeping $(vm_disk "$vm") + build stamp (use down --wipe to delete)"
    fi
  done
}

cmd_status() {
  for vm in "${ALL_VMS[@]}"; do
    if vm_exists "$vm"; then
      info "$vm: $(virsh domstate "$vm" 2>/dev/null)"
    else
      info "$vm: (undefined)"
    fi
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <up|down|status>

Commands:
  up       Create/start vm-sw VMs (cloud-init only when build fingerprint changes)
  down     Stop VMs; keep qcow2 overlays + .build stamps by default
  down --wipe  Delete per-VM disks and stamps (forces full rebuild on next up)
  status   Show libvirt state

Build fingerprint (auto-rebuild) includes: base image, guest-bridge.sh,
guest-mgmt.sh, guest-latency.sh, cloud-init recipe, NIC bridges, latency/mgmt, RAM/vCPU.
Stamp file per VM: \${IMAGE_DIR}/<name>.build

Site-switch VMs: ${ALL_VMS[*]}
Base image:      ${BASE_IMAGE}
EOF
}

main() {
  local cmd="${1:-up}"
  shift || true
  case "$cmd" in
    up) cmd_up ;;
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
