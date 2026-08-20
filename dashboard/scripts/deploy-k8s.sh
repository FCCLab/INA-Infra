#!/usr/bin/env bash
# Deploy Multi-Cluster Resource Dashboard to mgmt Kubernetes cluster.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECONFIG_DIR="${HOME}/.kube"
NAMESPACE="dashboard"

echo "==> Ensuring namespace '${NAMESPACE}' exists..."
kubectl --context=mgmt@mgmt create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl --context=mgmt@mgmt apply -f -

echo "==> Creating/updating kubeconfigs secret in namespace '${NAMESPACE}'..."
kubectl --context=mgmt@mgmt -n "${NAMESPACE}" create secret generic dashboard-kubeconfigs \
  --from-file=config="${KUBECONFIG_DIR}/config" \
  --from-file=config-central="${KUBECONFIG_DIR}/config-central" \
  --from-file=config-regional="${KUBECONFIG_DIR}/config-regional" \
  --from-file=config-edge="${KUBECONFIG_DIR}/config-edge" \
  --dry-run=client -o yaml | kubectl --context=mgmt@mgmt apply -f -

echo "==> Applying dashboard deployment manifests..."
kubectl --context=mgmt@mgmt apply -f "${DASHBOARD_ROOT}/deploy/k8s-dashboard.yaml"

echo "==> Waiting for dashboard rollout..."
kubectl --context=mgmt@mgmt -n "${NAMESPACE}" rollout status deployment/dashboard-backend --timeout=60s
kubectl --context=mgmt@mgmt -n "${NAMESPACE}" rollout status deployment/dashboard-frontend --timeout=60s

echo
echo "==> Multi-Cluster Resource Dashboard successfully deployed!"
echo "UI:  http://10.1.132.200:30574/"
echo "API: http://10.1.132.200:30574/docs"
