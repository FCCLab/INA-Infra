#!/usr/bin/env bash
# Compatibility wrapper — prefer:
#   ./scripts/profile/profile_wait_smf_nrf.sh <profilename> [flags...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_SCRIPT="$SCRIPT_DIR/profile/profile_wait_smf_nrf.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
FORWARD=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ns)
      NS="${2:?}"
      shift 2
      ;;
    *)
      FORWARD+=("$1")
      shift
      ;;
  esac
done

exec "$PROFILE_SCRIPT" "$NS" "${FORWARD[@]+"${FORWARD[@]}"}"
