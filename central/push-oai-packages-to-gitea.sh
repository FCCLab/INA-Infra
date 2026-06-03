#!/usr/bin/env bash
# Push central/oai-packages to Gitea (branch v5) for Porch upstream repo "oai-packages".
#
# Prereqs: Gitea reachable; repo nephio/oai-packages created (000-gitea-repos.yaml or this script).
#
#   export GITEA_HOST=10.1.101.10 GITEA_PORT=30519
#   export GITEA_USER=nephio GITEA_PASS=secret
#   ./push-oai-packages-to-gitea.sh
#
# Then on mgmt:
#   kubectl apply -f repo-oai-packages-gitea.yaml
#   ./sync-oai-packages-repo.sh
#   kubectl apply -f 002-database.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OAI_DIR="${OAI_DIR:-$SCRIPT_DIR/oai-packages}"
GITEA_HOST="${GITEA_HOST:-10.1.101.10}"
GITEA_PORT="${GITEA_PORT:-30519}"
GITEA_USER="${GITEA_USER:-nephio}"
GITEA_PASS="${GITEA_PASS:-secret}"
GITEA_ORG="${GITEA_ORG:-nephio}"
REPO_NAME="${REPO_NAME:-oai-packages}"
BRANCH="${BRANCH:-v5}"
GITEA_URL="http://${GITEA_HOST}:${GITEA_PORT}"
GIT_REMOTE="http://${GITEA_USER}:${GITEA_PASS}@${GITEA_HOST}:${GITEA_PORT}/${GITEA_ORG}/${REPO_NAME}.git"

if [[ ! -d "$OAI_DIR" ]]; then
  echo "Missing directory: $OAI_DIR" >&2
  exit 1
fi

echo "Ensuring Gitea repo ${GITEA_ORG}/${REPO_NAME} exists ..."
code=$(curl -s -o /dev/null -w "%{http_code}" \
  -u "${GITEA_USER}:${GITEA_PASS}" \
  -X POST "${GITEA_URL}/api/v1/user/repos" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${REPO_NAME}\",\"auto_init\":false,\"private\":false}")
if [[ "$code" != "201" && "$code" != "409" ]]; then
  echo "Create repo failed (HTTP ${code})" >&2
  exit 1
fi
echo "Repo ok (HTTP ${code})"

cd "$OAI_DIR"

if find . -name Kptfile -print0 | xargs -0 grep -q 'gcr.io/kpt-fn' 2>/dev/null; then
  echo "Fixing gcr.io/kpt-fn in Kptfiles ..."
  find . -name Kptfile -exec sed -i \
    -e 's|gcr.io/kpt-fn/set-namespace|ghcr.io/kptdev/krm-functions-catalog/set-namespace|g' \
    -e 's|gcr.io/kpt-fn/apply-replacements|ghcr.io/kptdev/krm-functions-catalog/apply-replacements|g' \
    {} \;
fi

if [[ ! -d .git ]]; then
  git init -b "$BRANCH"
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$GIT_REMOTE"

git add -A
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "OAI packages ${BRANCH}: kpt-fn images -> ghcr.io/kptdev/krm-functions-catalog"
fi

echo "Pushing branch ${BRANCH} to ${GITEA_ORG}/${REPO_NAME} ..."
git push -u origin "${BRANCH}" --force

echo ""
echo "Done. On mgmt:"
echo "  kubectl --context=mgmt@mgmt apply -f ${SCRIPT_DIR}/repo-oai-packages-gitea.yaml"
echo "  ./sync-oai-packages-repo.sh"
echo "  kubectl --context=mgmt@mgmt apply -f ${SCRIPT_DIR}/002-database.yaml"
