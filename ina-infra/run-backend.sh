#!/usr/bin/env bash
# Run FastAPI on the host (required for host-locked Gurobi academic licenses).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export INA_INFRA_ROOT="$ROOT"
# GitOps checkouts: sibling monorepo repos/ if present, else ina-infra/repos.
if [[ -z "${REPOS_DIR:-}" ]]; then
  if [[ -d "$ROOT/../repos" ]]; then
    export REPOS_DIR="$(cd "$ROOT/../repos" && pwd)"
  else
    export REPOS_DIR="$ROOT/repos"
  fi
fi
# Optional PL solver sources (not required for Apply/GitOps alone).
if [[ -z "${INA_SRC:-}" ]]; then
  if [[ -d "$ROOT/algorithm/new_implementation/ina" ]]; then
    export INA_SRC="$ROOT/algorithm/new_implementation"
  elif [[ -d "$ROOT/../algorithm/new_implementation/ina" ]]; then
    export INA_SRC="$(cd "$ROOT/../algorithm/new_implementation" && pwd)"
  fi
fi
export PUSH_SCRIPT="${PUSH_SCRIPT:-$ROOT/backend/scripts/push_git_repos.sh}"
export SSH_CFG="${SSH_CFG:-$ROOT/backend/scripts/ssh_config}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export GRB_LICENSE_FILE="${GRB_LICENSE_FILE:-$HOME/gurobi.lic}"
export INA_DB_PATH="${INA_DB_PATH:-$ROOT/data/profiles.db}"

cd "$ROOT/backend"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8082}" "$@"
