#!/usr/bin/env bash
# Render Grafana into repos/ for Config Sync GitOps.
# Edge: hostPort 3000 on CLUSTER_GRAFANA_NODE; address is grafana_vip
# (10.1.137.105). Add the /32 on that node first:
#   ./scripts/setup_grafana_secondary_ips.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
GRAFANA_NS="${GRAFANA_NS:-grafana}"
GRAFANA_NAME="${GRAFANA_NAME:-grafana}"
GRAFANA_IMAGE="${GRAFANA_IMAGE:-docker.io/grafana/grafana:11.5.2}"
GRAFANA_PVC_SIZE="${GRAFANA_PVC_SIZE:-5Gi}"
GRAFANA_STORAGE_CLASS="${GRAFANA_STORAGE_CLASS:-local-path}"
GRAFANA_HOST_PORT="${GRAFANA_HOST_PORT:-3000}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-inainfra}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-inainfra}"
# InfluxDB 2 datasource (in-cluster); must match render_influxdb_gitops.sh defaults.
INFLUX_NS="${INFLUX_NS:-influxdb}"
INFLUX_URL="${INFLUX_URL:-http://influxdb.${INFLUX_NS}.svc:8086}"
INFLUX_ORG="${INFLUX_ORG:-ina-infra}"
INFLUX_BUCKET="${INFLUX_BUCKET:-default}"
INFLUX_ADMIN_TOKEN="${INFLUX_ADMIN_TOKEN:-ina-infra-influxdb-token}"
PROM_URL="${PROM_URL:-http://prometheus.monitoring.svc:9090}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Default cluster: edge

Grafana on site L2 via hostPort ${GRAFANA_HOST_PORT} + /32 secondary:
  edge  http://$(grafana_vip edge):${GRAFANA_HOST_PORT}/

Secondary IP: ./scripts/setup_grafana_secondary_ips.sh
Push:         ./bringup/03_push_to_git_repos/push_git_repos.sh edge
Verify:       ./scripts/check-configsync.sh edge

Environment:
  GRAFANA_IMAGE GRAFANA_NS GRAFANA_PVC_SIZE GRAFANA_STORAGE_CLASS GRAFANA_HOST_PORT
  GRAFANA_ADMIN_USER GRAFANA_ADMIN_PASSWORD
  INFLUX_URL INFLUX_ORG INFLUX_BUCKET INFLUX_ADMIN_TOKEN PROM_URL
EOF
}

purge_grafana_manifests() {
  local dest_ns="$1"
  if [[ -d "$dest_ns" ]]; then
    find "$dest_ns" -maxdepth 1 -type f -name '*.yaml' -delete
  fi
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${GRAFANA_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
EOF
}

write_secret() {
  local dir="$1"
  cat >"${dir}/secret-${GRAFANA_NAME}-admin.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${GRAFANA_NAME}-admin
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
type: Opaque
stringData:
  admin-user: ${GRAFANA_ADMIN_USER}
  admin-password: ${GRAFANA_ADMIN_PASSWORD}
EOF
}

write_datasources() {
  local dir="$1"
  # Provisioned at /etc/grafana/provisioning/datasources
  cat >"${dir}/configmap-${GRAFANA_NAME}-datasources.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${GRAFANA_NAME}-datasources
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
data:
  datasources.yaml: |
    apiVersion: 1
    datasources:
      - name: InfluxDB
        type: influxdb
        access: proxy
        url: ${INFLUX_URL}
        isDefault: true
        jsonData:
          version: Flux
          organization: ${INFLUX_ORG}
          defaultBucket: ${INFLUX_BUCKET}
        secureJsonData:
          token: ${INFLUX_ADMIN_TOKEN}
      - name: Prometheus
        type: prometheus
        access: proxy
        url: ${PROM_URL}
        isDefault: false
        jsonData:
          httpMethod: POST
EOF
}

write_pvc() {
  local dir="$1"
  cat >"${dir}/persistentvolumeclaim-${GRAFANA_NAME}.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${GRAFANA_NAME}
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${GRAFANA_STORAGE_CLASS}
  resources:
    requests:
      storage: ${GRAFANA_PVC_SIZE}
EOF
}

write_deployment() {
  local dir="$1"
  local node="$2"
  cat >"${dir}/deployment-${GRAFANA_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${GRAFANA_NAME}
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ${GRAFANA_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${GRAFANA_NAME}
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${node}
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
      securityContext:
        fsGroup: 472
        runAsUser: 472
        runAsGroup: 472
      containers:
      - name: grafana
        image: ${GRAFANA_IMAGE}
        imagePullPolicy: IfNotPresent
        ports:
        - name: http
          containerPort: 3000
          hostPort: ${GRAFANA_HOST_PORT}
          protocol: TCP
        env:
        - name: GF_SECURITY_ADMIN_USER
          valueFrom:
            secretKeyRef:
              name: ${GRAFANA_NAME}-admin
              key: admin-user
        - name: GF_SECURITY_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: ${GRAFANA_NAME}-admin
              key: admin-password
        - name: GF_USERS_ALLOW_SIGN_UP
          value: "false"
        - name: GF_SERVER_ROOT_URL
          value: http://$(grafana_vip edge):${GRAFANA_HOST_PORT}/
        readinessProbe:
          httpGet:
            path: /api/health
            port: http
          initialDelaySeconds: 10
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /api/health
            port: http
          initialDelaySeconds: 30
          periodSeconds: 20
        resources:
          requests:
            cpu: 50m
            memory: 128Mi
          limits:
            cpu: "1"
            memory: 512Mi
        volumeMounts:
        - name: data
          mountPath: /var/lib/grafana
        - name: datasources
          mountPath: /etc/grafana/provisioning/datasources
          readOnly: true
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: ${GRAFANA_NAME}
      - name: datasources
        configMap:
          name: ${GRAFANA_NAME}-datasources
EOF
}

write_service() {
  local dir="$1"
  cat >"${dir}/service-${GRAFANA_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${GRAFANA_NAME}
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
spec:
  type: ClusterIP
  ports:
  - name: http
    port: 3000
    targetPort: http
    protocol: TCP
  selector:
    app.kubernetes.io/name: ${GRAFANA_NAME}
EOF
}

write_cluster_grafana() {
  local cluster="$1"
  local repo_name dest_ns vip node

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${GRAFANA_NS}"
  vip="$(grafana_vip "$cluster")"
  node="${CLUSTER_GRAFANA_NODE[$cluster]:-}"

  if [[ -z "$vip" || -z "$node" ]]; then
    echo "error: CLUSTER_GRAFANA_VIP/NODE unset for '${cluster}' (edge only today)" >&2
    exit 1
  fi

  mkdir -p "$dest_ns"
  purge_grafana_manifests "$dest_ns"

  write_namespace "$dest_ns"
  write_secret "$dest_ns"
  write_datasources "$dest_ns"
  write_pvc "$dest_ns"
  write_deployment "$dest_ns" "$node"
  write_service "$dest_ns"

  echo "==> [${cluster}] ${dest_ns}"
  echo "    UI: http://${vip}:${GRAFANA_HOST_PORT}/ on ${node} (need ${vip}/32 on site NIC)"
  echo "    user=${GRAFANA_ADMIN_USER} datasources=InfluxDB(${INFLUX_URL}) Prometheus(${PROM_URL})"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(edge)
  fi
  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      edge) ;;
      -h|--help) usage; exit 0 ;;
      *)
        echo "error: grafana render supports edge only (got '${cluster}')" >&2
        exit 1
        ;;
    esac
    write_cluster_grafana "$cluster"
  done
  echo
  echo "Secondary IP: ./scripts/setup_grafana_secondary_ips.sh ${clusters[*]}"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
