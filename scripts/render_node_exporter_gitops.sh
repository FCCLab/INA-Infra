#!/usr/bin/env bash
# Render node_exporter DaemonSet into repos/ for Config Sync GitOps.
# Scraped by per-cluster Prometheus via pod annotations (prometheus.io/scrape).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
NE_NS="${NE_NS:-monitoring}"
NE_NAME="${NE_NAME:-node-exporter}"
NE_IMAGE="${NE_IMAGE:-docker.io/prom/node-exporter:v1.8.2}"
# 9101 avoids conflict with any host-installed node_exporter on :9100 (seen on edge-3).
NE_PORT="${NE_PORT:-9101}"

purge_node_exporter() {
  local dest_ns="$1"
  local f
  for f in \
    "${dest_ns}/daemonset-${NE_NAME}.yaml" \
    "${dest_ns}/serviceaccount-${NE_NAME}.yaml" \
    "${dest_ns}/service-${NE_NAME}.yaml"; do
    if [[ -f "$f" ]]; then
      rm -f "$f"
    fi
  done
}

write_cluster_node_exporter() {
  local cluster="$1"
  local repo_name dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${NE_NS}"
  mkdir -p "$dest_ns"
  purge_node_exporter "$dest_ns"

  cat >"${dest_ns}/serviceaccount-${NE_NAME}.yaml" <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${NE_NAME}
  namespace: ${NE_NS}
  labels:
    app.kubernetes.io/name: ${NE_NAME}
EOF

  cat >"${dest_ns}/daemonset-${NE_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${NE_NAME}
  namespace: ${NE_NS}
  labels:
    app.kubernetes.io/name: ${NE_NAME}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: ${NE_NAME}
  updateStrategy:
    type: RollingUpdate
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${NE_NAME}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "${NE_PORT}"
        prometheus.io/path: "/metrics"
    spec:
      serviceAccountName: ${NE_NAME}
      hostNetwork: true
      hostPID: true
      dnsPolicy: ClusterFirstWithHostNet
      tolerations:
        - operator: Exists
      containers:
        - name: node-exporter
          image: ${NE_IMAGE}
          imagePullPolicy: IfNotPresent
          args:
            - --path.procfs=/host/proc
            - --path.sysfs=/host/sys
            - --path.rootfs=/host/root
            - --web.listen-address=:${NE_PORT}
            - --collector.filesystem.mount-points-exclude=^/(dev|proc|sys|var/lib/docker/.+|var/lib/kubelet/.+)(\$|/)
            - --collector.netclass.ignored-devices=^(veth.*|cni.*|flannel.*|docker.*|calico.*|tunl.*|vxlan.*|kube-ipvs.*)$
            - --collector.netdev.device-exclude=^(veth.*|cni.*|flannel.*|docker.*|calico.*|tunl.*|vxlan.*|kube-ipvs.*)$
          ports:
            - name: metrics
              containerPort: ${NE_PORT}
              protocol: TCP
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              add:
                - SYS_TIME
              drop:
                - ALL
            readOnlyRootFilesystem: true
          volumeMounts:
            - name: proc
              mountPath: /host/proc
              readOnly: true
            - name: sys
              mountPath: /host/sys
              readOnly: true
            - name: root
              mountPath: /host/root
              mountPropagation: HostToContainer
              readOnly: true
      volumes:
        - name: proc
          hostPath:
            path: /proc
        - name: sys
          hostPath:
            path: /sys
        - name: root
          hostPath:
            path: /
EOF

  cat >"${dest_ns}/service-${NE_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${NE_NAME}
  namespace: ${NE_NS}
  labels:
    app.kubernetes.io/name: ${NE_NAME}
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "${NE_PORT}"
    prometheus.io/path: "/metrics"
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: metrics
      port: ${NE_PORT}
      targetPort: metrics
      protocol: TCP
  selector:
    app.kubernetes.io/name: ${NE_NAME}
EOF

  echo "==> [${cluster}] ${dest_ns} (${NE_NAME}:${NE_PORT})"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt central regional edge)
  fi
  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      mgmt|central|regional|edge) ;;
      *)
        echo "error: unknown cluster '${cluster}'" >&2
        exit 1
        ;;
    esac
    write_cluster_node_exporter "$cluster"
  done
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write node_exporter DaemonSet (+ SA + headless Service) into
repos/<gitea-repo>/namespaces/${NE_NS}/ for Config Sync.

Default clusters: mgmt central regional edge

Environment:
  NE_IMAGE   Image (default: ${NE_IMAGE})
  NE_NS      Namespace (default: ${NE_NS})
  NE_PORT    Listen / scrape port (default: ${NE_PORT})
EOF
  exit 0
fi

main "$@"
