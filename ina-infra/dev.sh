#!/usr/bin/env bash
# Local dev: backend (uvicorn --reload) + frontend (Vite HMR in Docker).
#
# Usage:
#   ./dev up              # both in background (default)
#   ./dev up -f           # both in foreground (Ctrl+C to stop)
#   ./dev up backend      # API only (background)
#   ./dev down            # stop
#   ./dev logs            # tail logs
#
# UI  → http://127.0.0.1:5180
# API → http://127.0.0.1:8082/docs
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
BACKEND_LOG="${INA_BACKEND_LOG:-$LOG_DIR/backend.log}"
FRONTEND_CONTAINER="${FRONTEND_DEV_CONTAINER:-ina-infra-frontend-dev}"
BACKEND_PORT="${PORT:-8082}"
FRONTEND_PORT="${FRONTEND_PORT:-5180}"
API_PROXY="${VITE_API_PROXY:-http://host.docker.internal:${BACKEND_PORT}}"
FRONTEND_IMAGE="${FRONTEND_DEV_IMAGE:-node:20-bookworm}"

usage() {
  cat <<EOF
Usage: $0 [command] [target] [options]

Commands:
  up [target]     Start dev stack (background by default)
  down|stop       Stop dev backend + frontend
  logs            Tail backend + frontend logs
  status          Show dev stack status

Targets (with 'up'):
  both            Backend + frontend (default)
  backend         API only
  frontend        UI only

Options (with 'up'):
  -f, --foreground   Run in foreground (logs to terminal; Ctrl+C stops)

Examples:
  $0                # up both (background)
  $0 up -f          # up both (foreground)
  $0 up backend
  $0 logs
  $0 down

Env:
  PORT              Backend port (default: 8082)
  FRONTEND_PORT     Vite port on host (default: 5180)
  VITE_API_PROXY    Vite proxy target
  INA_SRC           PL/PM/PS solver tree (auto-detected)
EOF
}

setup_env() {
  export INA_INFRA_ROOT="$ROOT"
  if [[ -z "${REPOS_DIR:-}" ]]; then
    if [[ -d "$ROOT/../repos" ]]; then
      export REPOS_DIR="$(cd "$ROOT/../repos" && pwd)"
    else
      export REPOS_DIR="$ROOT/repos"
    fi
  fi
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
}

stop_daemon_backend() {
  local pid_file="$ROOT/logs/backend.pid"
  # Legacy path from older runs
  if [[ ! -f "$pid_file" && -f "$ROOT/backend/backend.pid" ]]; then
    pid_file="$ROOT/backend/backend.pid"
  fi
  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    echo "Stopping daemon backend (PID $(<"$pid_file")) …"
    kill "$(<"$pid_file")" 2>/dev/null || true
    rm -f "$pid_file"
  fi
}

stop_dev_backend() {
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    local pid
    pid="$(<"$BACKEND_PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping dev backend (PID $pid) …"
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$BACKEND_PID_FILE"
  fi
}

stop_dev_frontend() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$FRONTEND_CONTAINER"; then
    echo "Stopping dev frontend container …"
    docker rm -f "$FRONTEND_CONTAINER" >/dev/null 2>&1 || true
  fi
  if docker compose ps --status running 2>/dev/null | grep -q frontend; then
    docker compose -f "$ROOT/docker-compose.yml" stop frontend >/dev/null 2>&1 || true
  fi
}

stop_dev() {
  stop_dev_backend
  stop_dev_frontend
}

backend_running() {
  [[ -f "$BACKEND_PID_FILE" ]] && kill -0 "$(<"$BACKEND_PID_FILE")" 2>/dev/null
}

frontend_running() {
  docker ps --format '{{.Names}}' | grep -qx "$FRONTEND_CONTAINER"
}

uvicorn_reload_args() {
  local -a args=(--reload --reload-dir app)
  if [[ -n "${INA_SRC:-}" && -d "${INA_SRC}/ina" ]]; then
    args+=(--reload-dir "$INA_SRC/ina")
  fi
  printf '%s\n' "${args[@]}"
}

run_backend_dev() {
  setup_env
  stop_daemon_backend
  mkdir -p "$LOG_DIR"

  mapfile -t reload_args < <(uvicorn_reload_args)

  cd "$ROOT/backend"
  if [[ "${DEV_FOREGROUND:-0}" == "1" ]]; then
    echo "Backend → http://0.0.0.0:${BACKEND_PORT}  (uvicorn --reload)"
    exec python3 -m uvicorn app.main:app \
      --host 0.0.0.0 --port "$BACKEND_PORT" \
      "${reload_args[@]}"
  fi

  : >"$BACKEND_LOG"
  nohup python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" \
    "${reload_args[@]}" >>"$BACKEND_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" >"$BACKEND_PID_FILE"
}

run_frontend_dev() {
  setup_env
  command -v docker >/dev/null 2>&1 || {
    echo "docker required for frontend dev (host has no Node/npm)" >&2
    exit 1
  }

  stop_dev_frontend

  if [[ "${DEV_FOREGROUND:-0}" == "1" ]]; then
    echo "Frontend → http://0.0.0.0:${FRONTEND_PORT}  (Vite HMR)"
    exec docker run --rm -it \
      --name "$FRONTEND_CONTAINER" \
      -p "${FRONTEND_PORT}:5180" \
      -v "${ROOT}/frontend:/app" \
      -w /app \
      --add-host=host.docker.internal:host-gateway \
      -e "VITE_API_PROXY=${API_PROXY}" \
      "$FRONTEND_IMAGE" \
      bash -lc 'npm install && npm run dev -- --host 0.0.0.0 --port 5180'
  fi

  docker run -d --rm \
    --name "$FRONTEND_CONTAINER" \
    -p "${FRONTEND_PORT}:5180" \
    -v "${ROOT}/frontend:/app" \
    -w /app \
    --add-host=host.docker.internal:host-gateway \
    -e "VITE_API_PROXY=${API_PROXY}" \
    "$FRONTEND_IMAGE" \
    bash -lc 'npm install && npm run dev -- --host 0.0.0.0 --port 5180' >/dev/null
}

print_started() {
  local target="$1"
  echo "Dev stack started in background (${target})."
  if [[ "$target" == "both" || "$target" == "backend" ]]; then
    echo "  API  http://127.0.0.1:${BACKEND_PORT}/docs"
    echo "  Backend log: ${BACKEND_LOG}"
    if [[ -f "$BACKEND_PID_FILE" ]]; then
      echo "  Backend PID: $(<"$BACKEND_PID_FILE")"
    fi
  fi
  if [[ "$target" == "both" || "$target" == "frontend" ]]; then
    echo "  UI   http://127.0.0.1:${FRONTEND_PORT}"
    echo "  Frontend: docker logs -f ${FRONTEND_CONTAINER}"
  fi
  echo "  Tail logs: $0 logs"
  echo "  Stop:      $0 down"
}

run_dev() {
  setup_env
  stop_dev
  stop_daemon_backend
  mkdir -p "$LOG_DIR"

  if [[ "${DEV_FOREGROUND:-0}" == "1" ]]; then
    mapfile -t reload_args < <(uvicorn_reload_args)
    echo "Starting INA-Infra dev stack (foreground) …"
    echo "  UI  http://127.0.0.1:${FRONTEND_PORT}"
    echo "  API http://127.0.0.1:${BACKEND_PORT}/docs"
    echo "Press Ctrl+C to stop."
    echo

    cd "$ROOT/backend"
    python3 -m uvicorn app.main:app \
      --host 0.0.0.0 --port "$BACKEND_PORT" \
      "${reload_args[@]}" 2>&1 | sed -u 's/^/[backend] /' &
    echo $! >"$BACKEND_PID_FILE"

    docker run --rm -it \
      --name "$FRONTEND_CONTAINER" \
      -p "${FRONTEND_PORT}:5180" \
      -v "${ROOT}/frontend:/app" \
      -w /app \
      --add-host=host.docker.internal:host-gateway \
      -e "VITE_API_PROXY=${API_PROXY}" \
      "$FRONTEND_IMAGE" \
      bash -lc 'npm install && npm run dev -- --host 0.0.0.0 --port 5180' \
      2>&1 | sed -u 's/^/[frontend] /' &

    cleanup() {
      echo
      echo "Shutting down dev stack …"
      stop_dev
    }
    trap cleanup EXIT INT TERM
    wait
    return
  fi

  run_backend_dev
  run_frontend_dev
  print_started both
}

cmd_up() {
  local target=both
  local foreground=0
  for arg in "$@"; do
    case "$arg" in
      -f|--foreground) foreground=1 ;;
      both|all) target=both ;;
      backend|api) target=backend ;;
      frontend|ui) target=frontend ;;
      -*) echo "Unknown option: $arg" >&2; exit 1 ;;
      *) target="$arg" ;;
    esac
  done

  if (( foreground )); then
    DEV_FOREGROUND=1
  fi

  case "$target" in
    both|all)
      if (( foreground )); then
        run_dev
      else
        setup_env
        stop_dev
        stop_daemon_backend
        mkdir -p "$LOG_DIR"
        run_backend_dev
        run_frontend_dev
        print_started both
      fi
      ;;
    backend|api)
      if (( foreground )); then
        DEV_FOREGROUND=1 run_backend_dev
      else
        setup_env
        stop_dev_backend
        stop_daemon_backend
        mkdir -p "$LOG_DIR"
        run_backend_dev
        print_started backend
      fi
      ;;
    frontend|ui)
      if (( foreground )); then
        DEV_FOREGROUND=1 run_frontend_dev
      else
        setup_env
        run_frontend_dev
        print_started frontend
      fi
      ;;
    *)
      echo "Unknown target: $target" >&2
      usage >&2
      exit 1
      ;;
  esac
}

cmd_down() {
  stop_dev
  echo "Dev stack stopped."
}

cmd_logs() {
  setup_env
  local pids=()
  if [[ -f "$BACKEND_LOG" ]]; then
    tail -F "$BACKEND_LOG" &
    pids+=($!)
  fi
  if frontend_running; then
    docker logs -f "$FRONTEND_CONTAINER" &
    pids+=($!)
  fi
  if ((${#pids[@]} == 0)); then
    echo "No dev logs (stack not running?). Start with: $0 up" >&2
    exit 1
  fi
  trap 'kill "${pids[@]}" 2>/dev/null' EXIT INT TERM
  wait
}

cmd_status() {
  setup_env
  echo "Backend:  $(backend_running && echo "running (PID $(<"$BACKEND_PID_FILE"))" || echo "stopped")"
  echo "Frontend: $(frontend_running && echo "running ($FRONTEND_CONTAINER)" || echo "stopped")"
  if [[ -f "$BACKEND_LOG" ]]; then
    echo "Backend log: $BACKEND_LOG"
  fi
}

main() {
  local cmd="${1:-up}"
  shift || true

  case "$cmd" in
    up|"")
      cmd_up "$@"
      ;;
    down|stop)
      cmd_down
      ;;
    logs|log)
      cmd_logs
      ;;
    status|ps)
      cmd_status
      ;;
    run)
      # Legacy: run dev → background; run backend/frontend → foreground
      local sub="${1:-dev}"
      case "$sub" in
        dev|"")
          cmd_up both
          ;;
        backend)
          DEV_FOREGROUND=1 run_backend_dev
          ;;
        frontend)
          DEV_FOREGROUND=1 run_frontend_dev
          ;;
        *)
          usage >&2
          exit 1
          ;;
      esac
      ;;
    -h|--help|help)
      usage
      ;;
    backend|frontend)
      cmd_up "$cmd" "$@"
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
