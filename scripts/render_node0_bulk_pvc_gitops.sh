#!/usr/bin/env bash
# Render a large local-path PVC + binder pod pinned to each cluster's CP node-0
# (/opt/local-path-provisioner on the 2T /dev/vdb disk).
#
# local-path uses WaitForFirstConsumer — a tiny binder Deployment is required so
# the volume is created on <cluster>-0 (not a worker).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
BULK_NS="${BULK_NS:-local-bulk}"
BULK_PVC_NAME="${BULK_PVC_NAME:-node0-bulk}"
BULK_SIZE="${BULK_SIZE:-1800Gi}"
BULK_STORAGE_CLASS="${BULK_STORAGE_CLASS:-local-path}"
BULK_IMAGE="${BULK_IMAGE:-registry.k8s.io/pause:3.9}"

if [[ $# -gt 0 ]]; then
  CLUSTERS=("$@")
else
  CLUSTERS=(central regional edge ue)
fi

usage() {
  cat <<EOF
Usage: $0 [cluster ...]

Write PVC + binder Deployment for node-0 bulk storage (2T local-path disk).

Defaults: clusters = central regional edge ue
  BULK_NS            namespace (default: local-bulk)
  BULK_PVC_NAME      PVC name (default: node0-bulk)
  BULK_SIZE          request size (default: 1800Gi)
  BULK_STORAGE_CLASS StorageClass (default: local-path)

Push with:
  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'node0 bulk PVC' central regional edge ue
EOF
  exit "${1:-0}"
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage 0

write_cluster() {
  local cluster="$1"
  local repo node dir
  repo="$(cluster_gitea_repo_name "$cluster")"
  node="${CLUSTER_CP_HOST[$cluster]}"
  dir="${REPOS_DIR}/${repo}/namespaces/${BULK_NS}"
  mkdir -p "$dir"
  rm -f "${dir}"/*.yaml

  cat >"${dir}/namespace-${BULK_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${BULK_NS}
  labels:
    app.kubernetes.io/name: ${BULK_NS}
EOF

  cat >"${dir}/10-persistentvolumeclaim-${BULK_PVC_NAME}.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${BULK_PVC_NAME}
  namespace: ${BULK_NS}
  labels:
    app.kubernetes.io/name: ${BULK_PVC_NAME}
    nephio.org/bulk-node: "${node}"
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${BULK_STORAGE_CLASS}
  resources:
    requests:
      storage: ${BULK_SIZE}
EOF

  # Binder keeps the PVC bound on node-0's 2T disk (WaitForFirstConsumer).
  cat >"${dir}/20-deployment-${BULK_PVC_NAME}-binder.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${BULK_PVC_NAME}-binder
  namespace: ${BULK_NS}
  labels:
    app.kubernetes.io/name: ${BULK_PVC_NAME}-binder
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: ${BULK_PVC_NAME}-binder
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${BULK_PVC_NAME}-binder
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${node}
      containers:
        - name: pause
          image: ${BULK_IMAGE}
          imagePullPolicy: IfNotPresent
          volumeMounts:
            - name: bulk
              mountPath: /data
          resources:
            requests:
              cpu: 1m
              memory: 8Mi
            limits:
              cpu: 10m
              memory: 16Mi
      volumes:
        - name: bulk
          persistentVolumeClaim:
            claimName: ${BULK_PVC_NAME}
EOF

  echo "  ${cluster}: ${dir}  PVC ${BULK_PVC_NAME}=${BULK_SIZE} @ ${node}"
}

echo "==> node0 bulk PVC (ns=${BULK_NS} size=${BULK_SIZE})"
for cluster in "${CLUSTERS[@]}"; do
  if [[ -z "${CLUSTER_CP_HOST[$cluster]+x}" ]]; then
    echo "Unknown cluster: ${cluster}" >&2
    usage 1
  fi
  write_cluster "$cluster"
done
echo "Done. Push with:"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Add node0 bulk PVC on 2T local-path' ${CLUSTERS[*]}"
