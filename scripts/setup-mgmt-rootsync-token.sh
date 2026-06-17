#!/usr/bin/env bash
# Create mgmt Gitea token secret for RootSync (run from repo root).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CTX="${KCTX:-mgmt@mgmt}"

cd "$REPO_ROOT"

echo "Applying mgmt infra Repository + Token CRs ..."
kubectl --context="$CTX" apply -f mgmt/repo-gitea.yaml -f mgmt/token-porch.yaml -f mgmt/token-configsync.yaml

echo "Waiting for mgmt-access-token-configsync secret (up to 3m) ..."
for _ in $(seq 1 18); do
  if kubectl --context="$CTX" get secret mgmt-access-token-configsync -n config-management-system >/dev/null 2>&1; then
    echo "Token secret ready."
    kubectl --context="$CTX" get rootsync mgmt -n config-management-system -o wide 2>/dev/null || true
    exit 0
  fi
  st=$(kubectl --context="$CTX" get token mgmt-access-token-configsync -n config-management-system \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  echo "  token Ready=${st:-pending}"
  sleep 10
done

echo "Token controller did not create secret — copying from central-repo token (same Gitea user) ..." >&2
kubectl --context="$CTX" get secret central-repo-access-token-configsync -n default -o yaml \
  | python3 -c "
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d.get('metadata', {})
for k in ('uid','resourceVersion','creationTimestamp','managedFields','ownerReferences','annotations'):
    m.pop(k, None)
m['name'] = 'mgmt-access-token-configsync'
m['namespace'] = 'config-management-system'
d['metadata'] = m
yaml.safe_dump(d, sys.stdout, default_flow_style=False)
" | kubectl --context="$CTX" apply -f -

echo "Fallback secret applied."
