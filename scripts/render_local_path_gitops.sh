#!/usr/bin/env bash
# Render local-path StorageClass + provisioner into repos/ for Config Sync GitOps push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
LOCAL_PATH_MANIFEST="${LOCAL_PATH_MANIFEST:-$REPO_ROOT/bringup/01_gitea/manifests/storage/local-path-provisioner.yaml}"
STORAGE_CLASS_NAME="${STORAGE_CLASS_NAME:-local-path}"
LOCAL_PATH_NODE_DIR="${LOCAL_PATH_NODE_DIR:-/opt/local-path-provisioner}"
SET_DEFAULT_SC="${SET_DEFAULT_SC:-1}"

write_cluster_local_path() {
  local cluster="$1"
  local repo_name dest_cluster dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/local-path-storage"
  mkdir -p "$dest_cluster" "$dest_ns"

  python3 - "$LOCAL_PATH_MANIFEST" "$dest_cluster" "$dest_ns" \
    "$STORAGE_CLASS_NAME" "$LOCAL_PATH_NODE_DIR" "$SET_DEFAULT_SC" <<'PY'
import json
import sys
from pathlib import Path

import yaml

manifest, dest_cluster, dest_ns, sc_name, node_dir, set_default = sys.argv[1:7]
dest_cluster = Path(dest_cluster)
dest_ns = Path(dest_ns)
cluster_kinds = {"ClusterRole", "ClusterRoleBinding", "StorageClass"}
cluster_docs = []
ns_docs = []

def clean_metadata(meta):
    if isinstance(meta, dict) and meta.get("annotations") is None:
        meta.pop("annotations", None)


for doc in yaml.safe_load_all(Path(manifest).read_text()):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    clean_metadata(doc.get("metadata"))
    if kind == "StorageClass":
        doc["metadata"]["name"] = sc_name
        if set_default == "1":
            doc["metadata"].setdefault("annotations", {})[
                "storageclass.kubernetes.io/is-default-class"
            ] = "true"
        else:
            doc["metadata"].get("annotations", {}).pop(
                "storageclass.kubernetes.io/is-default-class", None
            )
        cluster_docs.append(doc)
        continue
    if kind == "ConfigMap" and doc["metadata"]["name"] == "local-path-config":
        cfg = json.loads(doc["data"]["config.json"])
        cfg["nodePathMap"] = [{"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": [node_dir]}]
        doc["data"]["config.json"] = json.dumps(cfg, indent=4)
    if kind in cluster_kinds:
        cluster_docs.append(doc)
    else:
        ns_docs.append(doc)


def purge(directory, prefixes):
    directory = Path(directory)
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()


cluster_prefixes = (
    "storageclass-", "clusterrole-local-path", "clusterrolebinding-local-path",
)
ns_prefixes = (
    "namespace-local-path-storage",
    "serviceaccount-local-path",
    "role-local-path",
    "rolebinding-local-path",
    "deployment-local-path",
    "configmap-local-path",
)
purge(dest_cluster, cluster_prefixes)
purge(dest_ns, ns_prefixes)


def write_docs(docs, directory):
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        (directory / f"{kind}-{name}.yaml").write_text(
            yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        )


write_docs(cluster_docs, dest_cluster)
write_docs(ns_docs, dest_ns)
print(f"  cluster: {len(cluster_docs)} resources (StorageClass {sc_name})")
print(f"  namespaces/local-path-storage: {len(ns_docs)} resources")
print(f"  node path: {node_dir}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (local-path StorageClass)"
}

main() {
  local clusters=("$@")

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt "${ALL_CLUSTERS[@]}")
  fi

  if [[ ! -f "$LOCAL_PATH_MANIFEST" ]]; then
    echo "error: manifest not found: $LOCAL_PATH_MANIFEST" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  for cluster in "${clusters[@]}"; do
    write_cluster_local_path "$cluster"
  done

  echo
  echo "StorageClass: ${STORAGE_CLASS_NAME} (default: ${SET_DEFAULT_SC})"
  echo "MySQL with PVC: MYSQL_STORAGE=pvc ./scripts/render_oai_core_gitops.sh central"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write rancher/local-path provisioner + StorageClass to repos/<gitea-repo>/.
Default: mgmt, central, regional, edge, ue.

Source: ${LOCAL_PATH_MANIFEST}

Environment:
  STORAGE_CLASS_NAME     StorageClass name (default: local-path)
  LOCAL_PATH_NODE_DIR    Host path for volumes (default: /opt/local-path-provisioner)
  SET_DEFAULT_SC         1 = default StorageClass (default: 1)
  LOCAL_PATH_MANIFEST    Source YAML
EOF
  exit 0
fi

main "$@"
