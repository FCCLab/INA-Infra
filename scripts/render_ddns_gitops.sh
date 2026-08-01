#!/usr/bin/env bash
# Render benjaminbear/docker-ddns-server into repos/ for Config Sync GitOps.
# Central: hostPort 53/tcp+udp + Web UI hostPort on CLUSTER_DDNS_NODE;
# address is ddns_vip (10.1.137.106). Add the /32 on that node first:
#   ./scripts/setup_ddns_secondary_ips.sh
#
# Upstream: https://github.com/benjaminbear/docker-ddns-server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
DDNS_NS="${DDNS_NS:-ddns}"
DDNS_NAME="${DDNS_NAME:-ddns}"
DDNS_IMAGE="${DDNS_IMAGE:-docker.io/bbaerthlein/docker-ddns-server:latest}"
DDNS_PVC_SIZE="${DDNS_PVC_SIZE:-2Gi}"
DDNS_STORAGE_CLASS="${DDNS_STORAGE_CLASS:-local-path}"
# Web UI container listens on 8080; host 8080 is taken on central-0 (node).
DDNS_UI_HOST_PORT="${DDNS_UI_HOST_PORT:-8088}"
# Zone = <cluster>.inainfra (override with DDNS_DOMAINS / DDNS_PARENT_NS).
DDNS_DEFAULT_TTL="${DDNS_DEFAULT_TTL:-3600}"
DDNS_ALLOW_WILDCARD="${DDNS_ALLOW_WILDCARD:-true}"
# htpasswd -nb inainfra inainfra (apr1). Override via DDNS_ADMIN_LOGIN.
DDNS_ADMIN_LOGIN="${DDNS_ADMIN_LOGIN:-inainfra:\$apr1\$zX2Gi1GU\$qwEYs.YQzERhHXy6hrQN1.}"

ddns_domain_for() {
  local cluster="$1"
  printf '%s.inainfra' "$cluster"
}

ddns_parent_ns_for() {
  local cluster="$1"
  printf 'ns.%s.inainfra' "$cluster"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Default cluster: central

DynDNS ([docker-ddns-server](https://github.com/benjaminbear/docker-ddns-server))
on site L2 via hostPort 53 + UI ${DDNS_UI_HOST_PORT}:
  central  DNS $(ddns_vip central):53  zone $(ddns_domain_for central)
           UI  http://$(ddns_vip central):${DDNS_UI_HOST_PORT}/

Secondary IP: ./scripts/setup_ddns_secondary_ips.sh
Push:         ./bringup/03_push_to_git_repos/push_git_repos.sh central
Verify:       ./scripts/check-configsync.sh central

Environment:
  DDNS_IMAGE DDNS_NS DDNS_UI_HOST_PORT DDNS_DOMAINS DDNS_PARENT_NS
  DDNS_DEFAULT_TTL DDNS_ALLOW_WILDCARD DDNS_ADMIN_LOGIN
EOF
}

purge_ddns_manifests() {
  local dest_ns="$1"
  if [[ -d "$dest_ns" ]]; then
    find "$dest_ns" -maxdepth 1 -type f -name '*.yaml' -delete
  fi
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${DDNS_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
EOF
}

write_secret() {
  local dir="$1"
  # Quote login so YAML keeps literal $ in the apr1 hash.
  cat >"${dir}/secret-${DDNS_NAME}-admin.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${DDNS_NAME}-admin
  namespace: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
type: Opaque
stringData:
  DDNS_ADMIN_LOGIN: '${DDNS_ADMIN_LOGIN}'
EOF
}

write_pvc() {
  local dir="$1"
  cat >"${dir}/persistentvolumeclaim-${DDNS_NAME}-bind.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${DDNS_NAME}-bind
  namespace: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${DDNS_STORAGE_CLASS}
  resources:
    requests:
      storage: ${DDNS_PVC_SIZE}
EOF
  cat >"${dir}/persistentvolumeclaim-${DDNS_NAME}-db.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${DDNS_NAME}-db
  namespace: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${DDNS_STORAGE_CLASS}
  resources:
    requests:
      storage: ${DDNS_PVC_SIZE}
EOF
}

write_deployment() {
  local dir="$1"
  local node="$2"
  local domains="$3"
  local parent_ns="$4"
  cat >"${dir}/deployment-${DDNS_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${DDNS_NAME}
  namespace: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ${DDNS_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${DDNS_NAME}
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${node}
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
      containers:
      - name: ddns
        image: ${DDNS_IMAGE}
        imagePullPolicy: IfNotPresent
        ports:
        - name: dns-tcp
          containerPort: 53
          hostPort: 53
          protocol: TCP
        - name: dns-udp
          containerPort: 53
          hostPort: 53
          protocol: UDP
        - name: http
          containerPort: 8080
          hostPort: ${DDNS_UI_HOST_PORT}
          protocol: TCP
        env:
        - name: DDNS_ADMIN_LOGIN
          valueFrom:
            secretKeyRef:
              name: ${DDNS_NAME}-admin
              key: DDNS_ADMIN_LOGIN
        - name: DDNS_DOMAINS
          value: "${domains}"
        - name: DDNS_PARENT_NS
          value: "${parent_ns}"
        - name: DDNS_DEFAULT_TTL
          value: "${DDNS_DEFAULT_TTL}"
        - name: DDNS_ALLOW_WILDCARD
          value: "${DDNS_ALLOW_WILDCARD}"
        readinessProbe:
          tcpSocket:
            port: http
          initialDelaySeconds: 10
          periodSeconds: 10
        livenessProbe:
          tcpSocket:
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
        - name: bind
          mountPath: /var/cache/bind
        - name: database
          mountPath: /root/database
      volumes:
      - name: bind
        persistentVolumeClaim:
          claimName: ${DDNS_NAME}-bind
      - name: database
        persistentVolumeClaim:
          claimName: ${DDNS_NAME}-db
EOF
}

write_service() {
  local dir="$1"
  cat >"${dir}/service-${DDNS_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${DDNS_NAME}
  namespace: ${DDNS_NS}
  labels:
    app.kubernetes.io/name: ${DDNS_NAME}
spec:
  type: ClusterIP
  ports:
  - name: http
    port: 8080
    targetPort: http
    protocol: TCP
  - name: dns-tcp
    port: 53
    targetPort: dns-tcp
    protocol: TCP
  - name: dns-udp
    port: 53
    targetPort: dns-udp
    protocol: UDP
  selector:
    app.kubernetes.io/name: ${DDNS_NAME}
EOF
}

write_cluster_ddns() {
  local cluster="$1"
  local repo_name dest_ns vip node domains parent_ns

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${DDNS_NS}"
  vip="$(ddns_vip "$cluster")"
  node="${CLUSTER_DDNS_NODE[$cluster]:-}"
  domains="${DDNS_DOMAINS:-$(ddns_domain_for "$cluster")}"
  parent_ns="${DDNS_PARENT_NS:-$(ddns_parent_ns_for "$cluster")}"

  if [[ -z "$vip" || -z "$node" ]]; then
    echo "error: CLUSTER_DDNS_VIP/NODE unset for '${cluster}' (central only today)" >&2
    exit 1
  fi

  mkdir -p "$dest_ns"
  purge_ddns_manifests "$dest_ns"

  write_namespace "$dest_ns"
  write_secret "$dest_ns"
  write_pvc "$dest_ns"
  write_deployment "$dest_ns" "$node" "$domains" "$parent_ns"
  write_service "$dest_ns"

  echo "==> [${cluster}] ${dest_ns}"
  echo "    DNS: ${vip}:53 on ${node} (need ${vip}/32 on site NIC)"
  echo "    UI:  http://${vip}:${DDNS_UI_HOST_PORT}/  zone=${domains} ns=${parent_ns}"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi
  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      central) ;;
      -h|--help) usage; exit 0 ;;
      *)
        echo "error: ddns render supports central only (got '${cluster}')" >&2
        exit 1
        ;;
    esac
    write_cluster_ddns "$cluster"
  done
  echo
  echo "Secondary IP: ./scripts/setup_ddns_secondary_ips.sh ${clusters[*]}"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
