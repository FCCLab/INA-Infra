#!/usr/bin/env bash
# Render Glass ISC DHCP (hostNetwork on central-0) into repos/ for Config Sync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
GLASS_NS="${GLASS_NS:-glass-dhcp}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${GLASS_DHCP_TAG:-latest}"
GLASS_IMAGE="${GLASS_IMAGE:-${REGISTRY}/glass-isc-dhcp:${TAG}}"
DHCPD_IMAGE="${DHCPD_IMAGE:-${REGISTRY}/networkboot/dhcpd:${TAG}}"
DHCP_IFACE="${DHCP_IFACE:-enp7s0}"
# Pin to CP so site L2 DHCP is authoritative from one node.
NODE_NAME="${GLASS_DHCP_NODE:-cpu-central-0}"
DHCPD_CONF="${DHCPD_CONF:-$REPO_ROOT/services/glass-dhcp/dhcpd.conf}"
GLASS_CONF="${GLASS_CONF:-$REPO_ROOT/services/glass-dhcp/glass_config.json}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [central]

Render Glass + ISC DHCP manifests into repos/<cluster>-repo/namespaces/${GLASS_NS}.
Default cluster: central (node ${NODE_NAME}, iface ${DHCP_IFACE}).

Images:
  ${GLASS_IMAGE}
  ${DHCPD_IMAGE}

Build/push first:
  ./services/glass-dhcp/build_push.sh
EOF
}

purge_ns() {
  local dest="$1"
  rm -f "${dest}"/*.yaml
}

write_cluster() {
  local cluster="$1"
  local repo_name dest
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest="${REPOS_DIR}/${repo_name}/namespaces/${GLASS_NS}"
  mkdir -p "$dest"
  purge_ns "$dest"

  if [[ ! -f "$DHCPD_CONF" ]]; then
    echo "missing ${DHCPD_CONF}" >&2
    exit 1
  fi
  if [[ ! -f "$GLASS_CONF" ]]; then
    echo "missing ${GLASS_CONF}" >&2
    exit 1
  fi

  # Indent multi-line files for ConfigMap literal
  local dhcpd_body glass_body
  dhcpd_body="$(sed 's/^/    /' "$DHCPD_CONF")"
  glass_body="$(sed 's/^/    /' "$GLASS_CONF")"

  cat >"${dest}/00-namespace.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${GLASS_NS}
  labels:
    app.kubernetes.io/name: glass-dhcp
EOF

  cat >"${dest}/configmap-dhcp.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: glass-dhcp-config
  namespace: ${GLASS_NS}
  labels:
    app.kubernetes.io/name: glass-dhcp
data:
  dhcpd.conf: |
${dhcpd_body}
  glass_config.json: |
${glass_body}
EOF

  cat >"${dest}/deployment-glass-dhcp.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: glass-dhcp
  namespace: ${GLASS_NS}
  labels:
    app.kubernetes.io/name: glass-dhcp
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: glass-dhcp
  template:
    metadata:
      labels:
        app.kubernetes.io/name: glass-dhcp
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      nodeSelector:
        kubernetes.io/hostname: ${NODE_NAME}
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      volumes:
        - name: config
          configMap:
            name: glass-dhcp-config
        - name: data
          emptyDir: {}
        - name: dhcp-log
          emptyDir: {}
      initContainers:
        - name: seed-data
          image: ${DHCPD_IMAGE}
          command:
            - /bin/sh
            - -c
            - |
              set -eu
              cp /config/dhcpd.conf /data/dhcpd.conf
              touch /data/dhcpd.leases
              touch /var/log/dhcp.log
          volumeMounts:
            - name: config
              mountPath: /config
            - name: data
              mountPath: /data
            - name: dhcp-log
              mountPath: /var/log
      containers:
        - name: dhcpd
          image: ${DHCPD_IMAGE}
          imagePullPolicy: Always
          args:
            - ${DHCP_IFACE}
          env:
            - name: INTERFACE
              value: ${DHCP_IFACE}
          securityContext:
            capabilities:
              add:
                - NET_ADMIN
                - NET_RAW
                - NET_BIND_SERVICE
          volumeMounts:
            - name: data
              mountPath: /data
            - name: dhcp-log
              mountPath: /var/log
        - name: glass
          image: ${GLASS_IMAGE}
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 3000
          volumeMounts:
            - name: data
              mountPath: /data
            - name: config
              mountPath: /opt/glass-isc-dhcp/config/glass_config.json
              subPath: glass_config.json
            - name: dhcp-log
              mountPath: /var/log
          readinessProbe:
            httpGet:
              path: /
              port: 3000
              scheme: HTTP
            initialDelaySeconds: 5
            periodSeconds: 10
EOF

  cat >"${dest}/service-glass.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: glass-dhcp
  namespace: ${GLASS_NS}
  labels:
    app.kubernetes.io/name: glass-dhcp
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: glass-dhcp
  ports:
    - name: http
      port: 3000
      targetPort: 3000
EOF

  echo "Wrote ${dest}"
}

CLUSTERS=()
if [[ $# -eq 0 ]]; then
  CLUSTERS=(central)
else
  for a in "$@"; do
    case "$a" in
      -h|--help) usage; exit 0 ;;
      central) CLUSTERS+=("$a") ;;
      *) echo "Only 'central' is supported (got: $a)" >&2; exit 1 ;;
    esac
  done
fi

for c in "${CLUSTERS[@]}"; do
  write_cluster "$c"
done

echo
echo "Next:"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Glass DHCP on central' ${CLUSTERS[*]}"
echo "  kubectl --context central@central -n ${GLASS_NS} get pods -o wide"
echo "  Glass UI (hostNetwork): http://10.1.132.210:3000"
