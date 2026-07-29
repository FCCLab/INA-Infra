#!/usr/bin/env bash
# Ping test for UEs in the ina-infra profile namespace.
#
# Thin wrapper around oai_slice_deployment_namespace_ping_test.sh with:
#   ns=ina-infra, SLICE_COUNT=4, DNN_PREFIX=10.140  → DNN GW 10.140.{N}.1
#
# For each selected UE:
#   [1] confirm Running pod
#   [2] confirm PDU iface (oaitun_ue*)
#   [3] ping target via that iface
#
# Default target: mgmt-0 10.1.132.200. Use --dnn for per-slice DNN GW.
# Use --n6 for per-slice UPF N6 (10.1.140.61..64 for slices 1..4).
#
# Usage:
#   ./scripts/ina-infra-ping-test.sh
#   ./scripts/ina-infra-ping-test.sh --ue1 --ue3 --count 10
#   ./scripts/ina-infra-ping-test.sh --dnn
#   ./scripts/ina-infra-ping-test.sh --n6
#   ./scripts/ina-infra-ping-test.sh --host 10.1.132.11
#   ./scripts/ina-infra-ping-test.sh --tmux
#   ./scripts/ina-infra-ping-test.sh -t
#
# Env overrides: PROFILE_NS / OAI_SLICE_NS, SLICE_COUNT, DNN_PREFIX, N6_PREFIX,
# N6_BASE, EDGE_HOST, …
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
fi

export OAI_SLICE_NS="${OAI_SLICE_NS:-${PROFILE_NS:-ina-infra}}"
export SLICE_COUNT="${SLICE_COUNT:-4}"
export DNN_PREFIX="${DNN_PREFIX:-10.140}"
export N6_PREFIX="${N6_PREFIX:-10.1.140}"
export N6_BASE="${N6_BASE:-60}"

exec "$SCRIPT_DIR/oai_slice_deployment_namespace_ping_test.sh" "$@"
