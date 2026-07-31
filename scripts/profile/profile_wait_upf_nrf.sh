#!/usr/bin/env bash
# Compatibility wrapper — canonical script lives under ina-infra.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/ina-infra/backend/scripts/profile_wait_upf_nrf.sh" "$@"
