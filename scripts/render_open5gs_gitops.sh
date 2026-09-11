#!/usr/bin/env bash
# Render Open5GS 5GC (FCCLab/5gc-open5gs) into repos/ for Config Sync GitOps.
# Edge: privileged pod + Multus macvlan on site L2 (10.1.137.107).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OPEN5GS_NS="${OPEN5GS_NS:-open5gs}"
OPEN5GS_NAME="${OPEN5GS_NAME:-open5gs-5gc}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${OPEN5GS_TAG:-v2.7.0}"
OPEN5GS_IMAGE="${OPEN5GS_IMAGE:-${REGISTRY}/open5gs/5gc:${TAG}}"
OPEN5GS_NAD_NAME="${OPEN5GS_NAD_NAME:-open5gs-site}"
OPEN5GS_IFACE="${OPEN5GS_IFACE:-site}"
SUBSCRIBER_DB="${SUBSCRIBER_DB:-$REPO_ROOT/services/open5gs/subscriber_db.csv}"
WEBUI_PORT="${WEBUI_PORT:-9999}"
NGAP_PORT="${NGAP_PORT:-38412}"
GTPU_PORT="${GTPU_PORT:-2152}"
UE_IP_BASE="${UE_IP_BASE:-10.45.0}"
TZ_VALUE="${OPEN5GS_TZ:-Europe/Madrid}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [edge-1|edge-2|edge]

Render Open5GS 5GC into repos/edge-repo/namespaces/${OPEN5GS_NS}.

Default node: $(open5gs_node edge)  VIP: $(open5gs_vip edge)
  edge-1   pin to cpu-edge-1 (enp7s0) — default; currently the only Ready worker
  edge-2   pin to edge-2 (eno1) — node is not in the cluster today (pod will Pending)

Image: ${OPEN5GS_IMAGE}

Build/push first:
  ./services/open5gs/build_push.sh

RAN (srsRAN / gNB) must use PLMN 001/01 and TAC 81:
  AMF NGAP  $(open5gs_vip edge):${NGAP_PORT}/sctp
  UPF GTP-U $(open5gs_vip edge):${GTPU_PORT}/udp
  Web UI    kubectl --context edge@edge -n open5gs port-forward svc/open5gs-5gc ${WEBUI_PORT}:${WEBUI_PORT}
EOF
}

purge_ns() {
  local dest="$1"
  if [[ -d "$dest" ]]; then
    find "$dest" -maxdepth 1 -type f -name '*.yaml' -delete
  fi
}

resolve_node() {
  local arg="${1:-}"
  case "$arg" in
    ""|edge|edge-1|cpu-edge-1)
      printf 'cpu-edge-1'
      ;;
    edge-2)
      printf 'edge-2'
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: node must be edge-1 or edge-2 (got '${arg}')" >&2
      usage >&2
      exit 1
      ;;
  esac
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${OPEN5GS_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
EOF
}

write_nad() {
  local dir="$1"
  local master="$2"
  local nad_config

  nad_config="$(python3 -c "
import json
cfg = {
  'cniVersion': '0.3.1',
  'name': '${OPEN5GS_NAD_NAME}',
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

  cat >"${dir}/network-attachment-definition-${OPEN5GS_NAD_NAME}.yaml" <<EOF
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ${OPEN5GS_NAD_NAME}
  namespace: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
spec:
  config: '${nad_config}'
EOF
}

write_configmaps() {
  local dir="$1"
  local vip="$2"
  local csv_body
  csv_body="$(sed 's/^/    /' "$SUBSCRIBER_DB")"

  cat >"${dir}/configmap-${OPEN5GS_NAME}-env.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${OPEN5GS_NAME}-env
  namespace: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
data:
  MONGODB_IP: "127.0.0.1"
  OPEN5GS_IP: "${vip}"
  NGAP_BIND_ADDR: "0.0.0.0"
  AMF_LOG_LEVEL: "info"
  UE_IP_BASE: "${UE_IP_BASE}"
  N6_IP: "${vip}"
  UPF_ADVERTISE_IP: "${vip}"
  DEBUG: "false"
  SUBSCRIBER_DB: "subscriber_db.csv"
  NETWORK_NAME_FULL: "srsRAN"
  NETWORK_NAME_SHORT: "srsRAN"
  TZ: "${TZ_VALUE}"
EOF

  cat >"${dir}/configmap-${OPEN5GS_NAME}-subscribers.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${OPEN5GS_NAME}-subscribers
  namespace: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
data:
  subscriber_db.csv: |
${csv_body}
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
  'name': '${OPEN5GS_NAD_NAME}',
  'interface': '${OPEN5GS_IFACE}',
  'ips': ['${vip}/24'],
  'routes': [{'dst': '10.1.132.0/24', 'gw': '10.1.137.1'}],
}]))
")"

  cat >"${dir}/deployment-${OPEN5GS_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${OPEN5GS_NAME}
  namespace: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ${OPEN5GS_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${OPEN5GS_NAME}
      annotations:
        k8s.v1.cni.cncf.io/networks: '${networks_json}'
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${node}
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
      initContainers:
      - name: setup-pbr
        image: docker.io/library/busybox:1.36
        imagePullPolicy: IfNotPresent
        securityContext:
          runAsUser: 0
          privileged: true
        command:
        - sh
        - -c
        - |
          for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 20 25 30; do
            if ip link show ${OPEN5GS_IFACE} >/dev/null 2>&1; then
              break
            fi
            sleep 1
          done
          ip route add default via 10.1.137.1 dev ${OPEN5GS_IFACE} table 100 || true
          ip rule add from ${vip} lookup 100 || true
          ip -4 addr show ${OPEN5GS_IFACE} || true
      containers:
      - name: 5gc
        image: ${OPEN5GS_IMAGE}
        imagePullPolicy: IfNotPresent
        args: ["5gc", "-c", "open5gs-5gc.yml"]
        envFrom:
        - configMapRef:
            name: ${OPEN5GS_NAME}-env
        ports:
        - name: webui
          containerPort: ${WEBUI_PORT}
          protocol: TCP
        - name: ngap
          containerPort: ${NGAP_PORT}
          protocol: SCTP
        - name: gtpu
          containerPort: ${GTPU_PORT}
          protocol: UDP
        securityContext:
          privileged: true
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
            - SYS_MODULE
        readinessProbe:
          exec:
            command: ["nc", "-z", "127.0.0.20", "7777"]
          initialDelaySeconds: 45
          periodSeconds: 10
          timeoutSeconds: 2
          failureThreshold: 30
        livenessProbe:
          exec:
            command: ["nc", "-z", "127.0.0.20", "7777"]
          initialDelaySeconds: 90
          periodSeconds: 20
          timeoutSeconds: 2
          failureThreshold: 10
        resources:
          requests:
            cpu: "2"
            memory: 4Gi
          limits:
            cpu: "4"
            memory: 8Gi
        volumeMounts:
        - name: subscribers
          mountPath: /open5gs/subscriber_db.csv
          subPath: subscriber_db.csv
        - name: mongodb
          mountPath: /data/db
      volumes:
      - name: subscribers
        configMap:
          name: ${OPEN5GS_NAME}-subscribers
      - name: mongodb
        emptyDir: {}
EOF
}

write_service() {
  local dir="$1"
  cat >"${dir}/service-${OPEN5GS_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${OPEN5GS_NAME}
  namespace: ${OPEN5GS_NS}
  labels:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: ${OPEN5GS_NAME}
  ports:
  - name: webui
    port: ${WEBUI_PORT}
    targetPort: webui
    protocol: TCP
EOF
}

write_cluster() {
  local cluster="edge"
  local node="$1"
  local repo_name dest vip master

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest="${REPOS_DIR}/${repo_name}/namespaces/${OPEN5GS_NS}"
  vip="$(open5gs_vip "$cluster")"
  master="$(open5gs_site_iface "$node")"

  if [[ -z "$vip" ]]; then
    echo "error: CLUSTER_OPEN5GS_VIP unset for '${cluster}'" >&2
    exit 1
  fi
  if [[ ! -f "$SUBSCRIBER_DB" ]]; then
    echo "missing ${SUBSCRIBER_DB}" >&2
    exit 1
  fi

  mkdir -p "$dest"
  purge_ns "$dest"

  write_namespace "$dest"
  write_nad "$dest" "$master"
  write_configmaps "$dest" "$vip"
  write_deployment "$dest" "$node" "$vip"
  write_service "$dest"

  echo "==> [${cluster}] ${dest}"
  echo "    node=${node}  master=${master}  vip=${vip}/24 (${OPEN5GS_IFACE})"
  echo "    Web UI:  kubectl --context edge@edge -n ${OPEN5GS_NS} port-forward svc/${OPEN5GS_NAME} ${WEBUI_PORT}:${WEBUI_PORT}"
  echo "    AMF N2:  ${vip}:${NGAP_PORT}/sctp"
  echo "    UPF N3:  ${vip}:${GTPU_PORT}/udp"
  if [[ "$node" == "edge-2" ]]; then
    echo "    WARNING: hostname 'edge-2' is not a Kubernetes node today; the pod will stay Pending."
  fi
}

main() {
  local node
  if [[ $# -gt 1 ]]; then
    echo "error: expected at most one node argument" >&2
    usage >&2
    exit 1
  fi
  node="$(resolve_node "${1:-}")"
  write_cluster "$node"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Open5GS 5GC on edge' edge"
  echo "Verify: kubectl --context edge@edge -n ${OPEN5GS_NS} get pods -o wide"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
