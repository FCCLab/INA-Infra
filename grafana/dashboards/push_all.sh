#!/usr/bin/env bash
# Push all INA-Infra application Grafana dashboards via the Grafana API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PUSH="${REPO_ROOT}/applications/cctv/dashboard/dashboard_push.sh"

python3 "${SCRIPT_DIR}/generate_app_dashboards.py"

files=(
  "${REPO_ROOT}/applications/cctv/dashboard/grafana-dashboard.json"
  "${SCRIPT_DIR}/cctv-metrics.json"
  "${SCRIPT_DIR}/physical-ai-metrics.json"
  "${SCRIPT_DIR}/ott-dashboard.json"
  "${SCRIPT_DIR}/ott-metrics.json"
  "${SCRIPT_DIR}/iot-dashboard.json"
  "${SCRIPT_DIR}/iot-metrics.json"
)

for f in "${files[@]}"; do
  "$PUSH" "$f"
done
