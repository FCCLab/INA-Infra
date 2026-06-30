#!/usr/bin/env bash
# Install MetalLB + Kubernetes Dashboard on workload clusters (control plane via SSH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

DASHBOARD_CHART_VERSION="${DASHBOARD_CHART_VERSION:-7.14.0}"
DASHBOARD_CHART_REPO="${DASHBOARD_CHART_REPO:-https://kubernetes-retired.github.io/dashboard/}"
DASHBOARD_CHART_URL="${DASHBOARD_CHART_URL:-https://github.com/kubernetes-retired/dashboard/releases/download/kubernetes-dashboard-${DASHBOARD_CHART_VERSION}/kubernetes-dashboard-${DASHBOARD_CHART_VERSION}.tgz}"
METALLB_CHART_VERSION="${METALLB_CHART_VERSION:-0.14.9}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Install MetalLB and Kubernetes Dashboard on workload cluster control planes.
With no arguments, installs on central, regional, edge, and ue.

Dashboard VIPs (https):
  central   ${CLUSTER_DASHBOARD_VIP[central]}
  regional  ${CLUSTER_DASHBOARD_VIP[regional]}
  edge      ${CLUSTER_DASHBOARD_VIP[edge]}
  ue        ${CLUSTER_DASHBOARD_VIP[ue]}

Login: service account admin-user (cluster-admin). Get a token:
  KCTX=regional@regional KUBECONFIG=~/.kube/config-regional \\
    kubectl -n kubernetes-dashboard create token admin-user

Environment:
  SSH_CONFIG              SSH config (default: utils/ssh_config/config)
  DASHBOARD_CHART_VERSION Helm chart version (default: 7.14.0)
  METALLB_CHART_VERSION   Helm chart version (default: 0.14.9)
  METALLB_POOL            MetalLB address pool (default: 10.1.132.10-99)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  local_script="$(mktemp)"
  remote_script="/tmp/install-dashboard-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

install_helm_remote_script() {
  cat <<'EOF'
if command -v helm >/dev/null 2>&1; then
  helm version --short
else
  echo "==> install helm"
  if curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash; then
    helm version --short
  else
    echo "==> install helm via apt (raw.githubusercontent.com unavailable)"
    sudo apt-get -o Dpkg::Use-Pty=0 update -qq
    sudo apt-get -o Dpkg::Use-Pty=0 install -y curl gpg apt-transport-https
    curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" \
      | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list >/dev/null
    sudo apt-get -o Dpkg::Use-Pty=0 update -qq
    sudo apt-get -o Dpkg::Use-Pty=0 install -y helm
    helm version --short
  fi
fi
EOF
}

install_dashboard_on_cluster() {
  local cluster="$1"
  local host vip
  host="${CLUSTER_CP_HOST[$cluster]}"
  vip="${CLUSTER_DASHBOARD_VIP[$cluster]}"

  echo
  echo "========================================"
  echo " Dashboard: ${cluster}"
  echo " Control plane: ${host}"
  echo " VIP: https://${vip}"
  echo "========================================"

  run_remote_script "$host" <<EOF
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"
export DEBIAN_FRONTEND=noninteractive

if [[ ! -f "\$KUBECONFIG" ]]; then
  echo "error: missing \$KUBECONFIG (run bringup_cluster.sh ${cluster} first)" >&2
  exit 1
fi

$(install_helm_remote_script)

echo "==> MetalLB"
helm repo add metallb https://metallb.github.io/metallb 2>/dev/null || true
helm repo update metallb
if ! kubectl get namespace metallb-system >/dev/null 2>&1; then
  helm upgrade --install metallb metallb/metallb \\
    --namespace metallb-system --create-namespace \\
    --version ${METALLB_CHART_VERSION} --wait --timeout 10m
else
  helm upgrade --install metallb metallb/metallb \\
    --namespace metallb-system \\
    --version ${METALLB_CHART_VERSION} --wait --timeout 10m
fi

kubectl apply -f - <<METALLB
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: local-pool
  namespace: metallb-system
spec:
  addresses:
    - ${METALLB_POOL}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: local-advertisement
  namespace: metallb-system
METALLB

echo "==> Kubernetes Dashboard (chart ${DASHBOARD_CHART_VERSION})"
chart_tgz="/tmp/kubernetes-dashboard-${DASHBOARD_CHART_VERSION}.tgz"
if helm repo add kubernetes-dashboard ${DASHBOARD_CHART_REPO} 2>/dev/null \
  && helm repo update kubernetes-dashboard; then
  helm upgrade --install kubernetes-dashboard kubernetes-dashboard/kubernetes-dashboard \\
    --namespace kubernetes-dashboard --create-namespace \\
    --version ${DASHBOARD_CHART_VERSION} --wait --timeout 15m
else
  echo "==> download dashboard chart tarball"
  curl -fsSL ${DASHBOARD_CHART_URL} -o "\$chart_tgz"
  helm upgrade --install kubernetes-dashboard "\$chart_tgz" \\
    --namespace kubernetes-dashboard --create-namespace \\
    --wait --timeout 15m
  rm -f "\$chart_tgz"
fi

echo "==> admin-user RBAC"
kubectl apply -f - <<'RBAC'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: admin-user
  namespace: kubernetes-dashboard
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-user
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: admin-user
    namespace: kubernetes-dashboard
RBAC

echo "==> Dashboard LoadBalancer VIP ${vip}"
kubectl apply -f - <<LB
apiVersion: v1
kind: Service
metadata:
  name: kubernetes-dashboard-lb
  namespace: kubernetes-dashboard
  annotations:
    metallb.universe.tf/loadBalancerIPs: ${vip}
spec:
  type: LoadBalancer
  allocateLoadBalancerNodePorts: false
  ports:
    - name: https
      port: 443
      protocol: TCP
      targetPort: 8443
  selector:
    app.kubernetes.io/component: app
    app.kubernetes.io/instance: kubernetes-dashboard
    app.kubernetes.io/name: kong
LB

echo "==> Wait for dashboard pods"
kubectl wait --for=condition=ready pod -l app.kubernetes.io/part-of=kubernetes-dashboard \\
  -n kubernetes-dashboard --timeout=300s

kubectl get pods -n kubernetes-dashboard
kubectl get svc -n kubernetes-dashboard kubernetes-dashboard-lb
echo ""
echo "Dashboard URL: https://${vip}"
echo "Token: kubectl -n kubernetes-dashboard create token admin-user"
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
  clusters=("${ALL_CLUSTERS[@]}")
else
  for c in "$@"; do
    if [[ -z "${CLUSTER_CP_HOST[$c]:-}" ]]; then
      echo "error: unknown cluster '${c}'" >&2
      exit 1
    fi
    clusters+=("$c")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! install_dashboard_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
