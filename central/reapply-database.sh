#!/usr/bin/env bash
# Re-apply OAI database PackageVariantSet after Porch/Gitea issues.
# Run on mgmt (from central/ or repo root with path to 002-database.yaml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
CTX="${KCTX:-mgmt@mgmt}"
GITEA_REPO="${GITEA_REPO:-http://gitea.gitea.svc.cluster.local:3000/nephio/central-repo.git}"

kubectl --context="$CTX" patch repository central-repo -n default --type=merge \
  -p "{\"spec\":{\"git\":{\"repo\":\"${GITEA_REPO}\"}}}" 2>/dev/null || true

echo "Restarting porch-server (clears stuck packagerevision locks)..."
kubectl --context="$CTX" rollout restart -n porch-system deploy/porch-server
kubectl --context="$CTX" rollout status -n porch-system deploy/porch-server --timeout=120s

echo "Removing stale database PackageVariant / revisions..."
kubectl --context="$CTX" delete packagevariant core-oai-database-central-repo-database -n default --ignore-not-found
kubectl --context="$CTX" delete packagevariant oai-common-central-repo-database -n default --ignore-not-found
kubectl --context="$CTX" delete packagerevisions.porch.kpt.dev -n default -l '' --field-selector metadata.name=central-repo.database.packagevariant-1 2>/dev/null || \
  kubectl --context="$CTX" delete packagerevision central-repo.database.packagevariant-1 -n default --ignore-not-found --wait=false 2>/dev/null || true
sleep 10

echo "Applying 002-database.yaml..."
kubectl --context="$CTX" apply -f "${SCRIPT_DIR}/002-database.yaml"

echo "Waiting for downstream draft in central-repo (up to 3m)..."
for _ in $(seq 1 18); do
  if kubectl --context="$CTX" get packagerevision central-repo.database.packagevariant-1 -n default &>/dev/null; then
    life=$(kubectl --context="$CTX" get packagerevision central-repo.database.packagevariant-1 -n default \
      -o jsonpath='{.spec.lifecycle}' 2>/dev/null || true)
    echo "Found central-repo.database.packagevariant-1 lifecycle=${life}"
    break
  fi
  sleep 10
done

kubectl --context="$CTX" get packagevariants,packagerevisions -n default 2>/dev/null | grep -E 'core-oai-database|central-repo.database|NAME' || true
kubectl --context="$CTX" get packagevariant core-oai-database-central-repo-database -n default \
  -o jsonpath='PackageVariant Ready={.status.conditions[?(@.type=="Ready")].status} {.status.conditions[?(@.type=="Ready")].message}{"\n"}' 2>/dev/null || true

cat <<EOF

Next: publish the DOWNSTREAM revision (REPO=central-repo).
Upstream must be Gitea oai-packages (./push-oai-packages-to-gitea.sh + repo-oai-packages-gitea.yaml).

  RPKG=central-repo.database.packagevariant-1

Use Nephio Web UI: central-repo → database → Propose → Approve
(porchctl may fail on this cluster: revision JSON type mismatch on upstream packages)

Or newer porchctl:
  porchctl rpkg propose -n default "\$RPKG"
  porchctl rpkg approve -n default "\$RPKG"

Verify:
  kubectl --context=$CTX get packagerevision "\$RPKG" -n default -o jsonpath='{.spec.lifecycle}{" rev="}{.spec.revision}{"\n"}'
  kubectl --context=central@central get pods -n oai-core -w

EOF
