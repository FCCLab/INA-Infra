#!/usr/bin/env bash
# Pull FCCLab/INA-Infra from GitHub and update submodules from .gitmodules / gitlinks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

GIT_BRANCH="${GIT_BRANCH:-main}"
DRY_RUN=0
FF_ONLY=0
USE_REMOTE=0

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Pull the parent repo from GitHub (origin → FCCLab/INA-Infra), then sync and
update all submodules recorded in .gitmodules.

Steps:
  1. git pull origin ${GIT_BRANCH}     (in repo root)
  2. git submodule sync --recursive
  3. git submodule update --init --recursive

Options:
  --ff-only           Pull with --ff-only (fail if merge required)
  --remote            Submodule update uses --remote (track branch tips)
  -b, --branch NAME   Branch to pull (default: main)
  -n, --dry-run       Print actions only
  -h, --help          Show this help

After pulling on the lab testbed, register Gitea remotes if needed:
  ./scripts/setup_lab_git_remotes.sh

Examples:
  $(basename "$0")
  $(basename "$0") --ff-only
  $(basename "$0") -b main -n
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ff-only)
      FF_ONLY=1
      shift
      ;;
    --remote)
      USE_REMOTE=1
      shift
      ;;
    -b|--branch)
      GIT_BRANCH="$2"
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
      die "unknown option: $1"
      ;;
    *)
      die "unexpected argument: $1"
      ;;
  esac
done

ensure_parent_origin() {
  local url
  url="$(github_repo_url "INA-Infra")"
  cd "$REPO_ROOT"
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$url"
  else
    git remote add origin "$url"
  fi
}

pull_parent() {
  local pull_args=(pull origin "$GIT_BRANCH")

  cd "$REPO_ROOT"
  echo "==> [parent] ${REPO_ROOT}"
  echo "    origin: $(git remote get-url origin)"
  echo "    branch: ${GIT_BRANCH}"

  if [[ "$FF_ONLY" == "1" ]]; then
    pull_args=(pull --ff-only origin "$GIT_BRANCH")
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: git ${pull_args[*]}"
    return 0
  fi

  if ! git "${pull_args[@]}"; then
    die "parent pull failed (resolve conflicts or retry with merge/rebase manually)"
  fi

  echo "    at $(git rev-parse --short HEAD) on ${GIT_BRANCH}"
}

update_submodules() {
  local update_args=(submodule update --init --recursive)

  if [[ "$USE_REMOTE" == "1" ]]; then
    update_args+=(--remote)
  fi

  cd "$REPO_ROOT"
  echo "==> [submodules] sync + update"
  echo "    paths: ${OAI_SLICE_DIR}, repos/* (+ nested under OAI slice)"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: git submodule sync --recursive"
    echo "    dry-run: git ${update_args[*]}"
    return 0
  fi

  git submodule sync --recursive
  git "${update_args[@]}"

  echo "    submodule status:"
  git submodule status --recursive | sed 's/^/      /'
}

command -v git >/dev/null 2>&1 || die "git not found in PATH"

echo "Pull from GitHub (branch=${GIT_BRANCH})"
echo

ensure_parent_origin
pull_parent
echo
update_submodules
echo
echo "Done."
