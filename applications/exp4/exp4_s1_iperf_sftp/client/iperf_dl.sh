#!/usr/bin/env bash
# Exp4 slice 1: iperf3 downlink goodput on the PDU (diagnostic + T_bar).
set -euo pipefail
HOST="${IPERF_HOST:-${TARGET_SERVER_IP:-10.1.137.211}}"
PORT="${IPERF_PORT:-5201}"
SEC="${IPERF_SEC:-10}"
exec iperf3 -c "$HOST" -p "$PORT" -R -t "$SEC" --json
