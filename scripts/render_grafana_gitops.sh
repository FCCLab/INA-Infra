#!/usr/bin/env bash
# Render Grafana into repos/ for Config Sync GitOps.
# Edge: Multus macvlan on site L2 (10.1.137.105) + ClusterIP for in-cluster.
# No hostPort / host /32 — remove leftovers with:
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
GRAFANA_PORT="${GRAFANA_PORT:-3000}"
GRAFANA_NAD_NAME="${GRAFANA_NAD_NAME:-grafana-site}"
GRAFANA_IFACE="${GRAFANA_IFACE:-site}"
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

Grafana on site L2 via Multus macvlan (no hostPort):
  edge  http://$(grafana_vip edge):${GRAFANA_PORT}/

Cleanup host /32: ./scripts/setup_grafana_secondary_ips.sh
Push:            ./bringup/03_push_to_git_repos/push_git_repos.sh edge
Verify:          ./scripts/check-configsync.sh edge

Environment:
  GRAFANA_IMAGE GRAFANA_NS GRAFANA_PVC_SIZE GRAFANA_STORAGE_CLASS GRAFANA_PORT
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

# Multus NAD: macvlan on site NIC, static /24, no gateway (keep flannel default).
write_nad() {
  local dir="$1"
  local master="$2"
  local nad_config

  # No default gw on site iface (keep flannel). Route mgmt plane back via site gw.
  nad_config="$(python3 -c "
import json
cfg = {
  'cniVersion': '0.3.1',
  'name': '${GRAFANA_NAD_NAME}',
  'plugins': [
    {
      'type': 'macvlan',
      'capabilities': {'ips': True},
      'master': '${master}',
      'mode': 'bridge',
      'ipam': {
        'type': 'static',
        'routes': [{'dst': '10.1.132.0/24', 'gw': '10.1.137.1'}],
      },
    },
    {
      'type': 'tuning',
      'capabilities': {'mac': True},
      'ipam': {},
      'sysctl': {
        'net.ipv4.conf.IFNAME.arp_ignore': '1',
        'net.ipv4.conf.IFNAME.arp_announce': '2',
      },
    },
  ],
}
print(json.dumps(cfg))
")"

  cat >"${dir}/network-attachment-definition-${GRAFANA_NAD_NAME}.yaml" <<EOF
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ${GRAFANA_NAD_NAME}
  namespace: ${GRAFANA_NS}
  labels:
    app.kubernetes.io/name: ${GRAFANA_NAME}
spec:
  config: '${nad_config}'
EOF
}

write_deployment() {
  local dir="$1"
  local node="$2"
  local vip="$3"
  local networks_json

  networks_json="$(python3 -c "
import json
print(json.dumps([{
  'name': '${GRAFANA_NAD_NAME}',
  'interface': '${GRAFANA_IFACE}',
  'ips': ['${vip}/24'],
  'routes': [{'dst': '10.1.132.0/24', 'gw': '10.1.137.1'}],
}]))
")"

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
      annotations:
        k8s.v1.cni.cncf.io/networks: '${networks_json}'
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
      initContainers:
      - name: setup-pbr
        image: busybox:latest
        imagePullPolicy: IfNotPresent
        securityContext:
          runAsUser: 0
          privileged: true
        command:
        - sh
        - -c
        - |
          ip route add default via 10.1.137.1 dev ${GRAFANA_IFACE} table 100 || true
          ip rule add from ${vip} lookup 100 || true
      containers:
      - name: grafana
        image: ${GRAFANA_IMAGE}
        imagePullPolicy: IfNotPresent
        ports:
        - name: http
          containerPort: ${GRAFANA_PORT}
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
          value: http://${vip}:${GRAFANA_PORT}/
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
    port: ${GRAFANA_PORT}
    targetPort: http
    protocol: TCP
  selector:
    app.kubernetes.io/name: ${GRAFANA_NAME}
EOF
}

write_cluster_grafana() {
  local cluster="$1"
  local repo_name dest_ns vip node master

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${GRAFANA_NS}"
  vip="$(grafana_vip "$cluster")"
  node="${CLUSTER_GRAFANA_NODE[$cluster]:-}"
  master="${SITE_IFACE}"

  if [[ -z "$vip" || -z "$node" ]]; then
    echo "error: CLUSTER_GRAFANA_VIP/NODE unset for '${cluster}' (edge only today)" >&2
    exit 1
  fi

  mkdir -p "$dest_ns"
  purge_grafana_manifests "$dest_ns"

  write_namespace "$dest_ns"
  write_nad "$dest_ns" "$master"
  write_secret "$dest_ns"
  write_datasources "$dest_ns"
  write_pvc "$dest_ns"
  write_deployment "$dest_ns" "$node" "$vip"
  write_service "$dest_ns"

  echo "==> [${cluster}] ${dest_ns}"
  echo "    Multus macvlan ${vip}/24 on ${master} (${GRAFANA_IFACE}); node=${node}"
  echo "    UI: http://${vip}:${GRAFANA_PORT}/"
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
  echo "Cleanup host /32: ./scripts/setup_grafana_secondary_ips.sh ${clusters[*]}"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
