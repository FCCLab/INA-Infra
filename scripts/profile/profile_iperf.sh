#!/usr/bin/env bash
# iperf3 UL/DL for each UE in a profile namespace.
#
# Thin wrapper around scripts/test_throughput.py with profile-oriented defaults
# (ue-ns = upf-ns = <profilename>).
#
# Default: iperf3 -s on each slice UPF debug sidecar, bound to live N6 IP.
# Clients are oai-ue-N on edge → UPF N6. Use --mgmt for mgmt-0, --dnn for DNN GW.
#
# Usage:
#   ./scripts/profile/profile_iperf.sh <profilename>
#   ./scripts/profile/profile_iperf.sh ina-infra --dir both --time 20
#   ./scripts/profile/profile_iperf.sh ina-infra --tcp --dir ul        # TCP (default)
#   ./scripts/profile/profile_iperf.sh ina-infra --udp --bitrate 10M   # UDP
#   ./scripts/profile/profile_iperf.sh ina-infra -u --dir dl           # same as --udp
#   ./scripts/profile/profile_iperf.sh ina-infra -t --udp --dir ul     # tmux + UDP
#   ./scripts/profile/profile_iperf.sh ina-infra --mgmt                # server on mgmt-0
#   ./scripts/profile/profile_iperf.sh ina-infra --list-only
#
# Proto: --tcp (default) | --udp / -u | --proto tcp|udp
# Note: -t is tmux (not TCP). Use --tcp for TCP explicitly.
#
# -t/--tmux opens two windows (tabs):
#   0:server  — iperf3 -s on UPF debug (-B N6); one pane per UE
#   1:client  — UE iperf3 forever; one pane per UE
#   Switch: Ctrl-b 0 / Ctrl-b 1   Kill: Ctrl-C (pkills all UE+UPF iperf3)
#
# Direction: ul|dl|both (also upload|download aliases).
# Env: EDGE_HOST
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
THROUGHPUT="$ROOT/scripts/test_throughput.py"

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  echo
  echo "Pass-through flags (see ./scripts/test_throughput.py -h):"
  echo "  --dir ul|dl|both   --mode sequential|parallel   --time SEC"
  echo "  --tcp (default)  --udp / -u  --proto tcp|udp   --bitrate RATE  --streams N"
  echo "  --n6 (default)  --dnn  --mgmt"
  echo "  --tmux / -t        --session NAME   --list-only   --skip-server"
  echo "  --ue N / --ue1..5  --host IP   --server-host HOST"
  exit "${1:-0}"
}

if [[ $# -lt 1 ]]; then
  echo "error: missing <profilename>" >&2
  usage 1
fi

case "$1" in
  -h|--help) usage 0 ;;
  -*)
    echo "error: first argument must be <profilename>, got: $1" >&2
    usage 1
    ;;
esac

NS="$1"
shift

if [[ ! -x "$THROUGHPUT" && ! -f "$THROUGHPUT" ]]; then
  echo "error: missing $THROUGHPUT" >&2
  exit 1
fi

# Map profile-style flags → test_throughput.py; keep everything else.
PY_ARGS=()
HAVE_SESSION=0
SERVER_MODE=""  # n6 | dnn | mgmt | (empty → default n6)
PROTO=""        # tcp | udp | (empty → default tcp)
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    # Profile convention: -t = tmux (python's -t means --tcp — use --tcp here).
    -t|--tmux)
      PY_ARGS+=(--tmux)
      shift
      ;;
    --tcp)
      PROTO=tcp
      shift
      ;;
    -u|--udp)
      PROTO=udp
      shift
      ;;
    --proto)
      case "${2:?}" in
        tcp|TCP) PROTO=tcp ;;
        udp|UDP) PROTO=udp ;;
        *)
          echo "error: --proto must be tcp or udp, got: $2" >&2
          usage 1
          ;;
      esac
      shift 2
      ;;
    # Profile/speedtest-style duration.
    -d|--duration)
      PY_ARGS+=(--time "${2:?}")
      shift 2
      ;;
    --time)
      PY_ARGS+=(--time "${2:?}")
      shift 2
      ;;
    --dir|--direction)
      dir="${2:?}"
      case "$dir" in
        upload|ul) dir=ul ;;
        download|dl) dir=dl ;;
        both) dir=both ;;
        *)
          echo "error: --dir must be ul|dl|both (or upload|download), got: $dir" >&2
          usage 1
          ;;
      esac
      PY_ARGS+=(--dir "$dir")
      shift 2
      ;;
    --session)
      HAVE_SESSION=1
      PY_ARGS+=(--session "${2:?}")
      shift 2
      ;;
    --n6)
      SERVER_MODE=n6
      shift
      ;;
    --dnn)
      SERVER_MODE=dnn
      shift
      ;;
    --mgmt)
      SERVER_MODE=mgmt
      shift
      ;;
    --host|--server-host)
      # Explicit mgmt/host mode — do not also pass --n6.
      SERVER_MODE=mgmt
      PY_ARGS+=("$1" "${2:?}")
      shift 2
      ;;
    --ue-ns|--upf-ns)
      echo "error: --ue-ns/--upf-ns are set from <profilename>=${NS}" >&2
      exit 1
      ;;
    *)
      PY_ARGS+=("$1")
      shift
      ;;
  esac
done

safe_ns="$(printf '%s' "$NS" | tr -c 'A-Za-z0-9_' '_')"
if [[ "$HAVE_SESSION" -eq 0 ]]; then
  PY_ARGS+=(--session "oai_iperf_${safe_ns}")
fi

if [[ -n "${EDGE_HOST:-}" ]]; then
  PY_ARGS+=(--edge-host "$EDGE_HOST")
fi

case "${SERVER_MODE:-n6}" in
  n6) PY_ARGS+=(--n6) ;;
  dnn) PY_ARGS+=(--dnn) ;;
  mgmt) ;;
  *)
    echo "error: unknown server mode: ${SERVER_MODE}" >&2
    exit 1
    ;;
esac

case "${PROTO:-tcp}" in
  tcp) PY_ARGS+=(--tcp) ;;
  udp) PY_ARGS+=(--udp) ;;
  *)
    echo "error: unknown proto: ${PROTO}" >&2
    exit 1
    ;;
esac

echo "iperf profile ns=${NS} (ue-ns=upf-ns=${NS}) server=${SERVER_MODE:-n6} proto=${PROTO:-tcp}"
exec python3 "$THROUGHPUT" \
  --ue-ns "$NS" \
  --upf-ns "$NS" \
  "${PY_ARGS[@]}"
