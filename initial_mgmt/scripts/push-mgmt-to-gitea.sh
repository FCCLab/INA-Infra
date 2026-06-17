#!/usr/bin/env bash
# Push mgmt-live-export/ to Gitea nephio/mgmt; bootstrap RootSync only if missing.
#
#   ./initial_mgmt/scripts/export-mgmt-live.sh
#   ./initial_mgmt/scripts/push-mgmt-to-gitea.sh
#
# Requires: git, curl; Gitea repo nephio/mgmt already exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="${SRC_DIR:-$CLUSTER_DIR}"
WORK_DIR="${WORK_DIR:-/tmp/nephio-mgmt-git-$$}"

GITEA_HOST="${GITEA_HOST:-10.1.132.51}"
GITEA_PORT="${GITEA_PORT:-3000}"
GITEA_USER="${GITEA_USER:-nephio}"
GITEA_PASS="${GITEA_PASS:-secret}"
GITEA_REPO="${GITEA_REPO:-mgmt}"
CTX="${KCTX:-mgmt@mgmt}"

GIT_URL="http://${GITEA_USER}:${GITEA_PASS}@${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}.git"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Push initial_mgmt/ to Gitea and bootstrap RootSync on mgmt if missing.

Options:
  -s DIR        Source export dir (default: initial_mgmt)
  -h            Help

Environment:
  GITEA_HOST GITEA_PORT GITEA_USER GITEA_PASS
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SRC_DIR="$2"; shift 2 ;;
    -h) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ ! -d "$SRC_DIR/namespaces" ]]; then
  echo "error: run ./initial_mgmt/scripts/export-mgmt-live.sh first (missing $SRC_DIR)" >&2
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
    commit -m "Export live mgmt cluster $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push origin main
  echo "Pushed to http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}"
fi

if kubectl --context="$CTX" get rootsync mgmt -n config-management-system >/dev/null 2>&1; then
  echo "RootSync mgmt already exists — skipping bootstrap (Config Sync will sync git automatically)."
  echo "Check progress: ./scripts/check-configsync.sh"
  rm -rf "$WORK_DIR"
  exit 0
fi

echo "RootSync not found — bootstrapping tokens and RootSync ..."
kubectl --context="$CTX" apply -f "$REPO_ROOT/mgmt/repo-gitea.yaml"
kubectl --context="$CTX" apply -f "$REPO_ROOT/mgmt/token-porch.yaml"
kubectl --context="$CTX" apply -f "$REPO_ROOT/mgmt/token-configsync.yaml"

echo "Waiting for configsync token secret (up to 3m) ..."
for _ in $(seq 1 18); do
  if kubectl --context="$CTX" get secret mgmt-access-token-configsync -n config-management-system >/dev/null 2>&1; then
    break
  fi
  sleep 10
done

if ! kubectl --context="$CTX" get secret mgmt-access-token-configsync -n config-management-system >/dev/null 2>&1; then
  echo "warning: mgmt-access-token-configsync not ready — apply RootSync manually later" >&2
  exit 0
fi

echo "Creating RootSync for nephio/mgmt ..."
kubectl --context="$CTX" apply -f - <<EOF
apiVersion: configsync.gke.io/v1beta1
kind: RootSync
metadata:
  name: mgmt
  namespace: config-management-system
spec:
  sourceFormat: unstructured
  git:
    repo: http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}.git
    branch: main
    auth: token
    secretRef:
      name: mgmt-access-token-configsync
    period: 15s
EOF

echo ""
echo "Verify:"
echo "  ./scripts/check-configsync.sh"
echo "  open http://${GITEA_HOST}:${GITEA_PORT}/nephio/${GITEA_REPO}"

rm -rf "$WORK_DIR"
