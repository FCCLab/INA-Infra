#!/usr/bin/env bash
# Trigger a one-shot Porch repository sync (porchctl on this cluster has no "repo sync").
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
CTX="${KCTX:-mgmt@mgmt}"
NS="${NS:-default}"
REPO="${REPO:-oai-packages}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

kubectl --context="$CTX" patch repository "$REPO" -n "$NS" --type=merge \
  -p "{\"spec\":{\"sync\":{\"runOnceAt\":\"${NOW}\"}}}"

echo "Triggered sync for ${REPO} at ${NOW}. Wait, then:"
echo "  kubectl --context=$CTX get repository $REPO -n $NS"
echo "  kubectl --context=$CTX get packagerevisions -n $NS | grep ${REPO}"
