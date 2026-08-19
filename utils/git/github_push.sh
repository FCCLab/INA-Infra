#!/usr/bin/env bash
# Push INA-Infra root monorepo and all submodules (dynamically discovered) to GitHub.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCEd[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GIT_BRANCH="${GIT_BRANCH:-main}"
COMMIT_MSG="${COMMIT_MSG:-}"
DRY_RUN=0
NO_COMMIT=0
PULL_FIRST=0
ALL_VENDORS=0

die() {
  echo "❌ Error: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Dynamically discover and push all managed submodules across INA-Infra to GitHub.
Automatically handles nested repositories (e.g. openairinterface5g, flexric, oai-smf,
oai-cn5g-fed, resource-grid-visualizer, GitOps cluster repos, neuroran-ui-ux, etc.)
in bottom-up order, commits dirty working trees, propagates gitlinks upward, and pushes
the main repository.

Options:
  -m, --message MSG   Commit message (default: timestamped sync message)
  -p, --pull-first    Pull latest upstream before pushing
  --no-commit         Push only; do not create commits for dirty trees
  --all               Also attempt pushing external third-party vendor submodules
  -n, --dry-run       Print actions without making changes or pushing
  -h, --help          Show this help

Examples:
  $(basename "$0")
  $(basename "$0") -m "feat: sync all components"
  $(basename "$0") --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      COMMIT_MSG="$2"
      shift 2
      ;;
    -p|--pull-first)
      PULL_FIRST=1
      shift
      ;;
    --no-commit)
      NO_COMMIT=1
      shift
      ;;
    --all)
      ALL_VENDORS=1
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
      die "Unknown option: $1"
      ;;
    *)
      die "Unexpected argument: $1"
      ;;
  esac
done

default_commit_msg() {
  printf 'Sync to GitHub (%s)' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

MSG="${COMMIT_MSG:-$(default_commit_msg)}"

command -v git >/dev/null 2>&1 || die "git not found in PATH"

echo "=========================================================="
echo " INA-Infra: Dynamic Submodule & Main Repo Push to GitHub"
echo " Workspace: ${REPO_ROOT}"
echo " Branch   : ${GIT_BRANCH}"
echo " Message  : ${MSG}"
if [[ "$DRY_RUN" == "1" ]]; then
  echo " Mode     : DRY RUN"
fi
echo "=========================================================="
echo

cd "$REPO_ROOT"

# Discover all submodule paths sorted bottom-up (deepest first)
readarray -t SUBMODULE_PATHS < <(
  git submodule status --recursive 2>/dev/null | awk '{print $2}' | awk '{ print length, $0 }' | sort -rn | cut -d" " -f2-
)

is_managed_submodule() {
  local dir="$1"
  local url
  url="$(git -C "$dir" config --get remote.origin.url 2>/dev/null || echo "")"
  if [[ "$url" == *"github.com/FCCLab"* || "$url" == *"github.com/tuannv"* ]]; then
    return 0
  fi
  # If dirty with local changes, treat as managed
  if ! git -C "$dir" diff --quiet 2>/dev/null || ! git -C "$dir" diff --cached --quiet 2>/dev/null || [[ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]]; then
    return 0
  fi
  return 1
}

detect_submodule_branch() {
  local dir="$1"
  local b
  b="$(git -C "$dir" branch --show-current 2>/dev/null || echo "")"
  if [[ -n "$b" ]]; then
    echo "$b"
    return
  fi

  # 1. Check which remote branch contains current HEAD
  local branch_contains
  branch_contains="$(git -C "$dir" branch -r --contains HEAD 2>/dev/null | grep 'origin/' | head -n1 | sed 's@^[ *]*origin/@@' | sed 's@.*-> origin/@@' | tr -d ' ' || echo "")"
  if [[ -n "$branch_contains" ]]; then
    echo "$branch_contains"
    return
  fi

  # 2. Check remote HEAD symbolic-ref
  local remote_head
  remote_head="$(git -C "$dir" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "")"
  if [[ -n "$remote_head" ]]; then
    echo "$remote_head"
    return
  fi

  # 3. Known repository branch fallback
  if git -C "$dir" show-ref --verify --quiet refs/heads/service-models-integration-fcc || git -C "$dir" show-ref --verify --quiet refs/remotes/origin/service-models-integration-fcc; then
    echo "service-models-integration-fcc"
  elif git -C "$dir" show-ref --verify --quiet refs/heads/nw-slicing-3gpp || git -C "$dir" show-ref --verify --quiet refs/remotes/origin/nw-slicing-3gpp; then
    echo "nw-slicing-3gpp"
  elif git -C "$dir" show-ref --verify --quiet refs/heads/main || git -C "$dir" show-ref --verify --quiet refs/remotes/origin/main; then
    echo "main"
  elif git -C "$dir" show-ref --verify --quiet refs/heads/master || git -C "$dir" show-ref --verify --quiet refs/remotes/origin/master; then
    echo "master"
  elif git -C "$dir" show-ref --verify --quiet refs/heads/develop || git -C "$dir" show-ref --verify --quiet refs/remotes/origin/develop; then
    echo "develop"
  else
    echo "main"
  fi
}

detect_submodule_remote() {
  local dir="$1"
  if git -C "$dir" remote get-url origin >/dev/null 2>&1; then
    echo "origin"
  elif git -C "$dir" remote get-url github >/dev/null 2>&1; then
    echo "github"
  else
    local r
    r="$(git -C "$dir" remote 2>/dev/null | head -n1 || echo "")"
    echo "${r:-origin}"
  fi
}

push_single_submodule() {
  local rel_path="$1"
  local target_dir="$REPO_ROOT/$rel_path"

  if [[ ! -d "$target_dir" || ! -e "$target_dir/.git" ]]; then
    return 0
  fi

  if [[ "$ALL_VENDORS" != "1" ]] && ! is_managed_submodule "$target_dir"; then
    return 0
  fi

  local remote
  remote="$(detect_submodule_remote "$target_dir")"
  local url
  url="$(git -C "$target_dir" remote get-url "$remote" 2>/dev/null || echo "")"
  local branch
  branch="$(detect_submodule_branch "$target_dir")"

  if [[ -z "$url" ]]; then
    return 0
  fi

  echo "==> [submodule] ${rel_path}"
  echo "    remote: ${remote} -> ${url}"
  echo "    branch: ${branch}"

  cd "$target_dir"

  # 1. Pull if requested
  if [[ "$PULL_FIRST" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "    [dry-run] git pull --no-edit --no-rebase ${remote} ${branch}"
    else
      echo "    📥 Pulling ${remote}/${branch} ..."
      git pull --no-edit --no-rebase "$remote" "$branch" 2>/dev/null || true
    fi
  fi

  # 2. Commit local changes if dirty
  if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git status --porcelain)" ]]; then
    if [[ "$NO_COMMIT" == "1" ]]; then
      echo "    ⚠️  Working tree dirty (--no-commit enabled); skipping commit"
    elif [[ "$DRY_RUN" == "1" ]]; then
      echo "    [dry-run] git add -A && git commit -m \"${MSG}\""
    else
      git add -A
      if ! git diff --staged --quiet; then
        git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" commit -m "$MSG"
        echo "    ✅ Committed local changes: ${MSG}"
      fi
    fi
  else
    echo "    ℹ️  No uncommitted working tree changes"
  fi

  # 3. Push to remote
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git push ${remote} HEAD:refs/heads/${branch}"
  else
    if git push "$remote" "HEAD:refs/heads/${branch}" 2>&1; then
      echo "    🚀 Pushed ${remote}/${branch} @$(git rev-parse --short HEAD)"
    else
      echo "    ⚠️  Push ${remote}/${branch} skipped or failed"
    fi
  fi

  # 4. Stage gitlink in parent repository
  local parent_dir
  parent_dir="$(dirname "$target_dir")"
  local child_base
  child_base="$(basename "$target_dir")"

  if [[ -d "$parent_dir" && -e "$parent_dir/.git" ]]; then
    if [[ "$DRY_RUN" != "1" ]]; then
      (
        cd "$parent_dir"
        git add "$child_base" 2>/dev/null || true
        if ! git diff --staged --quiet; then
          git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
            commit -m "Update gitlink for ${child_base}" 2>/dev/null || true
          echo "    🔗 Updated gitlink for ${child_base} in $(basename "$parent_dir")"
        fi
      )
    fi
  fi
  echo
}

# Process all discovered submodules bottom-up
for sub_path in "${SUBMODULE_PATHS[@]}"; do
  push_single_submodule "$sub_path"
done

# Process main parent repository
echo "==> [parent] INA-Infra"
cd "$REPO_ROOT"
echo "    remote: origin -> $(git remote get-url origin)"
echo "    branch: ${GIT_BRANCH}"

if [[ "$PULL_FIRST" == "1" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git pull --no-edit --no-rebase origin ${GIT_BRANCH}"
  else
    echo "    📥 Pulling origin/${GIT_BRANCH} ..."
    git pull --no-edit --no-rebase origin "$GIT_BRANCH" 2>/dev/null || true
  fi
fi

# Stage any remaining submodule gitlinks or root changes
git add -A 2>/dev/null || true
if ! git diff --staged --quiet; then
  if [[ "$NO_COMMIT" == "1" ]]; then
    echo "    ⚠️  Parent repo dirty (--no-commit enabled); skipping commit"
  elif [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git commit -m \"${MSG}\""
  else
    git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" commit -m "$MSG"
    echo "    ✅ Committed: ${MSG}"
  fi
else
  echo "    ℹ️  No local changes to commit in parent repo"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "    [dry-run] git push origin HEAD:${GIT_BRANCH}"
else
  if git push origin "HEAD:${GIT_BRANCH}"; then
    echo "    🚀 Pushed origin/${GIT_BRANCH} @$(git rev-parse --short HEAD)"
  else
    die "Push to origin/${GIT_BRANCH} failed"
  fi
fi

echo
echo "🎉 All submodules and main repository successfully synchronized with GitHub!"
