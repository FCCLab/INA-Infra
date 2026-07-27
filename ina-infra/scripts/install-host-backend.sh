#!/usr/bin/env bash
# Install/start host systemd unit for INA-Infra API (required for Gurobi academic).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UNIT_SRC="$REPO_ROOT/ina-infra/deploy/ina-infra-backend.service"
UNIT_DST=/etc/systemd/system/ina-infra-backend.service

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-running with sudo..."
  exec sudo "$0" "$@"
fi

# Ensure Python deps for the service user
sudo -u fcp bash -lc "
  pip3 install --user -q -r '$REPO_ROOT/ina-infra/backend/requirements.txt'
"

install -m 644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
systemctl enable --now ina-infra-backend.service
systemctl --no-pager --full status ina-infra-backend.service || true
echo
echo "API: http://127.0.0.1:8082/docs"
echo "Logs: journalctl -u ina-infra-backend -f"
