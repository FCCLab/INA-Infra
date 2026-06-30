#!/usr/bin/env bash
# Bring up the mgmt Kubernetes cluster on 10.1.132/24 only (no site NIC).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

export BRINGUP_MGMT_CLUSTER=1
exec "$SCRIPT_DIR/bringup_cluster.sh" "$@"
