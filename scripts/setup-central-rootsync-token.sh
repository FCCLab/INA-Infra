#!/usr/bin/env bash
# Copy central-repo Config Sync git token from mgmt → central (run from repo root).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MGMT_CTX="${MGMT_CTX:-mgmt@mgmt}"
CENTRAL_CTX="${KCTX:-central@central}"
# shellcheck source=merge-kubeconfig-central.sh
source "$SCRIPT_DIR/merge-kubeconfig-central.sh"
merge_kubeconfig_for_central
require_kubectl_context "$MGMT_CTX"
require_kubectl_context "$CENTRAL_CTX"

cd "$REPO_ROOT"

echo "Waiting for central-repo-access-token-configsync on mgmt (up to 3m) ..."
for _ in $(seq 1 18); do
  if kubectl --context="$MGMT_CTX" get secret central-repo-access-token-configsync -n default >/dev/null 2>&1; then
    break
  fi
  sleep 10
done

if ! kubectl --context="$MGMT_CTX" get secret central-repo-access-token-configsync -n default >/dev/null 2>&1; then
  echo "error: token secret missing on mgmt — apply central/central-repo on mgmt first" >&2
  exit 1
fi

echo "Copying token secret mgmt → central (namespace config-management-system) ..."
kubectl --context="$MGMT_CTX" get secret central-repo-access-token-configsync -n default -o yaml \
  | python3 -c "
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d.get('metadata', {})
for k in ('uid','resourceVersion','creationTimestamp','managedFields','ownerReferences','annotations'):
    m.pop(k, None)
m['name'] = 'central-repo-access-token-configsync'
m['namespace'] = 'config-management-system'
d['metadata'] = m
yaml.safe_dump(d, sys.stdout, default_flow_style=False)
" | kubectl --context="$CENTRAL_CTX" apply -f -

echo "Token secret ready on central."
kubectl --context="$CENTRAL_CTX" get rootsync central-repo -n config-management-system -o wide 2>/dev/null || true
