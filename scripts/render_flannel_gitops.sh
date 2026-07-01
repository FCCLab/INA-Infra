#!/usr/bin/env bash
# Render Flannel CNI manifests into repos/ for Config Sync GitOps push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
FLANNEL_MANIFEST="${FLANNEL_MANIFEST:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"
SITE_IFACE="${SITE_IFACE:-enp7s0}"

fetch_manifest() {
  local out
  out="$(mktemp)"
  curl -fsSL "$FLANNEL_MANIFEST" -o "$out"
  printf '%s' "$out"
}

write_cluster_flannel() {
  local cluster="$1"
  local repo_name src iface_flag dest_cluster dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  src="$2"
  if [[ "$cluster" == "mgmt" ]]; then
    iface_flag=""
  else
    iface_flag="$SITE_IFACE"
  fi
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/kube-flannel"
  mkdir -p "$dest_cluster" "$dest_ns"

  python3 - "$src" "$dest_cluster" "$dest_ns" "$iface_flag" <<'PY'
import sys
from pathlib import Path

import yaml

src, dest_cluster, dest_ns, iface = sys.argv[1:5]
docs = list(yaml.safe_load_all(Path(src).read_text()))
cluster_docs = []
ns_docs = []

for doc in docs:
    if not doc:
        continue
    kind = doc.get("kind", "")
    if kind in ("ClusterRole", "ClusterRoleBinding"):
        cluster_docs.append(doc)
        continue
    if kind == "DaemonSet" and iface:
        args = doc["spec"]["template"]["spec"]["containers"][0].setdefault("args", [])
        iface_arg = f"--iface={iface}"
        if iface_arg not in args:
            args.append(iface_arg)
    ns_docs.append(doc)

def write_docs(docs, directory, prefix):
    directory = Path(directory)
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        path = directory / f"{prefix}{kind}-{name}.yaml"
        path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))

write_docs(cluster_docs, dest_cluster, "")
write_docs(ns_docs, dest_ns, "")

print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/kube-flannel: {len(ns_docs)} resources")
if iface:
    print(f"  daemonset: --iface={iface}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (Flannel CNI)"
}

main() {
  local clusters=("$@")
  local src=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt "${ALL_CLUSTERS[@]}")
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  src="$(fetch_manifest)"

  for cluster in "${clusters[@]}"; do
    write_cluster_flannel "$cluster" "$src"
  done

  rm -f "$src"

  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh"
  echo "Remove imperative CNI first: ./scripts/uninstall_flannel.sh"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write Flannel manifests to repos/<gitea-repo>/ for Config Sync.
Default: mgmt, central, regional, edge, ue.

Workload clusters patch kube-flannel-ds with --iface=${SITE_IFACE}.
Mgmt uses upstream defaults (operator network).

Source: ${FLANNEL_MANIFEST}
EOF
  exit 0
fi

main "$@"
