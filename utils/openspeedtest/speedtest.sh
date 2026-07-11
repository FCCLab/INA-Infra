#!/usr/bin/env bash
# Run OpenSpeedTest from a UE PDU tunnel (bind to UE IP via oaitun_*).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ue_common.sh
source "$SCRIPT_DIR/ue_common.sh"

DURATION="${DURATION:-10}"
THREADS="${THREADS:-1}"
DIRECTION="${DIRECTION:-both}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <ue-id|namespace|pod> [options]

List UEs first:
  ./list_ues.sh

Examples:
  $(basename "$0") 1
  $(basename "$0") oai-nws-1ue
  $(basename "$0") 1 --server http://10.1.132.11/ --duration 10 --threads 1
  $(basename "$0") 2 --direction download

Options:
  --server URL       OpenSpeedTest server (default: ${OST_SERVER})
  --duration SEC     Seconds per direction (default: ${DURATION})
  --threads N        Parallel connections (default: ${THREADS}; use 1 on RFsim)
  --direction DIR    download|upload|both (default: ${DIRECTION})
  -h, --help         Show help

Environment:
  UE_HOST OST_SERVER TUN_MTU SSH_CONFIG DURATION THREADS DIRECTION
EOF
}

main() {
  local ue_sel="" server="$OST_SERVER" duration="$DURATION" threads="$THREADS" direction="$DIRECTION"
  local line id ns pod ctr tun ue_ip dest_ip

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --server) server="$2"; shift 2 ;;
      --duration|-d) duration="$2"; shift 2 ;;
      --threads|-t) threads="$2"; shift 2 ;;
      --direction|--dir) direction="$2"; shift 2 ;;
      -*)
        echo "error: unknown option $1" >&2
        usage >&2
        exit 1
        ;;
      *)
        if [[ -z "$ue_sel" ]]; then
          ue_sel="$1"
          shift
        else
          echo "error: unexpected argument $1" >&2
          exit 1
        fi
        ;;
    esac
  done

  if [[ -z "$ue_sel" ]]; then
    echo "Available UEs:"
    "$SCRIPT_DIR/list_ues.sh" || true
    echo
    usage >&2
    exit 1
  fi

  line="$(resolve_ue "$ue_sel")"
  IFS='|' read -r id ns pod ctr tun ue_ip <<<"$line"

  dest_ip="${server#http://}"
  dest_ip="${dest_ip#https://}"
  dest_ip="${dest_ip%%[:/]*}"

  echo "==> UE #${id}  ${ns}/${pod}"
  echo "    tun=${tun}  ue_ip=${ue_ip}  server=${server}  threads=${threads}"

  echo "==> prepare path (mtu ${TUN_MTU}, route ${dest_ip} via ${tun})"
  prep_ue_path "$ns" "$pod" "$ctr" "$tun" "$dest_ip"

  echo "==> copy speedtest.py into pod"
  copy_speedtest_py "$ns" "$pod" "$ctr"

  echo "==> run speedtest"
  # Unbuffered Python so progress/summary stream through SSH/kubectl (no TTY).
  ssh_ue "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} -n $(printf '%q' "$ns") exec \
    $(printf '%q' "$pod") -c $(printf '%q' "$ctr") -- \
    env PYTHONUNBUFFERED=1 python3 -u /tmp/speedtest.py \
    --server $(printf '%q' "$server") --bind $(printf '%q' "$ue_ip") \
    --duration $(printf '%q' "$duration") --threads $(printf '%q' "$threads") \
    --direction $(printf '%q' "$direction")"
}

main "$@"
