#!/usr/bin/env bash
# Patch rootsync package files for this lab (local config only — does not touch the cluster).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_DIR="${PATCHES_DIR:-$SCRIPT_DIR/patches}"
ROOTSYNC_DIR="${ROOTSYNC_DIR:-$SCRIPT_DIR/rootsync}"
GITEA_HOST="${GITEA_HOST:-10.1.101.10}"
REPO_NAME="${REPO_NAME:-central-repo}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Apply patches under central/patches/ and normalize rootsync.yaml on disk only.
Does not run kpt or kubectl — use kpt fn render / kpt live apply in readme Step 7.

Options:
  --sources-only   Patch package-context.yaml only
  --rendered-only  Patch/normalize rootsync.yaml only
  -h, --help       Show this help

Environment:
  ROOTSYNC_DIR   Path to rootsync package (default: central/rootsync)
  GITEA_HOST     Mgmt Gitea host reachable from central (default: 10.1.101.10)
  REPO_NAME      Deployment repo name (default: central-repo)

Examples:
  $(basename "$0")
  $(basename "$0") --sources-only
  GITEA_HOST=172.18.0.200 $(basename "$0")
EOF
}

apply_patch() {
  local patch_file=$1
  if [[ ! -f "$patch_file" ]]; then
    echo "Missing patch: $patch_file" >&2
    exit 1
  fi
  if (cd "$ROOTSYNC_DIR" && patch -p0 --forward -r - < "$patch_file"); then
    echo "Applied: $(basename "$patch_file")"
  else
    echo "Already applied or no changes: $(basename "$patch_file")"
  fi
}

fix_rendered_rootsync() {
  local yaml="$ROOTSYNC_DIR/rootsync.yaml"
  [[ -f "$yaml" ]] || return 0
  sed -i \
    -e "s/^  name: example-rootsync$/  name: ${REPO_NAME}/" \
    -e "s/^  name: example-cluster-name$/  name: ${REPO_NAME}/" \
    -e "s|nephio/example-rootsync\\.git|nephio/${REPO_NAME}.git|g" \
    -e "s|nephio/example-cluster-name\\.git|nephio/${REPO_NAME}.git|g" \
    -e "s/example-rootsync-access-token-configsync/${REPO_NAME}-access-token-configsync/g" \
    -e "s/example-cluster-name-access-token-configsync/${REPO_NAME}-access-token-configsync/g" \
    -e "s|http://[0-9.]*:3000/nephio/${REPO_NAME}\\.git|http://${GITEA_HOST}:3000/nephio/${REPO_NAME}.git|g" \
    "$yaml"
}

MODE=all
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sources-only)  MODE=sources ;;
    --rendered-only) MODE=rendered ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

if [[ ! -d "$ROOTSYNC_DIR" ]]; then
  echo "rootsync package not found: $ROOTSYNC_DIR" >&2
  echo "Run: kpt pkg get --for-deployment \\"
  echo "  https://github.com/nephio-project/catalog.git/nephio/optional/rootsync@v6 rootsync" >&2
  exit 1
fi

if [[ "$MODE" == all || "$MODE" == sources ]]; then
  apply_patch "$PATCHES_DIR/rootsync-package-context.patch"
fi

if [[ "$MODE" == all || "$MODE" == rendered ]]; then
  apply_patch "$PATCHES_DIR/rootsync-rendered.patch" || true
  apply_patch "$PATCHES_DIR/rootsync-rendered-after-kpt-fn.patch" || true
  fix_rendered_rootsync
  echo "Normalized rootsync.yaml for repo=${REPO_NAME} gitea=${GITEA_HOST}:3000"
fi

echo ""
echo "rootsync.yaml (local):"
grep -E '^(  name:|    repo:|      name:)' "$ROOTSYNC_DIR/rootsync.yaml" 2>/dev/null || true
