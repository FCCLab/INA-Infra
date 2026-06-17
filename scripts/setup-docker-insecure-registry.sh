#!/usr/bin/env bash
# One-time Docker setup so pushes work against the HTTP registry on mgmt.
#
#   sudo ./scripts/setup-docker-insecure-registry.sh
#   sudo ./scripts/setup-docker-insecure-registry.sh 10.1.132.30:5000
set -euo pipefail

REGISTRY="${1:-10.1.132.30:5000}"
DAEMON_JSON="/etc/docker/daemon.json"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0 [REGISTRY]" >&2
  exit 1
fi

if [[ ! -d /etc/docker ]]; then
  mkdir -p /etc/docker
fi

if [[ -f "$DAEMON_JSON" ]] && command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq --arg reg "$REGISTRY" '
    .["insecure-registries"] = (
      (.["insecure-registries"] // [])
      | if index($reg) then . else . + [$reg] end
      | unique
    )
  ' "$DAEMON_JSON" >"$tmp"
  mv "$tmp" "$DAEMON_JSON"
else
  if [[ -f "$DAEMON_JSON" ]]; then
    echo "Warning: $DAEMON_JSON exists but jq is not installed; overwriting with insecure-registries only." >&2
    echo "Merge manually if you have other Docker settings." >&2
  fi
  cat >"$DAEMON_JSON" <<EOF
{
  "insecure-registries": ["${REGISTRY}"]
}
EOF
fi

echo "Wrote $DAEMON_JSON:"
cat "$DAEMON_JSON"
echo
echo "Restarting docker ..."
systemctl restart docker
echo "Done. Verify:"
docker info 2>/dev/null | sed -n '/Insecure Registries:/,/Registry Mirrors:/p' | head -n -1 || true
