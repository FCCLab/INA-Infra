#!/usr/bin/env bash
# Compatibility wrapper — canonical script lives under ina-infra.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/ina-infra/backend/scripts/wait_nrf_http.sh" "$@"
