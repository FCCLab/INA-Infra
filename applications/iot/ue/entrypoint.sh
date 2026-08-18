#!/bin/bash
set -euo pipefail
ROLE="${CONSOLE_ROLE:-backend}"
if [ "$ROLE" = "frontend" ]; then
  exec uvicorn frontend:app --host 0.0.0.0 --port "${FRONTEND_PORT:-80}"
fi
exec uvicorn backend:app --host 0.0.0.0 --port "${BACKEND_PORT:-8090}"
