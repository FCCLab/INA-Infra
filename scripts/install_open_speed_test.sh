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
Usage: $(basename "$0") [options] [cluster ...]

Install or uninstall OpenSpeedTest on cluster control planes with LoadBalancer VIPs.
With no cluster arguments, targets mgmt, central, regional, edge.

OpenSpeedTest URLs (http):
  mgmt      $(openspeedtest_vip mgmt)
  central   $(openspeedtest_vip central)
  regional  $(openspeedtest_vip regional)
  edge      $(openspeedtest_vip edge)

LoadBalancer VIPs require MetalLB (deploy via GitOps).
Source of truth for OST manifests is Config Sync — prefer:
  ./scripts/render_openspeedtest_gitops.sh [cluster ...]
  ./bringup/03_push_to_git_repos/push_git_repos.sh
This imperative path is for bootstrap / emergency; sync may overwrite.

Options:
  --uninstall, -u   Remove OpenSpeedTest deployment and service
  -h, --help        Show this help

Examples:
  $(basename "$0")
  $(basename "$0") central edge
  $(basename "$0") --uninstall central
  $(basename "$0") -u

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
  local host vip pool_name
  host="$(cluster_cp_host "$cluster")"
  vip="$(openspeedtest_vip "$cluster")"
  pool_name="$(metallb_site_pool_name "$cluster")"

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

echo "==> OpenSpeedTest LoadBalancer VIP ${vip} (pool ${pool_name})"
kubectl apply -f - <<SVC
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
SVC

echo "==> Wait for OpenSpeedTest pod"
kubectl wait --for=condition=ready pod -l app=openspeedtest -n default --timeout=300s

kubectl get deployment openspeedtest -n default
kubectl get svc openspeedtest-service -n default
echo ""
echo "OpenSpeedTest URL: http://${vip}"
EOF
}

uninstall_openspeedtest_on_cluster() {
  local cluster="$1"
  local host vip
  host="$(cluster_cp_host "$cluster")"
  vip="$(openspeedtest_vip "$cluster")"

  echo
  echo "========================================"
  echo " OpenSpeedTest uninstall: ${cluster}"
  echo " Control plane: ${host}"
  echo " VIP: ${vip}"
  echo "========================================"

  run_remote_script "$host" <<EOF
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"

if [[ ! -f "\$KUBECONFIG" ]]; then
  echo "error: missing \$KUBECONFIG (bring up ${cluster} first)" >&2
  exit 1
fi

echo "==> Delete OpenSpeedTest service"
kubectl delete service openspeedtest-service -n default --ignore-not-found

echo "==> Delete OpenSpeedTest deployment"
kubectl delete deployment openspeedtest -n default --ignore-not-found

echo "==> Wait for pods to terminate"
kubectl wait --for=delete pod -l app=openspeedtest -n default --timeout=120s 2>/dev/null || true

kubectl get deployment openspeedtest -n default 2>/dev/null || echo "deployment/openspeedtest removed"
kubectl get svc openspeedtest-service -n default 2>/dev/null || echo "service/openspeedtest-service removed"
EOF
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt) return 0 ;;
    *)
      if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
        echo "error: unknown cluster '${cluster}' (expected mgmt, central, regional, edge)" >&2
        return 1
      fi
      ;;
  esac
}

UNINSTALL=0
clusters=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall|-u)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      clusters+=("$1")
      shift
      ;;
  esac
done

if [[ ${#clusters[@]} -eq 0 ]]; then
  clusters=("${ALL_OPENSPEEDTEST_CLUSTERS[@]}")
else
  for cluster in "${clusters[@]}"; do
    validate_cluster "$cluster" || exit 1
  done
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

failed=0
for cluster in "${clusters[@]}"; do
  if [[ "$UNINSTALL" == "1" ]]; then
    if ! uninstall_openspeedtest_on_cluster "$cluster"; then
      failed=1
    fi
  elif ! install_openspeedtest_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
