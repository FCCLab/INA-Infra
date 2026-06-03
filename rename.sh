#!/usr/bin/env bash
# Rename cluster and user entries in a kubeconfig (labels only; certs unchanged).
set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") <cluster-name> <user-name> [kubeconfig]

Rename the cluster and user in a kubeconfig. Defaults to the current context.
A timestamped backup is created next to the config file.

Examples:
  $(basename "$0") central central ~/.kube/config-central
  $(basename "$0") mgmt mgmt ~/.kube/config
  KUBECONFIG=/etc/kubernetes/admin.conf sudo -E $(basename "$0") central central
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage >&2
  exit 1
fi

NEW_CLUSTER="$1"
NEW_USER="$2"
KUBECONFIG="${3:-${KUBECONFIG:-$HOME/.kube/config}}"
export KUBECONFIG

if [[ ! -f "$KUBECONFIG" ]]; then
  echo "error: kubeconfig not found: $KUBECONFIG" >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "error: kubectl not found in PATH" >&2
  exit 1
fi

CURRENT_CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
if [[ -z "$CURRENT_CONTEXT" ]]; then
  echo "error: no current context in $KUBECONFIG" >&2
  exit 1
fi

OLD_CLUSTER="$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')"
OLD_USER="$(kubectl config view --minify -o jsonpath='{.contexts[0].context.user}')"
OLD_NAMESPACE="$(kubectl config view --minify -o jsonpath='{.contexts[0].context.namespace}' 2>/dev/null || true)"
SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
NEW_CONTEXT="${NEW_USER}@${NEW_CLUSTER}"

if [[ "$OLD_CLUSTER" == "$NEW_CLUSTER" && "$OLD_USER" == "$NEW_USER" && "$CURRENT_CONTEXT" == "$NEW_CONTEXT" ]]; then
  echo "Already set: context=$NEW_CONTEXT cluster=$NEW_CLUSTER user=$NEW_USER"
  exit 0
fi

BACKUP="${KUBECONFIG}.bak.$(date +%Y%m%d%H%M%S)"
cp "$KUBECONFIG" "$BACKUP"
echo "Backup: $BACKUP"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 -d >"$TMPDIR/ca.crt"

kubectl config view --raw --minify -o jsonpath='{.users[0].user.client-certificate-data}' \
  | base64 -d >"$TMPDIR/client.crt"

kubectl config view --raw --minify -o jsonpath='{.users[0].user.client-key-data}' \
  | base64 -d >"$TMPDIR/client.key"

kubectl config set-cluster "$NEW_CLUSTER" \
  --server="$SERVER" \
  --certificate-authority="$TMPDIR/ca.crt" \
  --embed-certs=true

kubectl config set-credentials "$NEW_USER" \
  --client-certificate="$TMPDIR/client.crt" \
  --client-key="$TMPDIR/client.key" \
  --embed-certs=true

if [[ -n "$OLD_NAMESPACE" ]]; then
  kubectl config set-context "$NEW_CONTEXT" \
    --cluster="$NEW_CLUSTER" \
    --user="$NEW_USER" \
    --namespace="$OLD_NAMESPACE"
else
  kubectl config set-context "$NEW_CONTEXT" \
    --cluster="$NEW_CLUSTER" \
    --user="$NEW_USER"
fi

kubectl config use-context "$NEW_CONTEXT"

if [[ "$CURRENT_CONTEXT" != "$NEW_CONTEXT" ]]; then
  kubectl config delete-context "$CURRENT_CONTEXT" 2>/dev/null || true
fi

if [[ "$OLD_CLUSTER" != "$NEW_CLUSTER" ]]; then
  kubectl config delete-cluster "$OLD_CLUSTER" 2>/dev/null || true
fi

if [[ "$OLD_USER" != "$NEW_USER" ]]; then
  kubectl config delete-user "$OLD_USER" 2>/dev/null || true
fi

echo "Context: $(kubectl config current-context)"
echo "Cluster: $(kubectl config view --minify -o jsonpath='{.clusters[0].name}')"
echo "User:    $(kubectl config view --minify -o jsonpath='{.contexts[0].context.user}')"

if kubectl cluster-info >/dev/null 2>&1; then
  echo "Verify:  kubectl cluster-info OK"
else
  echo "warning: kubectl cluster-info failed; check credentials in $KUBECONFIG" >&2
  exit 1
fi
