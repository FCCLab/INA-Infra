#!/usr/bin/env bash
# Verify that a registry image tag has a resolvable manifest and non-empty blobs.
# Catches the failure mode where manifests/links exist but blob data is missing
# (docker push then reports "Layer already exists" and skips re-upload).
#
#   ./utils/registry/verify_registry_image.sh oai-nr-ue:nws-v0.2
#   REGISTRY=10.1.132.30:5000 ./utils/registry/verify_registry_image.sh oai-gnb nws-v0.2
set -euo pipefail

REGISTRY="${REGISTRY:-10.1.132.30:5000}"

usage() {
  cat <<EOF
Usage: $(basename "$0") IMAGE[:TAG] | IMAGE TAG

Verify manifest + blob payloads for an image in the local registry.
Exits 0 if all blobs download with the expected size; non-zero otherwise.

Environment:
  REGISTRY   host:port (default: ${REGISTRY})
EOF
}

if [[ $# -lt 1 || $# -gt 2 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 1
fi

if [[ $# -eq 2 ]]; then
  IMAGE="$1"
  TAG="$2"
elif [[ "$1" == *:* ]]; then
  IMAGE="${1%:*}"
  TAG="${1##*:}"
else
  IMAGE="$1"
  TAG="latest"
fi

registry_host="${REGISTRY%%:*}"
registry_port="${REGISTRY#*:}"
if [[ "$registry_port" == "$registry_host" ]]; then
  registry_port="5000"
fi

if curl -k -sf --connect-timeout 3 "https://${registry_host}:${registry_port}/v2/" >/dev/null; then
  REGISTRY_URL="https://${registry_host}:${registry_port}"
  CURL_OPTS=(-k -sS --http1.1)
else
  REGISTRY_URL="http://${registry_host}:${registry_port}"
  CURL_OPTS=(-sS)
fi

ACCEPT_ALL="application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
ACCEPT_MANIFEST="application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"

fetch_manifest() {
  local ref="$1"
  local accept="$2"
  curl "${CURL_OPTS[@]}" \
    -H "Accept: ${accept}" \
    "${REGISTRY_URL}/v2/${IMAGE}/manifests/${ref}"
}

verify_blob() {
  local digest="$1"
  local expect="$2"
  local tmp got
  tmp="$(mktemp)"
  got="$(curl "${CURL_OPTS[@]}" -o "$tmp" -w '%{size_download}' \
    "${REGISTRY_URL}/v2/${IMAGE}/blobs/${digest}" || true)"
  rm -f "$tmp"
  if [[ "$got" != "$expect" ]]; then
    echo "  FAIL blob ${digest}: got ${got:-0} bytes, expected ${expect}" >&2
    return 1
  fi
  echo "  OK   blob ${digest} (${expect} bytes)"
}

# Increments global FAILURES on problems.
verify_image_manifest_json() {
  local label="$1"
  local body="$2"

  if ! echo "$body" | jq -e '.config.digest and (.layers|type=="array")' >/dev/null 2>&1; then
    echo "error: unexpected manifest payload (${label})" >&2
    echo "$body" | head -c 400 >&2
    echo >&2
    FAILURES=$((FAILURES + 1))
    return 0
  fi

  echo "==> ${label}"
  while IFS=$'\t' read -r digest size; do
    if ! verify_blob "$digest" "$size"; then
      FAILURES=$((FAILURES + 1))
    fi
  done < <(echo "$body" | jq -r '
    ([.config] + .layers)
    | .[]
    | [.digest, (.size|tostring)]
    | @tsv
  ')
}

FAILURES=0
echo "==> Verifying ${IMAGE}:${TAG} at ${REGISTRY_URL}"

top="$(fetch_manifest "$TAG" "$ACCEPT_ALL")"
media_type="$(echo "$top" | jq -r '.mediaType // empty')"

if [[ "$media_type" == "application/vnd.oci.image.index.v1+json" \
   || "$media_type" == "application/vnd.docker.distribution.manifest.list.v2+json" ]]; then
  while IFS= read -r digest; do
    [[ -z "$digest" ]] && continue
    child="$(fetch_manifest "$digest" "$ACCEPT_MANIFEST")"
    verify_image_manifest_json "manifest ${digest}" "$child"
  done < <(echo "$top" | jq -r '
    .manifests[]
    | select(.platform.architecture != "unknown" and .platform.os != "unknown")
    | .digest
  ')
else
  verify_image_manifest_json "manifest ${IMAGE}:${TAG}" "$top"
fi

if [[ "$FAILURES" -gt 0 ]]; then
  cat >&2 <<EOF
error: ${FAILURES} blob check(s) failed for ${IMAGE}:${TAG}

Registry metadata can exist while blob payloads are empty. A plain
docker push may then skip layers ("Layer already exists"). Re-upload
blobs from a host that still has the image, or delete the corrupt
blobs and push again.
EOF
  exit 1
fi

echo "==> OK ${IMAGE}:${TAG}"
