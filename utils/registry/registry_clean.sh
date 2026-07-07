#!/usr/bin/env bash
# Clean up the local secure registry by removing images with no tags and unreferenced blobs.
# Usage: ./utils/registry/registry_clean.sh

set -euo pipefail

echo "==> Removing repositories with no tags..."
kubectl exec deployment/registry -n registry -c registry -- sh -c '
if [ -d "/var/lib/registry/docker/registry/v2/repositories" ]; then
  find /var/lib/registry/docker/registry/v2/repositories -type d -name "tags" | while read -r tags_dir; do
    if [ -z "$(ls -A "$tags_dir")" ]; then
      repo_dir=$(dirname "$(dirname "$tags_dir")")
      echo "  Deleting empty repository directory: $repo_dir"
      rm -rf "$repo_dir"
    fi
  done
  find /var/lib/registry/docker/registry/v2/repositories -mindepth 1 -type d -empty -delete 2>/dev/null || true
fi
'

echo "==> Running registry garbage collection to reclaim space..."
kubectl exec deployment/registry -n registry -c registry -- registry garbage-collect /etc/docker/registry/config.yml --delete-untagged

echo "==> Registry clean up completed successfully!"
