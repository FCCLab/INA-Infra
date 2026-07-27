#!/usr/bin/env bash
# Render mgmt registry PersistentVolumeClaim size into repos/mgmt for Config Sync.
#
# The registry lives on mgmt node-0's 2T local-path disk
# (/opt/local-path-provisioner on /dev/vdc). Default size is the practical max
# that fits remaining free space on that filesystem (~1860Gi).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
REGISTRY_NS="${REGISTRY_NS:-registry}"
REGISTRY_PVC_NAME="${REGISTRY_PVC_NAME:-registry-pvc}"
# Max usable on the 2T local-path disk (ext4 free ≈ 1861Gi with current usage).
REGISTRY_PVC_SIZE="${REGISTRY_PVC_SIZE:-1860Gi}"
REGISTRY_STORAGE_CLASS="${REGISTRY_STORAGE_CLASS:-local-path}"

usage() {
  cat <<EOF
Usage: $0

Update repos/mgmt/namespaces/registry PVC size for the lab Docker registry.

Env:
  REGISTRY_PVC_SIZE      request size (default: 1860Gi — max on 2T disk)
  REGISTRY_PVC_NAME      PVC name (default: registry-pvc)
  REGISTRY_STORAGE_CLASS StorageClass (default: local-path)

Requires StorageClass allowVolumeExpansion=true (see render_local_path_gitops.sh).

Push with:
  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Expand registry PVC' mgmt
EOF
  exit "${1:-0}"
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage 0

repo="$(cluster_gitea_repo_name mgmt)"
dir="${REPOS_DIR}/${repo}/namespaces/${REGISTRY_NS}"
mkdir -p "$dir"

cat >"${dir}/pvc-registry.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${REGISTRY_PVC_NAME}
  namespace: ${REGISTRY_NS}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: ${REGISTRY_PVC_SIZE}
  storageClassName: ${REGISTRY_STORAGE_CLASS}
EOF

echo "==> mgmt registry PVC ${REGISTRY_PVC_NAME}=${REGISTRY_PVC_SIZE} -> ${dir}/pvc-registry.yaml"
echo "Push with:"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Expand registry-pvc to ${REGISTRY_PVC_SIZE}' mgmt"
echo "Verify:"
echo "  kubectl --context mgmt@mgmt -n registry get pvc ${REGISTRY_PVC_NAME}"
