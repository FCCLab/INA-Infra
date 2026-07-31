#!/usr/bin/env bash
# Render an annotated Service so per-cluster Prometheus scrapes nvidia-dcgm-exporter.
# GPU Operator leaves ServiceMonitor disabled; this Service uses prometheus.io/*
# annotations and a port named "metrics" (matches kubernetes-service-endpoints SD).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
GPU_NS="${GPU_NS:-gpu-operator}"
SVC_NAME="${SVC_NAME:-nvidia-dcgm-exporter-metrics}"
DCGM_PORT="${DCGM_PORT:-9400}"
# Clusters that run GPU Operator (GH200 workers).
DEFAULT_GPU_CLUSTERS=(central edge)

write_dcgm_scrape_service() {
  local cluster="$1"
  local repo_name dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${GPU_NS}"
  mkdir -p "$dest_ns"

  cat >"${dest_ns}/service-${SVC_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${SVC_NAME}
  namespace: ${GPU_NS}
  labels:
    app.kubernetes.io/name: nvidia-dcgm-exporter
    app.kubernetes.io/component: metrics
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "${DCGM_PORT}"
    prometheus.io/path: "/metrics"
spec:
  type: ClusterIP
  ports:
    - name: metrics
      port: ${DCGM_PORT}
      targetPort: ${DCGM_PORT}
      protocol: TCP
  selector:
    app: nvidia-dcgm-exporter
EOF

  echo "==> [${cluster}] ${dest_ns}/service-${SVC_NAME}.yaml"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=("${DEFAULT_GPU_CLUSTERS[@]}")
  fi
  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      central|edge) ;;
      *)
        echo "error: DCGM scrape only for central|edge (got '${cluster}')" >&2
        exit 1
        ;;
    esac
    write_dcgm_scrape_service "$cluster"
  done
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [central|edge ...]

Write annotated Service ${SVC_NAME} in ${GPU_NS} so Prometheus scrapes DCGM.

Default clusters: ${DEFAULT_GPU_CLUSTERS[*]}
EOF
  exit 0
fi

main "$@"
