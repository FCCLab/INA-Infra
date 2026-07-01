#!/usr/bin/env bash
# Create empty Gitea repos for workload cluster Config Sync (central-repo, regional-repo, …).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

GITEA_HOST="${GITEA_HOST:-10.1.132.51}"
GITEA_PORT="${GITEA_PORT:-3000}"
GITEA_USER="${GITEA_USER:-nephio}"
GITEA_PASS="${GITEA_PASS:-secret}"
GITEA_ORG="${GITEA_ORG:-nephio}"
GITEA_URL="http://${GITEA_HOST}:${GITEA_PORT}"
AUTO_INIT="${GITEA_AUTO_INIT:-true}"

DEFAULT_WORKLOAD_CLUSTERS=("${ALL_CLUSTERS[@]}")
INCLUDE_MGMT=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Create empty Gitea repos for Nephio / Config Sync (POST /api/v1/user/repos).
Default (no args): central-repo, regional-repo, edge-repo, ue-repo.

Options:
  --include-mgmt    Also create mgmt and mgmt-staging
  -n, --dry-run     Print actions only
  -h, --help        Show this help

Environment:
  GITEA_HOST GITEA_PORT GITEA_USER GITEA_PASS GITEA_ORG
  GITEA_AUTO_INIT   true|false — initialize with README (default: true)

Examples:
  $(basename "$0")
  $(basename "$0") central edge
  $(basename "$0") --include-mgmt

Browse: http://${GITEA_HOST}:${GITEA_PORT}/${GITEA_ORG}/<repo>
Next: apply {cluster}-repo kpt package on mgmt, then Config Sync + RootSync on workload.
See configsync/readme.md
EOF
}

create_gitea_repo() {
  local repo_name="$1"
  local code body
  local payload

  payload=$(printf '{"name":"%s","auto_init":%s,"private":false}' \
    "$repo_name" "$( [[ "$AUTO_INIT" == true ]] && echo true || echo false )")

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "dry-run: create ${GITEA_ORG}/${repo_name}"
    return 0
  fi

  body="$(mktemp)"
  code="$(curl -s -o "$body" -w "%{http_code}" \
    -u "${GITEA_USER}:${GITEA_PASS}" \
    -X POST "${GITEA_URL}/api/v1/user/repos" \
    -H 'Content-Type: application/json' \
    -d "$payload")"

  case "$code" in
    201)
      echo "created  ${GITEA_ORG}/${repo_name}  $(gitea_repo_url "$repo_name")"
      ;;
    409)
      echo "exists   ${GITEA_ORG}/${repo_name}  $(gitea_repo_url "$repo_name")"
      ;;
    *)
      echo "failed   ${GITEA_ORG}/${repo_name}  (HTTP ${code})" >&2
      sed 's/^/  /' "$body" >&2 || true
      rm -f "$body"
      return 1
      ;;
  esac
  rm -f "$body"
}

list_gitea_repos() {
  echo
  echo "Gitea repos (${GITEA_USER}):"
  curl -fsS -u "${GITEA_USER}:${GITEA_PASS}" \
    "${GITEA_URL}/api/v1/user/repos?limit=50" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in sorted(data, key=lambda x: x.get('name', '')):
    print(f\"  {r.get('name')}\")
" 2>/dev/null || curl -fsS -u "${GITEA_USER}:${GITEA_PASS}" \
    "${GITEA_URL}/api/v1/user/repos" | grep '"name"' || true
}

DRY_RUN=0
clusters=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-mgmt)
      INCLUDE_MGMT=1
      shift
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      clusters+=("$1")
      shift
      ;;
  esac
done

if [[ ${#clusters[@]} -eq 0 ]]; then
  clusters=("${DEFAULT_WORKLOAD_CLUSTERS[@]}")
fi

if [[ "$INCLUDE_MGMT" == "1" ]]; then
  clusters=(mgmt mgmt-staging "${clusters[@]}")
fi

echo "Gitea ${GITEA_URL} (user ${GITEA_USER}, org path ${GITEA_ORG}/)"
echo

failed=0
for cluster in "${clusters[@]}"; do
  case "$cluster" in
    mgmt|mgmt-staging|oai-packages) ;;
    central|regional|edge|ue) ;;
    *)
      echo "error: unknown cluster '${cluster}'" >&2
      exit 1
      ;;
  esac
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  if ! create_gitea_repo "$repo_name"; then
    failed=1
  fi
done

list_gitea_repos
exit "$failed"
