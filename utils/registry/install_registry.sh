#!/usr/bin/env bash
# Configure a remote node to trust the local Docker registry.
# Usage: ./utils/registry/install_registry.sh <node-name>
# Example: ./utils/registry/install_registry.sh usrp

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <node-name>

Configure the specified remote node to trust the local insecure registry.
Requires passwordless SSH to be configured for <node-name> in utils/ssh_config/config.
EOF
}

if [[ $# -ne 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 1
fi

NODE="$1"

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found at $SSH_CONFIG" >&2
  exit 1
fi

if ! grep -qE "^Host ${NODE}$" "$SSH_CONFIG"; then
  echo "error: no SSH config entry for Host ${NODE} in ${SSH_CONFIG}" >&2
  exit 1
fi

# Define temporary remote paths
REMOTE_DOCKER_SH="/tmp/setup-docker-insecure-registry.sh"
REMOTE_CONTAINERD_SH="/tmp/setup-containerd-insecure-registry.sh"

echo "==> Configuring registry trust on remote node: ${NODE}"

# Copy the setup scripts to the remote node
scp -F "$SSH_CONFIG" "$REPO_ROOT/scripts/setup-docker-insecure-registry.sh" "${NODE}:${REMOTE_DOCKER_SH}"
scp -F "$SSH_CONFIG" "$REPO_ROOT/scripts/setup-containerd-insecure-registry.sh" "${NODE}:${REMOTE_CONTAINERD_SH}"

# Execute the scripts remotely via sudo and clean up
ssh -F "$SSH_CONFIG" "${NODE}" "
  sudo chmod +x '${REMOTE_DOCKER_SH}' '${REMOTE_CONTAINERD_SH}'
  echo '>>> Running Docker registry setup...'
  sudo '${REMOTE_DOCKER_SH}'
  echo '>>> Running Containerd registry setup...'
  sudo '${REMOTE_CONTAINERD_SH}'
  rm -f '${REMOTE_DOCKER_SH}' '${REMOTE_CONTAINERD_SH}'
"

echo "==> Done configuring registry trust on ${NODE}."
