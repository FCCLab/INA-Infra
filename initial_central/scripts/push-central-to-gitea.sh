#!/usr/bin/env bash
# Push initial_central/ to Gitea nephio/central-repo; bootstrap RootSync only if missing.
#
#   ./initial_central/scripts/export-central-live.sh
#   ./initial_central/scripts/push-central-to-gitea.sh
#
# Requires: git; Gitea repo nephio/central-repo; token on mgmt from central/central-repo package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="${SRC_DIR:-$CLUSTER_DIR}"
WORK_DIR="${WORK_DIR:-/tmp/nephio-central-git-$$}"

GITEA_HOST="${GITEA_HOST:-10.1.132.51}"
GITEA_PORT="${GITEA_PORT:-3000}"
GITEA_USER="${GITEA_USER:-nephio}"
GITEA_PASS="${GITEA_PASS:-secret}"
GITEA_REPO="${GITEA_REPO:-central-repo}"
ROOTSYNC_NAME="${ROOTSYNC_NAME:-central-repo}"
MGMT_CTX="${MGMT_CTX:-mgmt@mgmt}"
CTX="${KCTX:-central@central}"
# shellcheck source=../../scripts/merge-kubeconfig-central.sh
source "$REPO_ROOT/scripts/merge-kubeconfig-central.sh"
merge_kubeconfig_for_central
require_kubectl_context "$CTX"

GIT_URL="http://${GITEA_USER}:${GITEA_PASS}@${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}.git"
GITEA_REPO_URL="http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}.git"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Push initial_central/ to Gitea and bootstrap RootSync on central if missing.

Options:
  -s DIR        Source export dir (default: initial_central)
  -h            Help

Environment:
  GITEA_HOST GITEA_PORT GITEA_USER GITEA_PASS
  KUBECONFIG    Merged kubeconfig for mgmt + central
  MGMT_CTX      mgmt context (default: mgmt@mgmt)
  KCTX          central context (default: central@central)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SRC_DIR="$2"; shift 2 ;;
    -h) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ ! -d "$SRC_DIR/namespaces" && ! -d "$SRC_DIR/cluster" ]]; then
  echo "error: run ./initial_central/scripts/export-central-live.sh first (missing $SRC_DIR)" >&2
  exit 1
fi

echo "Cloning ${GITEA_REPO} from Gitea ..."
rm -rf "$WORK_DIR"
git clone --depth 1 "$GIT_URL" "$WORK_DIR"

echo "Copying export into git repo ..."
find "$WORK_DIR" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
cp -a "$SRC_DIR"/. "$WORK_DIR/"

cd "$WORK_DIR"
git add -A
if git diff --staged --quiet; then
  echo "No changes to push."
else
  git -c user.name="nephio-export" -c user.email="nephio@nephio.org" \
    commit -m "Export live central cluster $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push origin main
  echo "Pushed to http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}"
fi

if kubectl --context="$CTX" get "rootsync/${ROOTSYNC_NAME}" -n config-management-system >/dev/null 2>&1; then
  live_repo=$(kubectl --context="$CTX" get "rootsync/${ROOTSYNC_NAME}" -n config-management-system \
    -o jsonpath='{.spec.git.repo}' 2>/dev/null || true)
  if [[ -n "$live_repo" && "$live_repo" != "$GITEA_REPO_URL" ]]; then
    echo "Patching RootSync git repo: ${live_repo} -> ${GITEA_REPO_URL}"
    kubectl --context="$CTX" patch "rootsync/${ROOTSYNC_NAME}" -n config-management-system \
      --type=merge -p "{\"spec\":{\"git\":{\"repo\":\"${GITEA_REPO_URL}\"}}}"
  fi
  echo "RootSync ${ROOTSYNC_NAME} already exists — Config Sync will sync git automatically."
  echo "Check progress: ./scripts/check-configsync.sh -c ${CTX} -n ${ROOTSYNC_NAME}"
  rm -rf "$WORK_DIR"
  exit 0
fi

echo "RootSync not found — bootstrapping token + RootSync on central ..."
"$REPO_ROOT/scripts/setup-central-rootsync-token.sh"

if ! kubectl --context="$CTX" get secret central-repo-access-token-configsync -n config-management-system >/dev/null 2>&1; then
  echo "warning: central-repo-access-token-configsync not ready — apply RootSync manually later" >&2
  rm -rf "$WORK_DIR"
  exit 0
fi

echo "Creating RootSync for nephio/${GITEA_REPO} ..."
kubectl --context="$CTX" apply -f - <<EOF
apiVersion: configsync.gke.io/v1beta1
kind: RootSync
metadata:
  name: ${ROOTSYNC_NAME}
  namespace: config-management-system
spec:
  sourceFormat: unstructured
  git:
    repo: ${GITEA_REPO_URL}
    branch: main
    auth: token
    secretRef:
      name: central-repo-access-token-configsync
    period: 15s
EOF

echo ""
echo "Verify:"
echo "  ./scripts/check-configsync.sh -c ${CTX} -n ${ROOTSYNC_NAME}"
echo "  open http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}"

rm -rf "$WORK_DIR"
