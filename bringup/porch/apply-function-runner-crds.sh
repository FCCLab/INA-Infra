#!/usr/bin/env bash
# Install missing Porch function-runner CRDs/resources (ServiceTemplate, PodTemplate, FunctionConfig).
# Fixes PackageVariant render error:
#   no matches for kind "ServiceTemplate" in version "config.porch.kpt.dev/v1alpha1"
#
# Run on mgmt:
#   export KUBECONFIG=$HOME/.kube/config
#   ./apply-function-runner-crds.sh
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
CTX="${KCTX:-mgmt@mgmt}"
PORCH_REF="${PORCH_REF:-main}"
BASE="https://raw.githubusercontent.com/nephio-project/porch/${PORCH_REF}"

echo "Applying ServiceTemplate CRD..."
kubectl --context="$CTX" apply -f "${BASE}/api/porchconfig/v1alpha1/config.porch.kpt.dev_servicetemplates.yaml"

echo "Applying FunctionConfig CRD (if not already present)..."
kubectl --context="$CTX" apply -f "${BASE}/api/porchconfig/v1alpha1/config.porch.kpt.dev_functionconfigs.yaml" 2>/dev/null || \
  kubectl --context="$CTX" apply -f "$(dirname "$0")/0-functionconfigs.yaml"

echo "Ensuring porch-fn-system namespace..."
kubectl --context="$CTX" apply -f "$(dirname "$0")/1-namespace.yaml"

echo "Applying PodTemplate + ServiceTemplate + FunctionConfig instances..."
kubectl --context="$CTX" apply -f "${BASE}/deployments/porch/22-function-templates.yaml"
kubectl --context="$CTX" apply -f "${BASE}/deployments/porch/23-function-configurations.yaml"

echo "Restarting function-runner..."
kubectl --context="$CTX" rollout restart -n porch-system deploy/function-runner
kubectl --context="$CTX" rollout status -n porch-system deploy/function-runner --timeout=180s

echo ""
echo "Verify:"
echo "  kubectl --context=$CTX api-resources | grep servicetemplate"
echo "  kubectl --context=$CTX get servicetemplates,podtemplates,functionconfigs -n porch-fn-system"
echo "  kubectl --context=$CTX apply -f central/002-database.yaml"
