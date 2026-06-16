#!/usr/bin/env bash
# Print a Kubernetes Dashboard login token for admin-user.
set -euo pipefail

NAMESPACE="${DASHBOARD_NAMESPACE:-kubernetes-dashboard}"
SERVICE_ACCOUNT="${DASHBOARD_SA:-admin-user}"
DASHBOARD_URL="${DASHBOARD_URL:-https://10.1.132.65}"
TOKEN_DURATION="${TOKEN_DURATION:-24h}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Print a bearer token for the Kubernetes Dashboard.

Options:
  -u, --url URL           Dashboard URL to print (default: ${DASHBOARD_URL})
  -n, --namespace NAME    Dashboard namespace (default: ${NAMESPACE})
  -s, --service-account   Service account name (default: ${SERVICE_ACCOUNT})
  -d, --duration DURATION Token lifetime (default: ${TOKEN_DURATION})
  -h, --help              Show this help

Environment:
  DASHBOARD_URL
  DASHBOARD_NAMESPACE
  DASHBOARD_SA
  TOKEN_DURATION

Example:
  $(basename "$0")
  DASHBOARD_URL=https://10.1.132.65 $(basename "$0")
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--url)
      DASHBOARD_URL="$2"
      shift 2
      ;;
    -n|--namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    -s|--service-account)
      SERVICE_ACCOUNT="$2"
      shift 2
      ;;
    -d|--duration)
      TOKEN_DURATION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "Namespace not found: $NAMESPACE" >&2
  echo "Install the dashboard first (Helm), then apply utils/kubernetes-dashboard-rbac.yaml" >&2
  exit 1
fi

if ! kubectl -n "$NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
  echo "Service account not found: $SERVICE_ACCOUNT in $NAMESPACE" >&2
  echo "Apply: kubectl apply -f utils/kubernetes-dashboard-rbac.yaml" >&2
  exit 1
fi

echo "Dashboard URL: $DASHBOARD_URL"
echo "Service account: $SERVICE_ACCOUNT ($NAMESPACE)"
echo
echo "Token (paste into the dashboard login page):"
echo
kubectl -n "$NAMESPACE" create token "$SERVICE_ACCOUNT" --duration="$TOKEN_DURATION"
