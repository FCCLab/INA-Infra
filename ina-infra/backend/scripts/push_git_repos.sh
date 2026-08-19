#!/usr/bin/env bash
# Compatibility wrapper — canonical script is push_gitea_gitops.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/push_gitea_gitops.sh" "$@"
