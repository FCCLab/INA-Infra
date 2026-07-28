#!/usr/bin/env bash
# Maximize CPU performance on Nephio lab nodes (OAI / RAN friendly).
#
# Per host (via SSH + sudo):
#   1. CPU governor → performance (all cores)
#   2. Disable CPU idle/C-states (cpupower idle-set -D 0)
#   3. Optionally pin cpu_dma_latency=0 (persistent systemd unit)
#
# Usage:
#   ./scripts/maximize_cpu_performance.sh              # all cluster nodes + usrp
#   ./scripts/maximize_cpu_performance.sh edge usrp    # edge-0/1 + usrp
#   ./scripts/maximize_cpu_performance.sh --status
#   ./scripts/maximize_cpu_performance.sh --persist central regional edge usrp
#   HOSTS="usrp edge-0" ./scripts/maximize_cpu_performance.sh
#
# Notes:
#   - Changes without --persist are lost on reboot.
#   - Requires passwordless sudo on targets (lab default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_OPTS=(-F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 -o RequestTTY=no)
STATUS_ONLY=0
PERSIST=0
DRY_RUN=0
DISABLE_IDLE=1
DMA_LATENCY=1

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster|host ...]

Set CPU scaling governor to performance (and disable idle states) on lab nodes.

Targets (default: mgmt, central, regional, edge, ue, usrp):
  cluster name → both CP + worker SSH aliases (e.g. edge → edge-0 edge-1)
  host alias   → as in utils/ssh_config/config (e.g. usrp, central-0)
  Or set HOSTS="usrp edge-0" to override.

Options:
  -s, --status     Only print current governor / idle / freq summary
  -p, --persist    Install systemd oneshot to re-apply on boot
      --no-idle    Skip cpupower idle-set -D 0
      --no-dma     Skip cpu_dma_latency hold
  -n, --dry-run    Print remote commands only
  -h, --help       Show this help

Examples:
  $(basename "$0")
  $(basename "$0") --status usrp edge
  $(basename "$0") --persist edge usrp
EOF
}

ssh_host() {
  ssh "${SSH_OPTS[@]}" "$@"
}

resolve_targets() {
  local arg cluster
  local -a out=()
  if [[ -n "${HOSTS:-}" ]]; then
    # shellcheck disable=SC2206
    out=(${HOSTS})
    printf '%s\n' "${out[@]}"
    return
  fi
  if [[ $# -eq 0 ]]; then
    out=(
      mgmt-0 mgmt-1
      central-0 central-1
      regional-0 regional-1
      edge-0 edge-1
      ue-0 ue-1
      usrp
    )
    printf '%s\n' "${out[@]}"
    return
  fi
  for arg in "$@"; do
    case "$arg" in
      mgmt)
        out+=(mgmt-0 mgmt-1)
        ;;
      central|regional|edge|ue)
        cluster="$arg"
        out+=("${CLUSTER_CP_HOST[$cluster]}" "${CLUSTER_WORKER_HOST[$cluster]}")
        ;;
      *)
        out+=("$arg")
        ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}

# Remote payload: apply or status. Env: MODE=apply|status PERSIST DISABLE_IDLE DMA_LATENCY
remote_payload() {
  cat <<'REMOTE'
set -euo pipefail
MODE="${MODE:-apply}"
PERSIST="${PERSIST:-0}"
DISABLE_IDLE="${DISABLE_IDLE:-1}"
DMA_LATENCY="${DMA_LATENCY:-1}"

have() { command -v "$1" >/dev/null 2>&1; }

nproc_c=$(nproc)
gov_path() { echo "/sys/devices/system/cpu/cpu$1/cpufreq/scaling_governor"; }
cur_gov() {
  local g="" i
  for i in $(seq 0 $((nproc_c - 1))); do
    if [[ -r "$(gov_path "$i")" ]]; then
      g="$(cat "$(gov_path "$i")")"
      break
    fi
  done
  echo "${g:-n/a}"
}
freq_mhz() {
  local f="" i
  for i in $(seq 0 $((nproc_c - 1))); do
    if [[ -r "/sys/devices/system/cpu/cpu$i/cpufreq/scaling_cur_freq" ]]; then
      f="$(cat "/sys/devices/system/cpu/cpu$i/cpufreq/scaling_cur_freq")"
      break
    fi
  done
  if [[ -n "$f" ]]; then
    echo "$((f / 1000))MHz"
  else
    echo "n/a"
  fi
}

print_status() {
  echo "  host=$(hostname -s) cpus=${nproc_c} governor=$(cur_gov) cur_freq=$(freq_mhz)"
  if have cpupower; then
    cpupower frequency-info -p 2>/dev/null | sed 's/^/  /' || true
    cpupower idle-info 2>/dev/null | awk '/Number of idle states|Available idle states|Disabled:/{print "  "$0}' || true
  fi
  if [[ -e /dev/cpu_dma_latency ]]; then
    if systemctl is-active --quiet cpu-dma-latency.service 2>/dev/null; then
      echo "  cpu_dma_latency: held (cpu-dma-latency.service active)"
    else
      echo "  cpu_dma_latency: device present (hold not active)"
    fi
  fi
}

set_governor_performance() {
  local i ok=0
  if have cpupower; then
    if sudo cpupower frequency-set -g performance >/dev/null 2>&1; then
      ok=1
    fi
  fi
  if [[ "$ok" -eq 0 ]] && have cpufreq-set; then
    for i in $(seq 0 $((nproc_c - 1))); do
      sudo cpufreq-set -c "$i" -g performance 2>/dev/null || true
    done
    ok=1
  fi
  for i in $(seq 0 $((nproc_c - 1))); do
    if [[ -w "$(gov_path "$i")" ]] || sudo test -w "$(gov_path "$i")"; then
      echo performance | sudo tee "$(gov_path "$i")" >/dev/null 2>&1 || true
    fi
  done
  # Prefer max freq when userspace/performance allows
  if have cpupower; then
    sudo cpupower frequency-set -g performance >/dev/null 2>&1 || true
  fi
}

disable_idle() {
  if have cpupower; then
    sudo cpupower idle-set -D 0 >/dev/null 2>&1 || sudo cpupower idle-set -D0 >/dev/null 2>&1 || true
  fi
}

install_persist() {
  local unit=/etc/systemd/system/cpu-performance.service
  sudo tee "$unit" >/dev/null <<'UNIT'
[Unit]
Description=Set CPU governor to performance and disable idle states
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'n=$(nproc); if command -v cpupower >/dev/null; then cpupower frequency-set -g performance; cpupower idle-set -D 0 || true; elif command -v cpufreq-set >/dev/null; then for i in $(seq 0 $((n-1))); do cpufreq-set -c $i -g performance; done; fi; for i in $(seq 0 $((n-1))); do g=/sys/devices/system/cpu/cpu$i/cpufreq/scaling_governor; [[ -e $g ]] && echo performance >$g || true; done'
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now cpu-performance.service
  echo "  installed/enabled cpu-performance.service"
}

install_dma_latency() {
  # Keep /dev/cpu_dma_latency open at 0 so deep C-states stay inhibited.
  local unit=/etc/systemd/system/cpu-dma-latency.service
  sudo tee "$unit" >/dev/null <<'UNIT'
[Unit]
Description=Hold CPU DMA latency at 0 (disable deep idle)
After=multi-user.target

[Service]
Type=simple
ExecStart=/bin/bash -c 'exec 3>/dev/cpu_dma_latency; printf "\\0\\0\\0\\0" >&3; while true; do sleep 3600; done'
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now cpu-dma-latency.service
  echo "  installed/enabled cpu-dma-latency.service"
}

hold_dma_once() {
  # Best-effort one-shot write (does not persist without keeping FD open).
  if [[ -e /dev/cpu_dma_latency ]]; then
    printf '\0\0\0\0' | sudo tee /dev/cpu_dma_latency >/dev/null 2>&1 || true
  fi
}

echo "==> $(hostname -s) ($MODE)"
if [[ "$MODE" == "status" ]]; then
  print_status
  exit 0
fi

# Ensure tools if possible (non-fatal)
if ! have cpupower && have apt-get; then
  sudo apt-get install -y -qq linux-tools-common "linux-tools-$(uname -r)" 2>/dev/null || \
    sudo apt-get install -y -qq linux-tools-generic 2>/dev/null || true
fi
if ! have cpufreq-set && have apt-get; then
  sudo apt-get install -y -qq cpufrequtils 2>/dev/null || true
fi

set_governor_performance
if [[ "$DISABLE_IDLE" == "1" ]]; then
  disable_idle
fi
if [[ "$DMA_LATENCY" == "1" ]]; then
  if [[ "$PERSIST" == "1" ]]; then
    install_dma_latency
  else
    hold_dma_once
  fi
fi
if [[ "$PERSIST" == "1" ]]; then
  install_persist
fi
print_status
REMOTE
}

main() {
  local -a pos=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -s|--status) STATUS_ONLY=1; shift ;;
      -p|--persist) PERSIST=1; shift ;;
      --no-idle) DISABLE_IDLE=0; shift ;;
      --no-dma) DMA_LATENCY=0; shift ;;
      -n|--dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      --) shift; break ;;
      -*)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
      *) pos+=("$1"); shift ;;
    esac
  done
  pos+=("$@")

  local -a targets=()
  mapfile -t targets < <(resolve_targets "${pos[@]}")
  # unique preserve order
  local -A seen=()
  local -a uniq=()
  local h
  for h in "${targets[@]}"; do
    [[ -n "$h" ]] || continue
    [[ -z "${seen[$h]:-}" ]] || continue
    seen[$h]=1
    uniq+=("$h")
  done

  local mode="apply"
  [[ "$STATUS_ONLY" -eq 1 ]] && mode="status"

  echo "==> CPU performance tune (mode=${mode} persist=${PERSIST} idle=${DISABLE_IDLE} dma=${DMA_LATENCY})"
  echo "    hosts: ${uniq[*]}"

  local fail=0
  for h in "${uniq[@]}"; do
    echo
    echo "---- ${h} ----"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "  [dry-run] ssh ${h} MODE=${mode} ..."
      continue
    fi
    if ! ssh_host "$h" true 2>/dev/null; then
      echo "  SKIP unreachable: ${h}" >&2
      fail=1
      continue
    fi
    if ! ssh_host "$h" \
      env MODE="$mode" PERSIST="$PERSIST" DISABLE_IDLE="$DISABLE_IDLE" DMA_LATENCY="$DMA_LATENCY" \
      bash -s <<<"$(remote_payload)"; then
      echo "  FAIL on ${h}" >&2
      fail=1
    fi
  done

  echo
  if [[ "$fail" -ne 0 ]]; then
    echo "Done with some failures." >&2
    exit 1
  fi
  echo "Done."
}

main "$@"
