#!/usr/bin/env bash
# Run FastAPI on the host (required for host-locked Gurobi academic licenses).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
export INA_SRC="${INA_SRC:-$REPO_ROOT/algorithm/new_implementation}"
export REPO_ROOT
export REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
export PUSH_SCRIPT="${PUSH_SCRIPT:-$REPO_ROOT/bringup/03_push_to_git_repos/push_git_repos.sh}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export GRB_LICENSE_FILE="${GRB_LICENSE_FILE:-$HOME/gurobi.lic}"

cd "$ROOT/backend"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8082}" "$@"
