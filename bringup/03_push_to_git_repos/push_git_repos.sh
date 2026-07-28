#!/usr/bin/env bash
# Pull latest from Gitea into repos/, then commit overlay and push.
# Never pushes without a successful pull+merge first.
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
PULL_ONLY=0
REPLACE_REMOTE=0

ALL_PUSH_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Pull latest Gitea content into repos/<gitea-repo>/, then push local overlay.
Default (no args): mgmt, central, regional, edge, ue.

Flow per repo (pull first — never push immediately):
  1. Clone/pull latest from Gitea
  2. Merge remote into local repos/<name>/ (remote-only files added;
     existing local files kept — local wins on same path)
  3. Build worktree from updated local tree and commit (if changes)
  4. Fetch again and merge origin/<branch>
  5. On merge conflict: abort (no push), list conflicted files
  6. On clean merge: push

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
  -p, --pull-only     Only pull Gitea → repos/ (no commit/push)
  -r, --replace       Skip pull-into-local; replace remote with local tree
                      (old behavior: local is sole source of truth)
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Environment:
  GITEA_HOST GITEA_PORT GITEA_USER GITEA_PASS GITEA_ORG
  REPOS_DIR             Source tree (default: repos/ at repo root)
  GIT_BRANCH            Branch to push (default: main)
  COMMIT_MSG            Commit message

Examples:
  $(basename "$0")
  $(basename "$0") --pull-only central
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
# Always fetch before deciding; never push without this succeeding.
merge_origin_or_abort() {
  local branch="$1"
  local local_rev remote_rev

  echo "    fetching origin/${branch} ..."
  git fetch origin "$branch"
  local_rev="$(git rev-parse HEAD)"
  remote_rev="$(git rev-parse "origin/${branch}")"
  if [[ "$local_rev" == "$remote_rev" ]]; then
    echo "    origin/${branch} already at HEAD"
    return 0
  fi

  if git merge-base --is-ancestor "origin/${branch}" HEAD 2>/dev/null; then
    # We are strictly ahead of remote.
    echo "    ahead of origin/${branch} (fast-forward push)"
    return 0
  fi

  # Shallow clone may lack merge-base; deepen once and retry ancestor check.
  if ! git merge-base HEAD "origin/${branch}" >/dev/null 2>&1; then
    echo "    deepening clone for merge-base ..."
    git fetch --deepen 200 origin "$branch" || true
  fi

  if git merge-base --is-ancestor "origin/${branch}" HEAD 2>/dev/null; then
    echo "    ahead of origin/${branch} (fast-forward push)"
    return 0
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

# Pull remote-only paths into local repos/ (local existing files win).
pull_remote_into_local() {
  local work_dir="$1"
  local src_dir="$2"

  mkdir -p "$src_dir"
  # --ignore-existing: do not overwrite local renders; only add missing remote files.
  rsync -a --ignore-existing --exclude '.git' "${work_dir}/" "${src_dir}/"
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

  if [[ "$PULL_ONLY" == "1" ]]; then
    echo "==> [${cluster}] pull-only ${safe_url} → ${src_dir}"
  else
    echo "==> [${cluster}] pull-then-push ${src_dir} → ${GITEA_ORG}/${repo_name}"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: git clone ${safe_url} (branch ${GIT_BRANCH})"
    if [[ "$REPLACE_REMOTE" == "1" ]]; then
      echo "    dry-run: replace workdir with ${src_dir}/ (no pull-into-local)"
    else
      echo "    dry-run: rsync --ignore-existing remote → ${src_dir}/ (pull first)"
    fi
    if [[ "$PULL_ONLY" != "1" ]]; then
      echo "    dry-run: overlay updated ${src_dir}/ → workdir/ + commit if dirty"
      echo "    dry-run: fetch/merge origin/${GIT_BRANCH}; on conflict abort; else push"
    fi
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

  # Step 1: pull latest into local repos/ first (unless --replace).
  if [[ "$REPLACE_REMOTE" == "1" ]]; then
    echo "    --replace: skipping pull-into-local (local tree replaces remote)"
  else
    echo "    syncing remote → ${src_dir}/ (keep local files, add remote-only)"
    pull_remote_into_local "$work_dir" "$src_dir"
  fi

  if [[ "$PULL_ONLY" == "1" ]]; then
    if [[ "$REPLACE_REMOTE" == "1" ]]; then
      echo "    --pull-only with --replace: syncing remote → ${src_dir}/ (full mirror)"
      mkdir -p "$src_dir"
      rsync -a --delete --exclude '.git' "${work_dir}/" "${src_dir}/"
    fi
    rm -rf "$work_dir"
    echo "    pull-only done (no push)"
    return 0
  fi

  # Step 2: build push worktree from updated local tree.
  find "$work_dir" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
  cp -a "$src_dir"/. "$work_dir/"

  msg="${COMMIT_MSG:-Update ${repo_name} from repos/ ($(date -u +%Y-%m-%dT%H:%M:%SZ))}"

  (
    cd "$work_dir"
    git add -A
    if git diff --staged --quiet; then
      echo "    no local changes after pull (already matches ${safe_url}@${GIT_BRANCH})"
      exit 0
    fi

    git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
      commit -m "$msg"

    # Step 3: re-fetch/merge before push; refuse push on conflict.
    if ! merge_origin_or_abort "$GIT_BRANCH"; then
      exit 1
    fi

    echo "    pushing origin/${GIT_BRANCH} ..."
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
    -p|--pull-only)
      PULL_ONLY=1
      shift
      ;;
    -r|--replace)
      REPLACE_REMOTE=1
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
if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync not found in PATH (required to pull remote → repos/)" >&2
  exit 1
fi

if [[ "$PULL_ONLY" == "1" ]]; then
  echo "Pull-only Gitea → repos/ (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
else
  echo "Pull-then-push repos/ ↔ Gitea (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
fi
echo

failed=0
for cluster in "${clusters[@]}"; do
  if ! push_cluster_repo "$cluster"; then
    failed=1
  fi
  echo
done

exit "$failed"
