#!/usr/bin/env bash
# Pull latest from Gitea, overlay repos/{gitea-repo}/, merge (abort on conflict), push.
# Used for Config Sync unstructured GitOps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"

GITEA_HOST="${GITEA_HOST:-10.1.132.200}"
GITEA_PORT="${GITEA_PORT:-3000}"
GITEA_USER="${GITEA_USER:-nephio}"
GITEA_PASS="${GITEA_PASS:-secret}"
GITEA_ORG="${GITEA_ORG:-nephio}"
GIT_BRANCH="${GIT_BRANCH:-main}"
COMMIT_MSG="${COMMIT_MSG:-}"
DRY_RUN=0

ALL_PUSH_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Push GitOps content from repos/<gitea-repo>/ to Gitea for Config Sync.
Default (no args): mgmt, central, regional, edge, ue.

Flow per repo:
  1. Clone/pull latest from Gitea
  2. Overlay local repos/<name>/ and commit (if changes)
  3. Fetch again and merge origin/<branch>
  4. On merge conflict: abort (no push), list conflicted files
  5. On clean merge: push

Each cluster maps to a Gitea repo (see repos/):
  mgmt      → repos/mgmt              → nephio/mgmt
  central   → repos/central-repo      → nephio/central-repo
  regional  → repos/regional-repo     → nephio/regional-repo
  edge      → repos/edge-repo         → nephio/edge-repo
  ue        → repos/ue-repo           → nephio/ue-repo

Prerequisites:
  - Gitea repos exist (./bringup/02_configsync/configsync.sh repos)
  - Config Sync + RootSync on each target cluster
  - CNI: Flannel in repos/ (namespaces/kube-flannel/, cluster/)
  - MetalLB deployed (openspeedtest LoadBalancer VIPs only)
  - git on this host

Options:
  -m, --message MSG   Git commit message
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Environment:
  GITEA_HOST GITEA_PORT GITEA_USER GITEA_PASS GITEA_ORG
  REPOS_DIR             Source tree (default: repos/ at repo root)
  GIT_BRANCH            Branch to push (default: main)
  COMMIT_MSG            Commit message

Examples:
  $(basename "$0")
  $(basename "$0") central regional
  $(basename "$0") -m "Add OpenSpeedTest" mgmt

After push, verify:
  ./scripts/check-configsync.sh
EOF
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt|central|regional|edge|ue) return 0 ;;
    *)
      echo "error: unknown cluster '${cluster}' (expected mgmt, central, regional, edge, or ue)" >&2
      return 1
      ;;
  esac
}

repo_source_dir() {
  local cluster="$1"
  local repo_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  printf '%s/%s' "$REPOS_DIR" "$repo_name"
}

git_url_for_repo() {
  local repo_name="$1"
  printf 'http://%s:%s@%s:%s/%s/%s.git' \
    "$GITEA_USER" "$GITEA_PASS" "$GITEA_HOST" "$GITEA_PORT" "$GITEA_ORG" "$repo_name"
}

# Fetch origin/<branch> and merge into HEAD. On conflict: list files, abort, return 1.
merge_origin_or_abort() {
  local branch="$1"

  git fetch origin "$branch"
  if git merge-base --is-ancestor "origin/${branch}" HEAD 2>/dev/null; then
    # HEAD already contains remote tip (remote unchanged or we are ahead).
    return 0
  fi

  # Shallow clone may lack merge-base; deepen once and retry ancestor check.
  if ! git merge-base HEAD "origin/${branch}" >/dev/null 2>&1; then
    echo "    deepening clone for merge-base ..."
    git fetch --deepen 200 origin "$branch" || true
  fi

  echo "    merging origin/${branch} ..."
  if git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
      merge --no-edit "origin/${branch}"; then
    return 0
  fi

  echo "error: merge conflict with origin/${branch}; not pushing" >&2
  echo "    conflicted files:" >&2
  git diff --name-only --diff-filter=U | sed 's/^/      /' >&2 || true
  git merge --abort 2>/dev/null || true
  return 1
}

push_cluster_repo() {
  local cluster="$1"
  local repo_name src_dir git_url work_dir rc=0
  local safe_url msg
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  src_dir="$(repo_source_dir "$cluster")"
  git_url="$(git_url_for_repo "$repo_name")"
  safe_url="http://${GITEA_HOST}:${GITEA_PORT}/${GITEA_ORG}/${repo_name}"

  if [[ ! -d "$src_dir" ]]; then
    echo "error: [${cluster}] missing source dir ${src_dir}" >&2
    return 1
  fi

  if [[ ! -d "$src_dir/namespaces" && ! -d "$src_dir/cluster" ]]; then
    echo "error: [${cluster}] ${src_dir} has no namespaces/ or cluster/ (Config Sync unstructured layout)" >&2
    return 1
  fi

  echo "==> [${cluster}] pull+push ${src_dir} → ${GITEA_ORG}/${repo_name}"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: git clone ${safe_url} (branch ${GIT_BRANCH})"
    echo "    dry-run: overlay ${src_dir}/ → workdir/"
    echo "    dry-run: commit if dirty → fetch/merge origin/${GIT_BRANCH}"
    echo "    dry-run: on conflict abort; else git push origin ${GIT_BRANCH}"
    return 0
  fi

  work_dir="$(mktemp -d)"

  # Depth > 1 so a raced remote commit can still merge against a common base.
  if ! git clone --depth 50 --branch "$GIT_BRANCH" "$git_url" "$work_dir" 2>/dev/null; then
    git clone --depth 50 "$git_url" "$work_dir"
    (
      cd "$work_dir"
      git checkout -b "$GIT_BRANCH" 2>/dev/null || git checkout "$GIT_BRANCH"
    )
  fi

  echo "    pulled ${safe_url}@$(git -C "$work_dir" rev-parse --short HEAD)"

  find "$work_dir" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
  cp -a "$src_dir"/. "$work_dir/"

  msg="${COMMIT_MSG:-Update ${repo_name} from repos/ ($(date -u +%Y-%m-%dT%H:%M:%SZ))}"

  (
    cd "$work_dir"
    git add -A
    if git diff --staged --quiet; then
      echo "    no local changes (already matches ${safe_url}@${GIT_BRANCH})"
      exit 0
    fi

    git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
      commit -m "$msg"

    # Re-check remote in case it moved after clone; refuse push on conflict.
    if ! merge_origin_or_abort "$GIT_BRANCH"; then
      exit 1
    fi

    git push origin "HEAD:${GIT_BRANCH}"
  ) || rc=$?

  rm -rf "$work_dir"

  if [[ "$rc" -eq 0 ]]; then
    echo "    pushed ${safe_url}"
    echo "    Dashboard:   https://$(dashboard_mgmt_ip "$cluster"):$(dashboard_nodeport)"
    echo "    OpenSpeedTest: http://$(openspeedtest_vip "$cluster")"
  fi
  return "$rc"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      COMMIT_MSG="$2"
      shift 2
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
      break
      ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_PUSH_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    clusters+=("$cluster")
  done
fi

if ! command -v git >/dev/null 2>&1; then
  echo "error: git not found in PATH" >&2
  exit 1
fi

echo "Pull+merge+push repos/ → Gitea (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
echo

failed=0
for cluster in "${clusters[@]}"; do
  if ! push_cluster_repo "$cluster"; then
    failed=1
  fi
  echo
done

exit "$failed"
