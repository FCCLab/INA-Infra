#!/usr/bin/env bash
# Compatibility wrapper — prefer:
#   ./scripts/profile/profile_patch_mysql.sh <profilename> [--slices N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_SCRIPT="$SCRIPT_DIR/profile/profile_patch_mysql.sh"

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

# Positional profile name still supported: patch_ina_mysql_live.sh test
if [[ ${#FORWARD[@]} -gt 0 && "${FORWARD[0]}" != -* ]]; then
  NS="${FORWARD[0]}"
  FORWARD=("${FORWARD[@]:1}")
fi

exec "$PROFILE_SCRIPT" "$NS" "${FORWARD[@]+"${FORWARD[@]}"}"
