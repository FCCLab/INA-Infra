#!/usr/bin/env bash
# Render InfluxDB into repos/ for Config Sync GitOps.
# Edge: Multus macvlan on site L2 (10.1.137.104) + ClusterIP for in-cluster.
# No hostPort / host /32 — remove leftovers with:
#   ./scripts/setup_influxdb_secondary_ips.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
INFLUX_NS="${INFLUX_NS:-influxdb}"
INFLUX_NAME="${INFLUX_NAME:-influxdb}"
INFLUX_IMAGE="${INFLUX_IMAGE:-docker.io/library/influxdb:2.7}"
INFLUX_PVC_SIZE="${INFLUX_PVC_SIZE:-1900Gi}"
INFLUX_STORAGE_CLASS="${INFLUX_STORAGE_CLASS:-local-path}"
INFLUX_PORT="${INFLUX_PORT:-8086}"
INFLUX_NAD_NAME="${INFLUX_NAD_NAME:-influxdb-site}"
INFLUX_IFACE="${INFLUX_IFACE:-site}"
# Lab defaults (override via env). Token is also the API auth for writers.
INFLUX_ADMIN_USER="${INFLUX_ADMIN_USER:-inainfra}"
INFLUX_ADMIN_PASSWORD="${INFLUX_ADMIN_PASSWORD:-inainfra}"
INFLUX_ORG="${INFLUX_ORG:-ina-infra}"
INFLUX_BUCKET="${INFLUX_BUCKET:-default}"
INFLUX_ADMIN_TOKEN="${INFLUX_ADMIN_TOKEN:-ina-infra-influxdb-token}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Default cluster: edge

InfluxDB on site L2 via Multus macvlan (no hostPort):
  edge  http://$(influxdb_vip edge):${INFLUX_PORT}/

Cleanup host /32: ./scripts/setup_influxdb_secondary_ips.sh
Push:            ./bringup/03_push_to_git_repos/push_git_repos.sh edge
Verify:          ./scripts/check-configsync.sh edge

Environment:
  INFLUX_IMAGE INFLUX_NS INFLUX_PVC_SIZE INFLUX_STORAGE_CLASS INFLUX_PORT
  INFLUX_ADMIN_USER INFLUX_ADMIN_PASSWORD INFLUX_ORG INFLUX_BUCKET INFLUX_ADMIN_TOKEN
EOF
}

purge_influxdb_manifests() {
  local dest_ns="$1"
  if [[ -d "$dest_ns" ]]; then
    find "$dest_ns" -maxdepth 1 -type f -name '*.yaml' -delete
  fi
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${INFLUX_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
EOF
}

write_secret() {
  local dir="$1"
  cat >"${dir}/secret-${INFLUX_NAME}-init.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${INFLUX_NAME}-init
  namespace: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
type: Opaque
stringData:
  DOCKER_INFLUXDB_INIT_USERNAME: ${INFLUX_ADMIN_USER}
  DOCKER_INFLUXDB_INIT_PASSWORD: ${INFLUX_ADMIN_PASSWORD}
  DOCKER_INFLUXDB_INIT_ORG: ${INFLUX_ORG}
  DOCKER_INFLUXDB_INIT_BUCKET: ${INFLUX_BUCKET}
  DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: ${INFLUX_ADMIN_TOKEN}
EOF
}

# Preserve bound volumeName from previous GitOps YAML (avoids KNV2009 on re-render).
_preserve_pvc_volume_name() {
  local new_file="$1"
  local old_file="${2:-}"
  [[ -n "$old_file" && -f "$old_file" ]] || return 0
  local vn
  vn="$(awk '/^[[:space:]]*volumeName:/{print $2; exit}' "$old_file")"
  [[ -n "$vn" ]] || return 0
  if grep -q '^[[:space:]]*volumeName:' "$new_file"; then
    sed -i "s|^[[:space:]]*volumeName:.*|  volumeName: ${vn}|" "$new_file"
  else
    sed -i "/^[[:space:]]*storageClassName:/a\\  volumeName: ${vn}" "$new_file"
  fi
}

write_pvc() {
  local dir="$1"
  # Bound local-path PVCs get volumeName set by the provisioner; Config Sync must
  # not try to clear it (KNV2009). Mutation-ignore stops post-create updates.
  local pvc="${dir}/persistentvolumeclaim-${INFLUX_NAME}.yaml"
  local pvc_old=""
  [[ -f "$pvc" ]] && pvc_old="$(mktemp)" && cp "$pvc" "$pvc_old"
  cat >"${pvc}" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${INFLUX_NAME}
  namespace: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
  annotations:
    client.lifecycle.config.k8s.io/mutation: ignore
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${INFLUX_STORAGE_CLASS}
  resources:
    requests:
      storage: ${INFLUX_PVC_SIZE}
EOF
  _preserve_pvc_volume_name "$pvc" "${pvc_old:-}"
  [[ -n "${pvc_old:-}" ]] && rm -f "$pvc_old"
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
  'name': '${INFLUX_NAD_NAME}',
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

  cat >"${dir}/network-attachment-definition-${INFLUX_NAD_NAME}.yaml" <<EOF
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ${INFLUX_NAD_NAME}
  namespace: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
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
  'name': '${INFLUX_NAD_NAME}',
  'interface': '${INFLUX_IFACE}',
  'ips': ['${vip}/24'],
  'routes': [{'dst': '10.1.132.0/24', 'gw': '10.1.137.1'}],
}]))
")"

  cat >"${dir}/deployment-${INFLUX_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${INFLUX_NAME}
  namespace: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ${INFLUX_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${INFLUX_NAME}
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
        fsGroup: 1000
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
          ip route add default via 10.1.137.1 dev ${INFLUX_IFACE} table 100 || true
          ip rule add from ${vip} lookup 100 || true
      containers:
      - name: influxdb
        image: ${INFLUX_IMAGE}
        imagePullPolicy: IfNotPresent
        ports:
        - name: http
          containerPort: ${INFLUX_PORT}
          protocol: TCP
        env:
        - name: DOCKER_INFLUXDB_INIT_MODE
          value: setup
        - name: DOCKER_INFLUXDB_INIT_USERNAME
          valueFrom:
            secretKeyRef:
              name: ${INFLUX_NAME}-init
              key: DOCKER_INFLUXDB_INIT_USERNAME
        - name: DOCKER_INFLUXDB_INIT_PASSWORD
          valueFrom:
            secretKeyRef:
              name: ${INFLUX_NAME}-init
              key: DOCKER_INFLUXDB_INIT_PASSWORD
        - name: DOCKER_INFLUXDB_INIT_ORG
          valueFrom:
            secretKeyRef:
              name: ${INFLUX_NAME}-init
              key: DOCKER_INFLUXDB_INIT_ORG
        - name: DOCKER_INFLUXDB_INIT_BUCKET
          valueFrom:
            secretKeyRef:
              name: ${INFLUX_NAME}-init
              key: DOCKER_INFLUXDB_INIT_BUCKET
        - name: DOCKER_INFLUXDB_INIT_ADMIN_TOKEN
          valueFrom:
            secretKeyRef:
              name: ${INFLUX_NAME}-init
              key: DOCKER_INFLUXDB_INIT_ADMIN_TOKEN
        readinessProbe:
          httpGet:
            path: /health
            port: http
          initialDelaySeconds: 10
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /health
            port: http
          initialDelaySeconds: 30
          periodSeconds: 20
        resources:
          requests:
            cpu: 100m
            memory: 512Mi
          limits:
            cpu: "2"
            memory: 2Gi
        volumeMounts:
        - name: data
          mountPath: /var/lib/influxdb2
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: ${INFLUX_NAME}
EOF
}

write_service() {
  local dir="$1"
  cat >"${dir}/service-${INFLUX_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${INFLUX_NAME}
  namespace: ${INFLUX_NS}
  labels:
    app.kubernetes.io/name: ${INFLUX_NAME}
spec:
  type: ClusterIP
  ports:
  - name: http
    port: ${INFLUX_PORT}
    targetPort: http
    protocol: TCP
  selector:
    app.kubernetes.io/name: ${INFLUX_NAME}
EOF
}

write_cluster_influxdb() {
  local cluster="$1"
  local repo_name dest_ns vip node master

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${INFLUX_NS}"
  vip="$(influxdb_vip "$cluster")"
  node="${CLUSTER_INFLUXDB_NODE[$cluster]:-}"
  master="${SITE_IFACE}"

  if [[ -z "$vip" || -z "$node" ]]; then
    echo "error: CLUSTER_INFLUXDB_VIP/NODE unset for '${cluster}' (edge only today)" >&2
    exit 1
  fi

  mkdir -p "$dest_ns"
  purge_influxdb_manifests "$dest_ns"

  write_namespace "$dest_ns"
  write_nad "$dest_ns" "$master"
  write_secret "$dest_ns"
  write_pvc "$dest_ns"
  write_deployment "$dest_ns" "$node" "$vip"
  write_service "$dest_ns"

  echo "==> [${cluster}] ${dest_ns}"
  echo "    Multus macvlan ${vip}/24 on ${master} (${INFLUX_IFACE}); node=${node}"
  echo "    UI/API: http://${vip}:${INFLUX_PORT}/"
  echo "    org=${INFLUX_ORG} bucket=${INFLUX_BUCKET} user=${INFLUX_ADMIN_USER}"
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
        echo "error: influxdb render supports edge only (got '${cluster}')" >&2
        exit 1
        ;;
    esac
    write_cluster_influxdb "$cluster"
  done
  echo
  echo "Cleanup host /32: ./scripts/setup_influxdb_secondary_ips.sh ${clusters[*]}"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
