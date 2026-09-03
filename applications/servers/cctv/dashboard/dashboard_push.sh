#!/usr/bin/env bash
# Push CCTV Grafana Dashboard to Grafana API
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_FILE="${1:-${SCRIPT_DIR}/grafana-dashboard.json}"

GRAFANA_URL="${GRAFANA_URL:-http://10.1.137.105:3000}"
GRAFANA_USER="${GRAFANA_USER:-inainfra}"
GRAFANA_PASS="${GRAFANA_PASS:-inainfra}"

if [ ! -f "${DASHBOARD_FILE}" ]; then
  echo "Error: Dashboard file '${DASHBOARD_FILE}' not found." >&2
  exit 1
fi

echo "==> Pushing ${DASHBOARD_FILE} to Grafana (${GRAFANA_URL}) as user '${GRAFANA_USER}'..."

python3 - <<EOF
import json
import urllib.request
import base64
import sys

dashboard_file = "${DASHBOARD_FILE}"
grafana_url = "${GRAFANA_URL}".rstrip("/")
username = "${GRAFANA_USER}"
password = "${GRAFANA_PASS}"

try:
    with open(dashboard_file, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"Error reading JSON file {dashboard_file}: {e}", file=sys.stderr)
    sys.exit(1)

# If the file already contains {"dashboard": ...}, extract it
dash = data.get("dashboard", data)

payload = json.dumps({
    "dashboard": dash,
    "overwrite": True,
    "message": "Pushed via dashboard_push.sh"
}).encode("utf-8")

auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
req = urllib.request.Request(
    f"{grafana_url}/api/dashboards/db",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth}"
    }
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print(f"==> Successfully pushed dashboard!")
        print(f"    UID:     {res.get('uid')}")
        print(f"    Slug:    {res.get('slug')}")
        print(f"    Version: {res.get('version')}")
        print(f"    URL:     {grafana_url}{res.get('url')}")
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    print(f"HTTP Error {e.code} pushing dashboard: {err_body}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Error pushing dashboard: {e}", file=sys.stderr)
    sys.exit(1)
EOF
