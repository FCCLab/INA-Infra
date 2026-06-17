#!/usr/bin/env bash
# Print a Kubernetes Dashboard login token for admin-user.
set -euo pipefail

NAMESPACE="${DASHBOARD_NAMESPACE:-kubernetes-dashboard}"
SERVICE_ACCOUNT="${DASHBOARD_SA:-admin-user}"
DASHBOARD_URL="${DASHBOARD_URL:-https://10.1.132.41}"
TOKEN_DURATION="${TOKEN_DURATION:-24h}"
KCTX="${KCTX:-central@central}"

# central@central lives in ~/.kube/config-central; merge it even when the shell
# already sets KUBECONFIG to mgmt-only (~/.kube/config).
CENTRAL_KUBECONFIG="${CENTRAL_KUBECONFIG:-$HOME/.kube/config-central}"
DEFAULT_KUBECONFIG="${HOME}/.kube/config"
if [[ -f "$CENTRAL_KUBECONFIG" ]]; then
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG="${DEFAULT_KUBECONFIG}:${CENTRAL_KUBECONFIG}"
  elif [[ ":${KUBECONFIG}:" != *":${CENTRAL_KUBECONFIG}:"* ]]; then
    export KUBECONFIG="${KUBECONFIG}:${CENTRAL_KUBECONFIG}"
  fi
else
  export KUBECONFIG="${KUBECONFIG:-$DEFAULT_KUBECONFIG}"
fi

kubectl_ctx() {
  kubectl --context="$KCTX" "$@"
}

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
  KCTX                  kubectl context (default: central@central)
  KUBECONFIG            Shell kubeconfig; config-central is merged in automatically
  CENTRAL_KUBECONFIG    Path to central kubeconfig (default: ~/.kube/config-central)

Example:
  $(basename "$0")
  DASHBOARD_URL=https://10.1.132.41 $(basename "$0")
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

if ! kubectl config get-contexts "$KCTX" >/dev/null 2>&1; then
  echo "kubectl context not found: $KCTX" >&2
  if [[ ! -f "$CENTRAL_KUBECONFIG" ]]; then
    echo "Missing $CENTRAL_KUBECONFIG (run bringup-central-0.sh on the central node)." >&2
  else
    echo "Try: export KUBECONFIG=\$HOME/.kube/config:\$HOME/.kube/config-central" >&2
  fi
  exit 1
fi

if ! kubectl_ctx get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "Namespace not found: $NAMESPACE (context: $KCTX)" >&2
  echo "Push initial_central/ and wait for Config Sync, or helm install kubernetes-dashboard." >&2
  exit 1
fi

if ! kubectl_ctx -n "$NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
  echo "Service account not found: $SERVICE_ACCOUNT in $NAMESPACE" >&2
  echo "Apply cluster/clusterrolebinding-admin-user.yaml via GitOps push." >&2
  exit 1
fi

echo "Dashboard URL: $DASHBOARD_URL"
echo "Service account: $SERVICE_ACCOUNT ($NAMESPACE)"
echo
echo "Token (paste into the dashboard login page):"
echo
kubectl_ctx -n "$NAMESPACE" create token "$SERVICE_ACCOUNT" --duration="$TOKEN_DURATION"
