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
export PUSH_SCRIPT="${PUSH_SCRIPT:-$ROOT/backend/scripts/push_gitea_gitops.sh}"
export SSH_CFG="${SSH_CFG:-$ROOT/backend/scripts/ssh_config}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export GRB_LICENSE_FILE="${GRB_LICENSE_FILE:-$HOME/gurobi.lic}"
export INA_DB_PATH="${INA_DB_PATH:-$ROOT/data/profiles.db}"

daemon=1
uvicorn_args=()
for arg in "$@"; do
  case "$arg" in
    -d|--daemon)
      daemon=1
      ;;
    -f|--foreground)
      daemon=0
      ;;
    *)
      uvicorn_args+=("$arg")
      ;;
  esac
done

cd "$ROOT/backend"
mkdir -p "$ROOT/logs"
if (( daemon )); then
  pid_file="${INA_BACKEND_PID:-$ROOT/logs/backend.pid}"
  log_file="${INA_BACKEND_LOG:-$ROOT/logs/backend.log}"

  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    read -r existing_pid <"$pid_file"
    printf 'Backend is already running (PID %s)\n' "$existing_pid"
    exit 0
  fi

  nohup python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port "${PORT:-8082}" "${uvicorn_args[@]}" \
    >"$log_file" 2>&1 < /dev/null &
  backend_pid=$!
  printf '%s\n' "$backend_pid" >"$pid_file"
  printf 'Backend started in background (PID %s)\nLog: %s\n' "$backend_pid" "$log_file"
else
  exec python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port "${PORT:-8082}" "${uvicorn_args[@]}"
fi
