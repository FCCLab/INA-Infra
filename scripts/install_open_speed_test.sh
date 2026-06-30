#!/usr/bin/env bash
# Install OpenSpeedTest on mgmt and workload clusters (control plane via SSH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
OPENSPEEDTEST_IMAGE="${OPENSPEEDTEST_IMAGE:-openspeedtest/latest:latest}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Install OpenSpeedTest on cluster control planes with MetalLB LoadBalancer VIPs.
With no arguments, installs on mgmt, central, regional, edge, and ue.

OpenSpeedTest URLs (http):
  mgmt      $(openspeedtest_vip mgmt)
  central   $(openspeedtest_vip central)
  regional  $(openspeedtest_vip regional)
  edge      $(openspeedtest_vip edge)
  ue        $(openspeedtest_vip ue)

Requires MetalLB and local-pool (run install_ip_pool.sh first).

Examples:
  $(basename "$0")
  $(basename "$0") central edge

Environment:
  SSH_CONFIG           SSH config (default: utils/ssh_config/config)
  OPENSPEEDTEST_IMAGE  Container image (default: openspeedtest/latest:latest)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  local_script="$(mktemp)"
  remote_script="/tmp/install-openspeedtest-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

install_openspeedtest_on_cluster() {
  local cluster="$1"
  local host vip
  host="$(cluster_cp_host "$cluster")"
  vip="$(openspeedtest_vip "$cluster")"

  echo
  echo "========================================"
  echo " OpenSpeedTest: ${cluster}"
  echo " Control plane: ${host}"
  echo " URL: http://${vip}"
  echo "========================================"

  run_remote_script "$host" <<EOF
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"

if [[ ! -f "\$KUBECONFIG" ]]; then
  echo "error: missing \$KUBECONFIG (bring up ${cluster} first)" >&2
  exit 1
fi

if ! kubectl get ipaddresspool -n metallb-system local-pool >/dev/null 2>&1; then
  echo "error: MetalLB pool local-pool not found (run install_ip_pool.sh ${cluster} first)" >&2
  exit 1
fi

echo "==> OpenSpeedTest deployment"
kubectl apply -f - <<DEPLOY
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
DEPLOY

echo "==> OpenSpeedTest LoadBalancer VIP ${vip}"
kubectl apply -f - <<SVC
apiVersion: v1
kind: Service
metadata:
  annotations:
    metallb.universe.tf/ip-allocated-from-pool: local-pool
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
SVC

echo "==> Wait for OpenSpeedTest pod"
kubectl wait --for=condition=ready pod -l app=openspeedtest -n default --timeout=300s

kubectl get deployment openspeedtest -n default
kubectl get svc openspeedtest-service -n default
echo ""
echo "OpenSpeedTest URL: http://${vip}"
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_OPENSPEEDTEST_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    case "$cluster" in
      mgmt) ;;
      *)
        if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
          echo "error: unknown cluster '${cluster}' (expected mgmt, central, regional, edge, or ue)" >&2
          exit 1
        fi
        ;;
    esac
    clusters+=("$cluster")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! install_openspeedtest_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
