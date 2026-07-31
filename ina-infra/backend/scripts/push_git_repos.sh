#!/usr/bin/env bash
# For each repos/* submodule: pull from lab Gitea, push Gitea + mirror GitHub origin.
# Exits immediately on merge conflict or any git failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

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

For each repos/<cluster-repo>/ submodule: pull from Gitea, push Gitea, mirror GitHub.
Default (no args): mgmt, central, regional, edge, ue.

Clone parent from GitHub, then on the testbed run:
  ./scripts/setup_lab_git_remotes.sh

Per repo:
  1. cd repos/<name>
  2. git pull gitea/<branch>
  3. git add -A && commit if dirty
  4. git pull gitea again
  5. git push gitea
  6. git push origin (GitHub mirror)

Options:
  -m, --message MSG   Commit message
  -p, --pull-only     Pull from Gitea only (no commit/push)
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

gitea_url_for_cluster() {
  local cluster="$1"
  gitea_repo_url "$(cluster_gitea_repo_name "$cluster")"
}

github_url_for_cluster() {
  local cluster="$1"
  github_repo_url "$(github_gitops_repo_name "$cluster")"
}

# Pull remote/<branch>. On conflict: abort merge and exit the whole script.
pull_or_exit() {
  local remote="$1"
  local branch="$2"
  local label="$3"

  echo "    pulling ${remote}/${branch} ..."
  if [[ "$remote" == "gitea" ]]; then
    if git_auth pull --no-edit --no-rebase "$remote" "$branch"; then
      return 0
    fi
  elif git pull --no-edit --no-rebase "$remote" "$branch"; then
    return 0
  fi

  echo "error: [${label}] pull conflict with ${remote}/${branch}; exiting" >&2
  echo "    conflicted files:" >&2
  git diff --name-only --diff-filter=U | sed 's/^/      /' >&2 || true
  git merge --abort 2>/dev/null || true
  exit 1
}

ensure_remotes() {
  local cluster="$1"
  local gitea_url github_url

  gitea_url="$(gitea_url_for_cluster "$cluster")"
  github_url="$(github_url_for_cluster "$cluster")"

  if git remote get-url gitea >/dev/null 2>&1; then
    git remote set-url gitea "$gitea_url"
  else
    git remote add gitea "$gitea_url"
  fi

  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$github_url"
  else
    git remote add origin "$github_url"
  fi
}

ensure_on_branch() {
  local branch="$1"
  local cluster="$2"

  ensure_remotes "$cluster"
  git_auth fetch gitea "$branch"
  git fetch origin "$branch" 2>/dev/null || true

  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    git checkout -q "$branch"
  elif git show-ref --verify --quiet "refs/remotes/gitea/${branch}"; then
    git checkout -q -B "$branch" "gitea/${branch}"
  elif git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    git checkout -q -B "$branch" "origin/${branch}"
  else
    git checkout -q -B "$branch"
  fi
  git branch -q --set-upstream-to="gitea/${branch}" "$branch" 2>/dev/null || true
}

mirror_github() {
  local cluster="$1"
  local branch="$2"

  echo "    mirroring origin/${branch} (GitHub) ..."
  if git push origin "HEAD:${branch}"; then
    return 0
  fi
  echo "warning: [${cluster}] GitHub mirror push failed (Gitea push succeeded)" >&2
  return 0
}

push_cluster_repo() {
  local cluster="$1"
  local repo_name src_dir gitea_url github_url rel_path msg

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  src_dir="$(repo_source_dir "$cluster")"
  rel_path="repos/${repo_name}"
  gitea_url="$(gitea_url_for_cluster "$cluster")"
  github_url="$(github_url_for_cluster "$cluster")"

  if [[ "$PULL_ONLY" == "1" ]]; then
    echo "==> [${cluster}] pull ${src_dir} (gitea)"
  else
    echo "==> [${cluster}] pull+push ${src_dir} (gitea → GitHub)"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: cd ${src_dir} && git pull gitea ${GIT_BRANCH}"
    if [[ "$PULL_ONLY" != "1" ]]; then
      echo "    dry-run: commit if dirty && git pull gitea && git push gitea && git push origin"
    fi
    return 0
  fi

  if [[ ! -e "$src_dir/.git" && ! -f "$src_dir/.git" ]]; then
    if [[ -f "${REPO_ROOT}/.gitmodules" ]]; then
      echo "    initializing submodule ${rel_path} ..."
      (
        cd "$REPO_ROOT"
        git submodule update --init -- "$rel_path"
      ) || die "[${cluster}] submodule init failed for ${rel_path}"
    else
      die "[${cluster}] missing git repo at ${src_dir} (set REPOS_DIR)"
    fi
  fi

  [[ -d "$src_dir" ]] || die "[${cluster}] missing dir ${src_dir}"

  cd "$src_dir"

  ensure_on_branch "$GIT_BRANCH" "$cluster"
  echo "    at $(git rev-parse --short HEAD) on ${GIT_BRANCH}"
  echo "    gitea:  ${gitea_url}"
  echo "    github: ${github_url}"

  # 1) Pull from Gitea first
  pull_or_exit gitea "$GIT_BRANCH" "$cluster"

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
    pull_or_exit gitea "$GIT_BRANCH" "$cluster"
  fi

  # 3) Push to Gitea (Config Sync source of truth)
  if [[ "$(git rev-parse HEAD)" == "$(git rev-parse "gitea/${GIT_BRANCH}" 2>/dev/null || echo "")" ]]; then
    echo "    nothing to push to gitea"
  else
    echo "    pushing gitea/${GIT_BRANCH} ..."
    git_auth push gitea "HEAD:${GIT_BRANCH}" \
      || die "[${cluster}] gitea push failed"
  fi

  # 4) Mirror to GitHub
  mirror_github "$cluster" "$GIT_BRANCH"

  cd "$REPO_ROOT"
  git add "$rel_path" 2>/dev/null || true

  echo "    done gitea@${gitea_url} github@${github_url} @$(git -C "$src_dir" rev-parse --short HEAD)"
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
  echo "Pull repos/ from Gitea (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
else
  echo "Pull+push repos/ Gitea → GitHub (${GITEA_HOST}:${GITEA_PORT}): ${clusters[*]}"
fi
echo

for cluster in "${clusters[@]}"; do
  push_cluster_repo "$cluster"
  echo
done
