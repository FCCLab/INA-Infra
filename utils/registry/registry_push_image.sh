#!/usr/bin/env bash
# Push an image from a remote node server to the local registry.
# Usage: ./utils/registry/registry_push_image.sh <node-name> <source_image:tag> [<target_image:tag>]
# Example: ./utils/registry/registry_push_image.sh usrp open5gs-5gc:latest open5gs/5gc:v1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <node-name> <source_image:tag> [<target_image:tag>]

Tag an image on a remote node and push it to the local secure registry.
If <target_image:tag> is omitted, it defaults to the same name and tag as the source image.
Requires passwordless SSH to be configured for <node-name> in utils/ssh_config/config.
EOF
}

if [[ $# -lt 2 || $# -gt 3 || "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 1
fi

NODE="$1"
SRC_IMAGE="$2"
TARGET_REF="${3:-$SRC_IMAGE}"

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found at $SSH_CONFIG" >&2
  exit 1
fi

if ! grep -qE "^Host ${NODE}$" "$SSH_CONFIG"; then
  echo "error: no SSH config entry for Host ${NODE} in ${SSH_CONFIG}" >&2
  exit 1
fi

# Parse target image reference
if [[ "$TARGET_REF" == *:* ]]; then
  TARGET_NAME="${TARGET_REF%:*}"
  TARGET_TAG="${TARGET_REF##*:}"
else
  TARGET_NAME="$TARGET_REF"
  TARGET_TAG=""
fi

REMOTE_PUSH_SH="/tmp/push-image-to-registry.sh"

echo "==> Copying push script to ${NODE}..."
scp -F "$SSH_CONFIG" "$REPO_ROOT/scripts/push-image-to-registry.sh" "${NODE}:${REMOTE_PUSH_SH}"

echo "==> Running push image on ${NODE}..."
ssh -F "$SSH_CONFIG" "${NODE}" "
  sudo chmod +x '${REMOTE_PUSH_SH}'
  sudo '${REMOTE_PUSH_SH}' '${SRC_IMAGE}' -n '${TARGET_NAME}' ${TARGET_TAG:+-t '${TARGET_TAG}'}
  rm -f '${REMOTE_PUSH_SH}'
"

echo "==> Done pushing image from ${NODE}."
