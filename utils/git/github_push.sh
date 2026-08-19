#!/usr/bin/env bash
# Push INA-Infra root monorepo and all submodules (including nested OAI repos) to GitHub.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GIT_BRANCH="${GIT_BRANCH:-main}"
COMMIT_MSG="${COMMIT_MSG:-}"
DRY_RUN=0
NO_COMMIT=0
PULL_FIRST=0

die() {
  echo "❌ Error: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Push INA-Infra monorepo and all submodules to GitHub (origin remote).

Order of operations:
  1. Nested submodules (OAI stack: smf -> cn5g-fed -> oai-slice)
  2. GitOps cluster submodules (repos/mgmt, repos/central-repo, repos/regional-repo, repos/edge-repo)
  3. Additional submodules (neuroran-ui-ux, 5gc-oai-packages, ran-oai-operators)
  4. Parent monorepo (gitlinks update + root workspace commit & push)

Options:
  -m, --message MSG   Commit message (default: timestamped sync message)
  -p, --pull-first    Pull latest upstream before pushing
  --no-commit         Push only; do not create commits for dirty trees
  -n, --dry-run       Print actions without making changes or pushing
  -h, --help          Show this help

Examples:
  $(basename "$0")
  $(basename "$0") -m "feat(gitops): sync cluster configs"
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

ensure_origin() {
  local url="$1"
  if git remote get-url origin >/dev/null 2>&1; then
    if [[ -n "$url" ]]; then
      git remote set-url origin "$url"
    fi
  else
    if [[ -n "$url" ]]; then
      git remote add origin "$url"
    fi
  fi
}

ensure_branch() {
  local branch="$1"
  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    git checkout -q "$branch"
  elif git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    git checkout -q -B "$branch" "origin/${branch}"
  fi
}

maybe_commit() {
  local label="$1"
  local msg="$2"

  if [[ "$NO_COMMIT" == "1" ]]; then
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "    ⚠️  [${label}] Working tree dirty (--no-commit enabled); skipping commit" >&2
    fi
    return 0
  fi

  git add -A
  if git diff --staged --quiet; then
    echo "    ℹ️  No local changes to commit"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git commit -m \"${msg}\""
    return 0
  fi

  git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
    commit -m "$msg"
  echo "    ✅ Committed: ${msg}"
}

push_origin() {
  local label="$1"
  local branch="$2"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git push origin HEAD:${branch}"
    return 0
  fi

  if git push origin "HEAD:${branch}"; then
    echo "    🚀 Pushed origin/${branch} @$(git rev-parse --short HEAD)"
    return 0
  fi
  die "[${label}] push to origin/${branch} failed"
}

pull_origin() {
  local label="$1"
  local branch="$2"

  if [[ "$PULL_FIRST" != "1" ]]; then
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] git pull --no-edit --no-rebase origin ${branch}"
    return 0
  fi

  echo "    📥 Pulling origin/${branch} ..."
  if ! git pull --no-edit --no-rebase origin "$branch"; then
    echo "    ⚠️  Pull failed or had conflicts, continuing..."
  fi
}

push_submodule_repo() {
  local rel_path="$1"
  local label="$2"
  local url="${3:-}"
  local branch="${4:-$GIT_BRANCH}"

  local target_dir="$REPO_ROOT/$rel_path"
  if [[ ! -d "$target_dir" ]]; then
    echo "⚠️  [${label}] Directory ${rel_path} does not exist; skipping"
    return 0
  fi

  echo "==> [${label}] ${rel_path}"
  cd "$target_dir"

  if [[ -n "$url" ]]; then
    ensure_origin "$url"
  fi
  ensure_branch "$branch"
  pull_origin "$label" "$branch"
  maybe_commit "$label" "$MSG"
  push_origin "$label" "$branch"
  echo
}

stage_gitlink() {
  local parent_rel="$1"
  local child_rel="$2"
  local msg="$3"
  local parent_dir="$REPO_ROOT/$parent_rel"

  [[ -d "$parent_dir" ]] || return 0
  cd "$parent_dir"
  git add "$child_rel" 2>/dev/null || true

  if [[ "$NO_COMMIT" == "1" ]]; then
    return 0
  fi
  if git diff --staged --quiet; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] stage gitlink ${child_rel} in ${parent_rel}"
    return 0
  fi
  git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
    commit -m "$msg"
  echo "    ✅ Updated gitlink ${child_rel} in ${parent_rel}"
}

command -v git >/dev/null 2>&1 || die "git not found in PATH"

echo "========================================================"
echo " INA-Infra: Push Main Repo and All Submodules to GitHub"
echo " Branch : ${GIT_BRANCH}"
echo " Message: ${MSG}"
if [[ "$DRY_RUN" == "1" ]]; then
  echo " Mode   : DRY RUN"
fi
echo "========================================================"
echo

# 1. Nested OAI components (bottom-up)
if [[ -d "$REPO_ROOT/INA-Infra-oai-slice-implementation/oai-cn5g-fed/component/oai-smf" ]]; then
  push_submodule_repo "INA-Infra-oai-slice-implementation/oai-cn5g-fed/component/oai-smf" "oai-smf" "https://github.com/FCCLab/INA-Infra-oai-smf.git"
  stage_gitlink "INA-Infra-oai-slice-implementation/oai-cn5g-fed" "component/oai-smf" "Update oai-smf gitlink"
fi

if [[ -d "$REPO_ROOT/INA-Infra-oai-slice-implementation/oai-cn5g-fed" ]]; then
  push_submodule_repo "INA-Infra-oai-slice-implementation/oai-cn5g-fed" "oai-cn5g-fed" "https://github.com/FCCLab/INA-Infra-oai-cn5g-fed.git"
  stage_gitlink "INA-Infra-oai-slice-implementation" "oai-cn5g-fed" "Update oai-cn5g-fed gitlink"
fi

if [[ -d "$REPO_ROOT/INA-Infra-oai-slice-implementation" ]]; then
  push_submodule_repo "INA-Infra-oai-slice-implementation" "oai-slice" "https://github.com/FCCLab/INA-Infra-oai-slice-implementation.git"
  stage_gitlink "." "INA-Infra-oai-slice-implementation" "Update INA-Infra-oai-slice-implementation gitlink"
fi

# 2. GitOps cluster repositories
GITOPS_REPOS=("repos/mgmt:INA-Infra-mgmt" "repos/central-repo:INA-Infra-central-repo" "repos/regional-repo:INA-Infra-regional-repo" "repos/edge-repo:INA-Infra-edge-repo")
for entry in "${GITOPS_REPOS[@]}"; do
  repo_path="${entry%%:*}"
  repo_gh="${entry##*:}"
  push_submodule_repo "$repo_path" "gitops-$(basename "$repo_path")" "https://github.com/FCCLab/${repo_gh}.git"
  stage_gitlink "." "$repo_path" "Update ${repo_path} gitlink"
done

# 3. Additional submodules
push_submodule_repo "ina-infra/frontend/neuroran-ui-ux" "neuroran-ui-ux" "https://github.com/FCCLab/neuroran-ui-ux.git"
stage_gitlink "." "ina-infra/frontend/neuroran-ui-ux" "Update neuroran-ui-ux gitlink"

push_submodule_repo "third_party/INA-Infra-5gc-oai-packages" "5gc-oai-packages" "https://github.com/FCCLab/INA-Infra-5gc-oai-packages.git"
stage_gitlink "." "third_party/INA-Infra-5gc-oai-packages" "Update 5gc-oai-packages gitlink"

push_submodule_repo "third_party/INA-Infra-ran-oai-operators" "ran-oai-operators" "https://github.com/FCCLab/INA-Infra-ran-oai-operators.git"
stage_gitlink "." "third_party/INA-Infra-ran-oai-operators" "Update ran-oai-operators gitlink"

# 4. Main parent repository
echo "==> [parent] INA-Infra"
cd "$REPO_ROOT"
ensure_origin "https://github.com/FCCLab/INA-Infra.git"
ensure_branch "$GIT_BRANCH"
pull_origin "parent" "$GIT_BRANCH"
maybe_commit "parent" "$MSG"
push_origin "parent" "$GIT_BRANCH"

echo
echo "🎉 All repositories successfully synchronized with GitHub!"
