#!/usr/bin/env bash
# Push GitOps cluster repos to local Gitea (Config Sync) + GitHub origin (primary).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/ina-infra/backend/scripts/push_gitea_gitops.sh" "$@"
