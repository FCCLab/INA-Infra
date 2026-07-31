#!/usr/bin/env bash
# Label workload nodes with ina-infra.nephio.lab/multus-master=<iface>
# (auto-detected from the site plane 10.1.137.0/24). UPF/AMF/SMF pods
# select nodes by this label so macvlan parent matches.
#
#   ./scripts/label_ina_multus_masters.sh [central|regional|edge|all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

export REPO_ROOT
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config:$HOME/.kube/config-central:$HOME/.kube/config-regional:$HOME/.kube/config-edge}"

TARGET="${1:-all}"

python3 - "$REPO_ROOT" "$TARGET" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

repo = Path(sys.argv[1])
target = sys.argv[2]
sys.path.insert(0, str(repo / "ina-infra" / "backend"))
from app.services import multus_iface

clusters = ["central", "regional", "edge"] if target == "all" else [target]
for c in clusters:
    labeled = multus_iface.label_cluster_nodes_multus_master(c)
    if not labeled:
        print(f"{c}: (no nodes labeled)")
        continue
    for node, master in sorted(labeled.items()):
        print(f"{c}: {node} → {master}")
PY
