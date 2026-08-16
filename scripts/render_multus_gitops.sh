#!/usr/bin/env bash
# Render Multus CNI into repos/ (required for OAI NAD / secondary interfaces).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
MULTUS_MANIFEST="${MULTUS_MANIFEST:-https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset-thick.yml}"
MULTUS_NS="${MULTUS_NS:-kube-system}"

fetch_manifest() {
  local out
  out="$(mktemp)"
  curl -fsSL "$MULTUS_MANIFEST" -o "$out"
  printf '%s' "$out"
}

write_cluster_multus() {
  local cluster="$1"
  local src="$2"
  local repo_name dest_ns dest_cluster

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${MULTUS_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_ns" "$dest_cluster"

  python3 - "$src" "$dest_ns" "$dest_cluster" "$MULTUS_NS" <<'PY'
import sys
from pathlib import Path

import yaml

src, dest_ns, dest_cluster, multus_ns = sys.argv[1:5]
dest_ns = Path(dest_ns)
dest_cluster = Path(dest_cluster)
cluster_kinds = {"ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"}
managed_ns = ("clusterrole-multus", "clusterrolebinding-multus", "serviceaccount-multus",
              "configmap-multus", "daemonset-kube-multus-ds")
managed_cluster = ("customresourcedefinition-network-attachment-definitions",)

for directory, prefixes in ((dest_ns, managed_ns), (dest_cluster, managed_cluster)):
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()

cluster_docs = []
ns_docs = []

for doc in yaml.safe_load_all(Path(src).read_text()):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    meta = doc.setdefault("metadata", {})
    if meta.get("annotations") is None:
        meta.pop("annotations", None)
    if kind in cluster_kinds:
        cluster_docs.append(doc)
    else:
        if kind != "Namespace" and "namespace" not in meta:
            meta["namespace"] = multus_ns
        ns_docs.append(doc)

def write_docs(docs, directory):
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        if doc.get("kind") == "DaemonSet" and name == "kube-multus-ds":
            for c in doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
                if c.get("name") == "kube-multus":
                    c["resources"] = {
                        "requests": {"cpu": "100m", "memory": "100Mi"},
                        "limits": {"cpu": "500m", "memory": "500Mi"},
                    }
        (directory / f"{kind}-{name}.yaml").write_text(
            yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        )

write_docs(cluster_docs, dest_cluster)
write_docs(ns_docs, dest_ns)
print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/{multus_ns}: {len(ns_docs)} resources")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (Multus CNI → ${MULTUS_NS})"
}

main() {
  local clusters=("$@")
  local src=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  src="$(fetch_manifest)"
  trap 'rm -f "${src:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_cluster_multus "$cluster" "$src"
  done

  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write Multus CNI manifests to repos/<gitea-repo>/ for Config Sync.
Default cluster: central.

Source: ${MULTUS_MANIFEST}
EOF
  exit 0
fi

main "$@"
