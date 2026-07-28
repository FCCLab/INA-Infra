#!/usr/bin/env bash
# For each repos/* Gitea submodule: cd → pull → commit (if dirty) → push.
# Exits immediately on merge conflict or any git failure.
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

ALL_PUSH_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

# One-shot auth (no permanent git config).
git_auth() {
  git -c "url.http://${GITEA_USER}:${GITEA_PASS}@${GITEA_HOST}:${GITEA_PORT}/.insteadOf=http://${GITEA_HOST}:${GITEA_PORT}/" "$@"
}

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

For each repos/<gitea-repo>/ submodule: pull, then push. Exit on conflict.
Default (no args): mgmt, central, regional, edge, ue.

repos/* are git submodules. After cloning the parent:
  git submodule update --init --recursive

Per repo:
  1. cd repos/<name>
  2. git pull (exit on conflict)
  3. git add -A && commit if dirty
  4. git pull again (exit on conflict)
  5. git push

Options:
  -m, --message MSG   Commit message
  -p, --pull-only     Pull only (no commit/push)
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Examples:
  $(basename "$0")
  $(basename "$0") --pull-only central
  $(basename "$0") -m "Add OpenSpeedTest" mgmt
EOF
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    mgmt|central|regional|edge|ue) return 0 ;;
    *) die "unknown cluster '${cluster}' (expected mgmt, central, regional, edge, or ue)" ;;
  esac
}

repo_source_dir() {
  local cluster="$1"
  printf '%s/%s' "$REPOS_DIR" "$(cluster_gitea_repo_name "$cluster")"
}

git_url_for_repo() {
  local repo_name="$1"
  printf 'http://%s:%s/%s/%s.git' \
    "$GITEA_HOST" "$GITEA_PORT" "$GITEA_ORG" "$repo_name"
}

# Pull origin/<branch>. On conflict: abort merge and exit the whole script.
pull_or_exit() {
  local branch="$1"
  local label="$2"

  echo "    pulling origin/${branch} ..."
  if git_auth pull --no-edit --no-rebase origin "$branch"; then
    return 0
  fi

  echo "error: [${label}] pull conflict with origin/${branch}; exiting" >&2
  echo "    conflicted files:" >&2
  git diff --name-only --diff-filter=U | sed 's/^/      /' >&2 || true
  git merge --abort 2>/dev/null || true
  exit 1
}

ensure_on_branch() {
  local branch="$1"
  local url="$2"

  git remote set-url origin "$url" 2>/dev/null || git remote add origin "$url"
  git_auth fetch origin "$branch"

  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    git checkout -q "$branch"
  else
    git checkout -q -B "$branch" "origin/${branch}"
  fi
  git branch -q --set-upstream-to="origin/${branch}" "$branch" 2>/dev/null || true
}

push_cluster_repo() {
  local cluster="$1"
  local repo_name src_dir safe_url rel_path msg

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  src_dir="$(repo_source_dir "$cluster")"
  rel_path="repos/${repo_name}"
  safe_url="$(git_url_for_repo "$repo_name")"

  if [[ "$PULL_ONLY" == "1" ]]; then
    echo "==> [${cluster}] pull ${src_dir}"
  else
    echo "==> [${cluster}] pull+push ${src_dir}"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: cd ${src_dir} && git pull origin ${GIT_BRANCH}"
    if [[ "$PULL_ONLY" != "1" ]]; then
      echo "    dry-run: commit if dirty && git pull && git push"
    fi
    return 0
  fi

  if [[ ! -e "$src_dir/.git" && ! -f "$src_dir/.git" ]]; then
    echo "    initializing submodule ${rel_path} ..."
    (
      cd "$REPO_ROOT"
      git_auth submodule update --init -- "$rel_path"
    ) || die "[${cluster}] submodule init failed for ${rel_path}"
  fi

  [[ -d "$src_dir" ]] || die "[${cluster}] missing dir ${src_dir}"

  cd "$src_dir"

  ensure_on_branch "$GIT_BRANCH" "$safe_url"
  echo "    at $(git rev-parse --short HEAD) on ${GIT_BRANCH}"

  # 1) Pull first
  pull_or_exit "$GIT_BRANCH" "$cluster"

  if [[ "$PULL_ONLY" == "1" ]]; then
    echo "    pull-only done @$(git rev-parse --short HEAD)"
    cd "$REPO_ROOT"
    return 0
  fi

  # 2) Commit local changes
  git add -A
  if git diff --staged --quiet; then
    echo "    no local changes"
  else
    msg="${COMMIT_MSG:-Update ${repo_name} ($(date -u +%Y-%m-%dT%H:%M:%SZ))}"
    git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
      commit -m "$msg"
    # 3) Pull again before push (exit on conflict)
    pull_or_exit "$GIT_BRANCH" "$cluster"
  fi

  # 4) Push
  if [[ "$(git rev-parse HEAD)" == "$(git rev-parse "origin/${GIT_BRANCH}")" ]]; then
    echo "    nothing to push"
  else
    echo "    pushing origin/${GIT_BRANCH} ..."
    git_auth push origin "HEAD:${GIT_BRANCH}" \
      || die "[${cluster}] push failed"
  fi

  # Refresh parent gitlink (stage only)
  cd "$REPO_ROOT"
  git add "$rel_path" 2>/dev/null || true

  echo "    done ${safe_url}@$(git -C "$src_dir" rev-parse --short HEAD)"
  echo "    Dashboard:   https://$(dashboard_mgmt_ip "$cluster"):$(dashboard_nodeport)"
  echo "    OpenSpeedTest: http://$(openspeedtest_vip "$cluster")"
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
    validate_cluster "$cluster"
    clusters+=("$cluster")
  done
fi

command -v git >/dev/null 2>&1 || die "git not found in PATH"

if [[ "$PULL_ONLY" == "1" ]]; then
  echo "Pull repos/ submodules (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
else
  echo "Pull+push repos/ submodules (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
fi
echo

for cluster in "${clusters[@]}"; do
  push_cluster_repo "$cluster"
  echo
done
