#!/usr/bin/env bash
# Push all FCCLab/INA-Infra-* repos to GitHub (origin remote).
# Order: nested OAI repos first, then gitops submodules, then parent monorepo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

GIT_BRANCH="${GIT_BRANCH:-main}"
COMMIT_MSG="${COMMIT_MSG:-}"
DRY_RUN=0
NO_COMMIT=0

ALL_GITOPS_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")
ALL_TARGETS=(oai-smf oai-cn5g-fed oai-slice gitops parent)

SMF_REL="${OAI_SLICE_DIR}/oai-cn5g-fed/component/oai-smf"
FED_REL="${OAI_SLICE_DIR}/oai-cn5g-fed"
SLICE_REL="${OAI_SLICE_DIR}"

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [target ...]

Push local trees to GitHub (origin → FCCLab/INA-Infra-*).

Default targets (all): oai-smf oai-cn5g-fed oai-slice gitops parent

Targets:
  oai-smf       ${SMF_REL}
  oai-cn5g-fed  ${FED_REL} (includes oai-smf gitlink refresh when oai-smf selected)
  oai-slice     ${SLICE_REL}
  gitops        repos/mgmt … repos/ue-repo
  parent        FCCLab/INA-Infra (submodule gitlinks + repo root)

Options:
  -m, --message MSG   Commit message (default: timestamped sync message)
  --no-commit         Push only; do not create commits for dirty trees
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Examples:
  $(basename "$0")
  $(basename "$0") -m "sync gitops" gitops parent
  $(basename "$0") --no-commit oai-smf
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      COMMIT_MSG="$2"
      shift 2
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
    oai-smf|oai-cn5g-fed|oai-slice|gitops|parent)
      break
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      die "unknown target: $1"
      ;;
  esac
done

targets=()
if [[ $# -eq 0 ]]; then
  targets=("${ALL_TARGETS[@]}")
else
  for t in "$@"; do
    case "$t" in
      oai-smf|oai-cn5g-fed|oai-slice|gitops|parent) targets+=("$t") ;;
      *) die "unknown target: $t" ;;
    esac
  done
fi

default_commit_msg() {
  printf 'Sync to GitHub (%s)' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

target_enabled() {
  local want="$1"
  local t
  for t in "${targets[@]}"; do
    [[ "$t" == "$want" ]] && return 0
  done
  return 1
}

ensure_submodule_path() {
  local rel="$1"
  if [[ -e "$REPO_ROOT/$rel/.git" || -f "$REPO_ROOT/$rel/.git" ]]; then
    return 0
  fi
  echo "    initializing submodule ${rel} ..."
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  (
    cd "$REPO_ROOT"
    git submodule update --init -- "$rel"
  ) || die "submodule init failed: ${rel}"
}

ensure_origin() {
  local url="$1"
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$url"
  else
    git remote add origin "$url"
  fi
}

ensure_branch() {
  local branch="$1"
  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    git checkout -q "$branch"
  else
    git checkout -q -B "$branch"
  fi
}

maybe_commit() {
  local label="$1"
  local msg="$2"

  if [[ "$NO_COMMIT" == "1" ]]; then
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "    warning: [${label}] dirty tree (--no-commit); pushing existing HEAD" >&2
    fi
    return 0
  fi

  git add -A
  if git diff --staged --quiet; then
    echo "    no local changes"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: commit in ${label}"
    return 0
  fi

  git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
    commit -m "$msg"
}

push_origin() {
  local label="$1"
  local branch="$2"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: git push origin HEAD:${branch}"
    return 0
  fi

  if git push origin "HEAD:${branch}"; then
    echo "    pushed origin/${branch} @$(git rev-parse --short HEAD)"
    return 0
  fi
  die "[${label}] push to origin/${branch} failed"
}

push_repo() {
  local rel_path="$1"
  local gh_repo_name="$2"
  local label="$3"
  local msg="$4"
  local url

  url="$(github_repo_url "$gh_repo_name")"
  echo "==> [${label}] ${rel_path}"
  echo "    github: ${url}"

  ensure_submodule_path "$rel_path"
  if [[ "$DRY_RUN" == "1" && ! -d "$REPO_ROOT/$rel_path" ]]; then
    echo "    dry-run: skip (path missing)"
    return 0
  fi

  cd "$REPO_ROOT/$rel_path"
  ensure_origin "$url"
  ensure_branch "$GIT_BRANCH"
  maybe_commit "$label" "$msg"
  push_origin "$label" "$GIT_BRANCH"
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
    echo "    dry-run: commit gitlink ${child_rel} in ${parent_rel}"
    return 0
  fi
  git -c user.name="nephio-gitops" -c user.email="nephio@nephio.org" \
    commit -m "$msg"
}

MSG="${COMMIT_MSG:-$(default_commit_msg)}"

command -v git >/dev/null 2>&1 || die "git not found in PATH"

echo "Push to GitHub (branch=${GIT_BRANCH}): ${targets[*]}"
echo

# --- OAI stack (deepest first) ---
if target_enabled oai-smf; then
  push_repo "$SMF_REL" "INA-Infra-oai-smf" "oai-smf" "$MSG"
  echo
fi

if target_enabled oai-cn5g-fed || target_enabled oai-smf; then
  if target_enabled oai-smf; then
    stage_gitlink "$FED_REL" "component/oai-smf" "Update oai-smf gitlink for GitHub"
  fi
  if target_enabled oai-cn5g-fed; then
    push_repo "$FED_REL" "INA-Infra-oai-cn5g-fed" "oai-cn5g-fed" "$MSG"
    echo
  fi
fi

if target_enabled oai-slice || target_enabled oai-cn5g-fed || target_enabled oai-smf; then
  if target_enabled oai-cn5g-fed || target_enabled oai-smf; then
    stage_gitlink "$SLICE_REL" "oai-cn5g-fed" "Update oai-cn5g-fed gitlink for GitHub"
  fi
  if target_enabled oai-slice; then
    push_repo "$SLICE_REL" "INA-Infra-oai-slice-implementation" "oai-slice" "$MSG"
    echo
  fi
fi

# --- GitOps submodules ---
if target_enabled gitops; then
  for cluster in "${ALL_GITOPS_CLUSTERS[@]}"; do
    repo_name="$(cluster_gitea_repo_name "$cluster")"
    gh_name="$(github_gitops_repo_name "$cluster")"
    push_repo "repos/${repo_name}" "$gh_name" "gitops-${cluster}" "$MSG"
    stage_gitlink "." "repos/${repo_name}" "Update repos/${repo_name} gitlink for GitHub"
    echo
  done
fi

# --- Parent monorepo ---
if target_enabled parent || target_enabled gitops \
  || target_enabled oai-slice || target_enabled oai-cn5g-fed || target_enabled oai-smf; then
  if target_enabled oai-slice || target_enabled oai-cn5g-fed || target_enabled oai-smf; then
    stage_gitlink "." "$SLICE_REL" "Update ${SLICE_REL} gitlink for GitHub"
  fi
  if target_enabled parent; then
    cd "$REPO_ROOT"
    ensure_origin "$(github_repo_url "INA-Infra")"
    ensure_branch "$GIT_BRANCH"
    echo "==> [parent] ${REPO_ROOT}"
    echo "    github: $(github_repo_url INA-Infra)"
    maybe_commit "parent" "$MSG"
    push_origin "parent" "$GIT_BRANCH"
    echo
  fi
fi

echo "Done."
