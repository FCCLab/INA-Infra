#!/usr/bin/env bash
# Deprecated native install — use Docker + GitOps instead:
#   ./services/glass-dhcp/build_push.sh
#   ./scripts/render_glass_dhcp_gitops.sh central
#   ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Glass DHCP' central
set -euo pipefail
echo "Native bringup is retired. Use:" >&2
echo "  ./services/glass-dhcp/build_push.sh" >&2
echo "  ./scripts/render_glass_dhcp_gitops.sh central" >&2
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Glass DHCP' central" >&2
exit 1
