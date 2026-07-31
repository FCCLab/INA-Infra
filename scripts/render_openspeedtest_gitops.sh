#!/usr/bin/env bash
# Render OpenSpeedTest Deployment + Service into repos/ for Config Sync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OPENSPEEDTEST_IMAGE="${OPENSPEEDTEST_IMAGE:-openspeedtest/latest:latest}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Workload OST: hostPort 80 on CLUSTER_OPENSPEEDTEST_NODE; address is
openspeedtest_vip (10.1.137.101/102/103). Add the /32 on that node first:
  ./scripts/setup_openspeedtest_secondary_ips.sh

URLs:
  mgmt      http://$(openspeedtest_vip mgmt)/
  central   http://$(openspeedtest_vip central)/
  regional  http://$(openspeedtest_vip regional)/
  edge      http://$(openspeedtest_vip edge)/

Push: ./bringup/03_push_to_git_repos/push_git_repos.sh
EOF
}

write_cluster_ost() {
  local cluster="$1"
  local repo_name dest vip pool_name ost_node node_block=""

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest="${REPOS_DIR}/${repo_name}/namespaces/default"
  vip="$(openspeedtest_vip "$cluster")"
  pool_name="$(metallb_site_pool_name "$cluster")"
  ost_node="${CLUSTER_OPENSPEEDTEST_NODE[$cluster]:-}"
  mkdir -p "$dest"

  # Drop leftover dedicated MetalLB OST pool from earlier experiments.
  rm -f \
    "${REPOS_DIR}/${repo_name}/namespaces/metallb-system/ipaddresspool-openspeedtest-pool.yaml" \
    "${REPOS_DIR}/${repo_name}/namespaces/metallb-system/l2advertisement-openspeedtest-pool-l2.yaml"

  if [[ "$cluster" == "mgmt" ]]; then
    cat >"${dest}/deployment-openspeedtest.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: openspeedtest
  name: openspeedtest
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: openspeedtest
  template:
    metadata:
      labels:
        app: openspeedtest
    spec:
      containers:
      - image: ${OPENSPEEDTEST_IMAGE}
        imagePullPolicy: Always
        name: openspeedtest
        ports:
        - containerPort: 3000
          protocol: TCP
        - containerPort: 3001
          protocol: TCP
        resources:
          limits:
            cpu: 500m
            memory: 512Mi
          requests:
            cpu: 100m
            memory: 128Mi
EOF
    cat >"${dest}/service-openspeedtest-service.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  annotations:
    metallb.universe.tf/ip-allocated-from-pool: ${pool_name}
    metallb.universe.tf/loadBalancerIPs: ${vip}
  name: openspeedtest-service
  namespace: default
spec:
  allocateLoadBalancerNodePorts: false
  ports:
  - name: http
    port: 80
    protocol: TCP
    targetPort: 3000
  selector:
    app: openspeedtest
  sessionAffinity: None
  type: LoadBalancer
EOF
    echo "==> [${cluster}] MetalLB VIP http://${vip}/"
    return 0
  fi

  if [[ -z "$ost_node" ]]; then
    echo "error: CLUSTER_OPENSPEEDTEST_NODE[$cluster] unset" >&2
    exit 1
  fi

  node_block="
      nodeSelector:
        kubernetes.io/hostname: ${ost_node}
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule"

  cat >"${dest}/deployment-openspeedtest.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: openspeedtest
  name: openspeedtest
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: openspeedtest
  template:
    metadata:
      labels:
        app: openspeedtest
    spec:${node_block}
      containers:
      - image: ${OPENSPEEDTEST_IMAGE}
        imagePullPolicy: Always
        name: openspeedtest
        ports:
        - containerPort: 3000
          hostPort: 80
          protocol: TCP
        - containerPort: 3001
          protocol: TCP
        resources:
          limits:
            cpu: 500m
            memory: 512Mi
          requests:
            cpu: 100m
            memory: 128Mi
EOF

  cat >"${dest}/service-openspeedtest-service.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: openspeedtest-service
  namespace: default
spec:
  ports:
  - name: http
    port: 80
    protocol: TCP
    targetPort: 3000
  selector:
    app: openspeedtest
  sessionAffinity: None
  type: ClusterIP
EOF

  echo "==> [${cluster}] hostPort http://${vip}/ on ${ost_node} (need ${vip}/32 on site NIC)"
}

main() {
  local clusters=("$@")
  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt "${ALL_CLUSTERS[@]}")
  fi
  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      mgmt|central|regional|edge) ;;
      -h|--help) usage; exit 0 ;;
      *) echo "error: unknown cluster '${cluster}'" >&2; exit 1 ;;
    esac
    write_cluster_ost "$cluster"
  done
  echo
  echo "Secondary IPs: ./scripts/setup_openspeedtest_secondary_ips.sh"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh $*"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

main "$@"
