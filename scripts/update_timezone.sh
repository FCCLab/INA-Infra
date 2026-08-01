#!/usr/bin/env bash
# Set timezone + NTP on Nephio lab nodes (default: Asia/Singapore + sg.pool.ntp.org).
#
# Per host (via SSH + sudo):
#   1. timedatectl set-timezone <ZONE>
#   2. drop-in for systemd-timesyncd → Singapore NTP pool
#   3. timedatectl set-ntp true + enable/restart systemd-timesyncd
#
# Usage:
#   ./scripts/update_timezone.sh                    # all known k8s nodes
#   ./scripts/update_timezone.sh --status
#   ./scripts/update_timezone.sh edge usrp gh82
#   ./scripts/update_timezone.sh --timezone Asia/Singapore central
#   HOSTS="usrp edge-0" ./scripts/update_timezone.sh
#
# Requires passwordless sudo on targets (lab default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_OPTS=(-F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 -o RequestTTY=no)
TIMEZONE="${TIMEZONE:-Asia/Singapore}"
NTP_SERVERS="${NTP_SERVERS:-sg.pool.ntp.org ntp.ubuntu.com}"
FALLBACK_NTP="${FALLBACK_NTP:-0.ubuntu.pool.ntp.org 1.ubuntu.pool.ntp.org}"
STATUS_ONLY=0
DRY_RUN=0

# Default: every SSH-reachable node that is (or can be) a k8s worker/CP.
DEFAULT_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1 gh82
  regional-0 regional-1
  edge-0 edge-1 edge-2 edge-3
  usrp
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster|host ...]

Set timezone and enable NTP (systemd-timesyncd) on lab nodes.

Targets (default: all known cluster nodes):
  cluster name → CP + worker SSH aliases (mgmt/central/regional/edge)
  host alias   → as in utils/ssh_config/config (e.g. usrp, gh82, edge-2)
  Or set HOSTS="usrp edge-0" to override.

Options:
  -s, --status              Only print timezone / NTP / clock
  -t, --timezone ZONE       Timezone (default: ${TIMEZONE})
      --ntp "SERVER ..."    Primary NTP servers (default: ${NTP_SERVERS})
  -n, --dry-run             Print remote commands only
  -h, --help                Show this help

Examples:
  $(basename "$0")
  $(basename "$0") --status
  $(basename "$0") edge usrp
  $(basename "$0") --timezone Asia/Singapore central regional
  TIMEZONE=Asia/Singapore $(basename "$0") --status gh82 edge-3

Environment:
  SSH_CFG / SSH_CONFIG   SSH config (default: utils/ssh_config/config)
  TIMEZONE               Same as --timezone
  NTP_SERVERS            Same as --ntp
  HOSTS                  Space-separated host override
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
    printf '%s\n' "${DEFAULT_HOSTS[@]}"
    return
  fi
  for arg in "$@"; do
    case "$arg" in
      mgmt)
        out+=(mgmt-0 mgmt-1)
        ;;
      central)
        out+=("${CLUSTER_CP_HOST[central]}" "${CLUSTER_WORKER_HOST[central]}" gh82)
        ;;
      regional)
        out+=("${CLUSTER_CP_HOST[regional]}" "${CLUSTER_WORKER_HOST[regional]}")
        ;;
      edge)
        out+=("${CLUSTER_CP_HOST[edge]}" "${CLUSTER_WORKER_HOST[edge]}" edge-2 edge-3 usrp)
        ;;
      *)
        out+=("$arg")
        ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}

remote_payload() {
  # Vars expanded by local shell; remote body after REMOTE is literal except we
  # pass MODE/TIMEZONE/NTP via env on the ssh command line.
  cat <<'REMOTE'
set -euo pipefail
MODE="${MODE:-apply}"
TIMEZONE="${TIMEZONE:-Asia/Singapore}"
NTP_SERVERS="${NTP_SERVERS:-sg.pool.ntp.org ntp.ubuntu.com}"
FALLBACK_NTP="${FALLBACK_NTP:-0.ubuntu.pool.ntp.org 1.ubuntu.pool.ntp.org}"

host="$(hostname -s 2>/dev/null || hostname)"
tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
ntp="$(timedatectl show -p NTP --value 2>/dev/null || true)"
sync="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
now="$(date -R)"

if [[ "$MODE" == "status" ]]; then
  printf '%-12s  tz=%-16s  ntp=%-3s  sync=%-3s  %s\n' \
    "$host" "${tz:-?}" "${ntp:-?}" "${sync:-?}" "$now"
  exit 0
fi

echo "==> $host: timezone $TIMEZONE + NTP ($NTP_SERVERS)"

sudo mkdir -p /etc/systemd/timesyncd.conf.d
sudo tee /etc/systemd/timesyncd.conf.d/99-lab-ntp.conf >/dev/null <<CONF
[Time]
NTP=${NTP_SERVERS}
FallbackNTP=${FALLBACK_NTP}
CONF

sudo timedatectl set-timezone "$TIMEZONE"
sudo timedatectl set-ntp true

if systemctl list-unit-files systemd-timesyncd.service >/dev/null 2>&1; then
  sudo systemctl enable systemd-timesyncd.service >/dev/null 2>&1 || true
  sudo systemctl restart systemd-timesyncd.service
fi

sleep 1
tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
ntp="$(timedatectl show -p NTP --value 2>/dev/null || true)"
sync="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
now="$(date -R)"
printf '    ok  tz=%s  ntp=%s  sync=%s  %s\n' "$tz" "$ntp" "$sync" "$now"
REMOTE
}

apply_host() {
  local host="$1" mode="$2"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $host MODE=$mode TIMEZONE=$TIMEZONE"
    return 0
  fi
  if ! ssh_host -o ConnectTimeout=8 "$host" 'true' 2>/dev/null; then
    echo "error: cannot SSH to $host" >&2
    return 1
  fi
  # Export vars into remote environment for the payload.
  ssh_host "$host" \
    "MODE=$(printf '%q' "$mode") TIMEZONE=$(printf '%q' "$TIMEZONE") NTP_SERVERS=$(printf '%q' "$NTP_SERVERS") FALLBACK_NTP=$(printf '%q' "$FALLBACK_NTP") bash -s" \
    < <(remote_payload)
}

main() {
  local -a args=() targets=()
  local arg mode rc=0

  if [[ -n "${SSH_CONFIG:-}" && -z "${SSH_CFG_SET:-}" ]]; then
    SSH_CFG="$SSH_CONFIG"
    SSH_OPTS=(-F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 -o RequestTTY=no)
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      -s|--status)
        STATUS_ONLY=1
        shift
        ;;
      -n|--dry-run)
        DRY_RUN=1
        shift
        ;;
      -t|--timezone)
        TIMEZONE="${2:?--timezone requires a value}"
        shift 2
        ;;
      --ntp)
        NTP_SERVERS="${2:?--ntp requires a value}"
        shift 2
        ;;
      --)
        shift
        args+=("$@")
        break
        ;;
      -*)
        echo "error: unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done

  if [[ ! -f "$SSH_CFG" ]]; then
    echo "error: SSH config not found: $SSH_CFG" >&2
    exit 1
  fi

  mapfile -t targets < <(resolve_targets "${args[@]+"${args[@]}"}" | awk 'NF && !seen[$0]++')
  if [[ ${#targets[@]} -eq 0 ]]; then
    echo "error: no targets" >&2
    exit 1
  fi

  mode=apply
  [[ "$STATUS_ONLY" -eq 1 ]] && mode=status

  echo "Targets (${#targets[@]}): ${targets[*]}"
  [[ "$mode" == "apply" ]] && echo "Timezone: $TIMEZONE  NTP: $NTP_SERVERS"
  echo

  local host
  for host in "${targets[@]}"; do
    if ! apply_host "$host" "$mode"; then
      rc=1
    fi
  done

  exit "$rc"
}

main "$@"
