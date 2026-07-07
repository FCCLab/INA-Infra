#!/usr/bin/env bash
# Push OAI latest images to local registry with replaced version tag, for ARM and AMD.
# Creates multi-architecture manifest lists when multiple architectures are provided.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"

# Detect protocol and curl options for the registry HTTP API
registry_host="${REGISTRY%%:*}"
registry_port="${REGISTRY#*:}"
if [[ "$registry_port" == "$registry_host" ]]; then
  registry_port="5000"
fi
if curl -k -sf --connect-timeout 3 "https://${registry_host}:${registry_port}/v2/" >/dev/null; then
  REGISTRY_URL="https://${registry_host}:${registry_port}"
  CURL_OPTS=(-k -s)
else
  REGISTRY_URL="http://${registry_host}:${registry_port}"
  CURL_OPTS=(-s)
fi

usage() {
  cat <<EOF
Usage: $(basename "$0") --arch <arch> <host> [--arch <arch2> <host2> ...] --version <version>

Options:
  --arch <arch> <host>   Specify architecture (e.g. arm64, amd64) and remote host node name
  --version <version>    The version tag to push to the registry (e.g. nws-v0.2)
  -h, --help             Show this help
EOF
}

# Parse arguments
ARCH_NAMES=()
ARCH_HOSTS=()
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      if [[ $# -lt 3 ]]; then
        echo "error: --arch requires both architecture and hostname" >&2
        exit 1
      fi
      ARCH_NAMES+=("$2")
      ARCH_HOSTS+=("$3")
      shift 3
      ;;
    --version)
      if [[ $# -lt 2 ]]; then
        echo "error: --version requires a value" >&2
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# Validation
if [[ ${#ARCH_NAMES[@]} -eq 0 ]]; then
  echo "error: at least one --arch <arch> <host> is required" >&2
  exit 1
fi

if [[ -z "$VERSION" ]]; then
  echo "error: --version is required" >&2
  exit 1
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found at $SSH_CONFIG" >&2
  exit 1
fi

# List of OAI images to tag/push
IMAGES=(
  "oai-gnb"
  "oai-cucp"
  "oai-nr-cuup"
  "oai-du"
  "oai-nr-ue"
  "oai-flexric"
)

# Push individual architecture tags
for i in "${!ARCH_NAMES[@]}"; do
  arch="${ARCH_NAMES[$i]}"
  host="${ARCH_HOSTS[$i]}"
  echo "==> Processing architecture $arch on host $host..."

  for image in "${IMAGES[@]}"; do
    # Check if host is configured
    if ! grep -qE "^Host ${host}$" "$SSH_CONFIG"; then
      echo "error: no SSH config entry for Host ${host} in ${SSH_CONFIG}" >&2
      exit 1
    fi

    # Detect the source image tag on the host
    SRC_IMAGE=""
    if ssh -F "$SSH_CONFIG" "$host" "sudo docker image inspect ${image}:latest-${arch} >/dev/null 2>&1"; then
      SRC_IMAGE="${image}:latest-${arch}"
    elif ssh -F "$SSH_CONFIG" "$host" "sudo docker image inspect ${image}:latest >/dev/null 2>&1"; then
      SRC_IMAGE="${image}:latest"
    fi

    if [[ -z "$SRC_IMAGE" ]]; then
      echo "error: could not find image ${image} (tried latest-${arch} and latest) on ${host}" >&2
      exit 1
    fi

    echo "==> Pushing ${SRC_IMAGE} from ${host} to registry as ${image}:${VERSION}-${arch}..."
    "$REPO_ROOT/utils/registry/registry_push_image.sh" "$host" "$SRC_IMAGE" "${image}:${VERSION}-${arch}"

    # If only one architecture is passed, push the main version tag directly as well
    if [[ ${#ARCH_NAMES[@]} -eq 1 ]]; then
      echo "==> Pushing ${SRC_IMAGE} from ${host} to registry as ${image}:${VERSION}..."
      "$REPO_ROOT/utils/registry/registry_push_image.sh" "$host" "$SRC_IMAGE" "${image}:${VERSION}"
    fi
  done
done

# Function to build and push the OCI Index manifest list directly using curl
create_multi_arch_manifest() {
  local image="$1"
  local tag="$2"

  if [[ ${#ARCH_NAMES[@]} -le 1 ]]; then
    return 0
  fi

  echo "==> Creating multi-architecture manifest list for ${image}:${tag}..."

  local manifests_json=""
  
  for i in "${!ARCH_NAMES[@]}"; do
    local arch="${ARCH_NAMES[$i]}"
    local arch_tag="${tag}-${arch}"

    # Fetch manifest from registry to get headers and check for OCI index wrapper
    local headers_file
    headers_file=$(mktemp)
    
    local body
    body=$(curl "${CURL_OPTS[@]}" -D "$headers_file" \
      -H "Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json" \
      "${REGISTRY_URL}/v2/${image}/manifests/${arch_tag}")

    local digest
    digest=$(grep -i "docker-content-digest" "$headers_file" | awk '{print $2}' | tr -d '\r\n')
    
    if [[ -z "$digest" ]]; then
      digest=$(echo -n "$body" | sha256sum | awk '{print "sha256:"$1}')
    fi

    local media_type
    media_type=$(grep -i "content-type" "$headers_file" | awk '{print $2}' | cut -d';' -f1 | tr -d '\r\n')
    if [[ -z "$media_type" || "$media_type" == "text/plain" ]]; then
      media_type=$(echo "$body" | jq -r '.mediaType // empty')
    fi
    if [[ -z "$media_type" ]]; then
      media_type="application/vnd.docker.distribution.manifest.v2+json"
    fi

    local size
    size=$(echo -n "$body" | wc -c)
    rm -f "$headers_file"

    # If the fetched image is an index/manifest list, resolve it to the actual child manifest for that arch
    if [[ "$media_type" == "application/vnd.oci.image.index.v1+json" || "$media_type" == "application/vnd.docker.distribution.manifest.list.v2+json" ]]; then
      local real_digest
      real_digest=$(echo "$body" | jq -r --arg arch "$arch" '.manifests[] | select(.platform.architecture == $arch) | .digest')
      if [[ -z "$real_digest" ]]; then
        real_digest=$(echo "$body" | jq -r '.manifests[] | select(.platform.architecture != "unknown") | .digest' | head -n 1)
      fi

      if [[ -n "$real_digest" ]]; then
        echo "==> Resolving OCI index wrapper for ${arch}: using manifest digest ${real_digest}"
        digest="$real_digest"

        # Fetch child manifest payload to get its actual size and media type
        body=$(curl "${CURL_OPTS[@]}" \
          -H "Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
          "${REGISTRY_URL}/v2/${image}/manifests/${digest}")
        
        size=$(echo -n "$body" | wc -c)
        media_type=$(echo "$body" | jq -r '.mediaType // empty')
        if [[ -z "$media_type" ]]; then
          media_type="application/vnd.oci.image.manifest.v1+json"
        fi
      fi
    fi

    # Append entry to OCI Index
    local manifest_entry
    manifest_entry=$(cat <<EOF
    {
      "mediaType": "${media_type}",
      "size": ${size},
      "digest": "${digest}",
      "platform": {
        "architecture": "${arch}",
        "os": "linux"
      }
    }
EOF
)
    if [[ -n "$manifests_json" ]]; then
      manifests_json="${manifests_json},${manifest_entry}"
    else
      manifests_json="${manifests_json}${manifest_entry}"
    fi
  done

  # Build the final OCI Index JSON payload
  local oci_index_json
  oci_index_json=$(cat <<EOF
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.index.v1+json",
  "manifests": [
    ${manifests_json}
  ]
}
EOF
)

  # PUT OCI Index to the registry under the common tag
  echo "==> Pushing OCI Index to registry: ${image}:${tag}"
  local put_res
  put_res=$(curl "${CURL_OPTS[@]}" -v -X PUT \
    -H "Content-Type: application/vnd.oci.image.index.v1+json" \
    -d "$oci_index_json" \
    "${REGISTRY_URL}/v2/${image}/manifests/${tag}" 2>&1)

  if echo "$put_res" | grep -q "HTTP/.* 20"; then
    echo "==> Successfully created tag ${image}:${tag}"
  else
    echo "error: failed to create tag ${image}:${tag}" >&2
    echo "$put_res" >&2
    return 1
  fi
}

# Process multi-arch manifests
if [[ ${#ARCH_NAMES[@]} -gt 1 ]]; then
  for image in "${IMAGES[@]}"; do
    create_multi_arch_manifest "$image" "$VERSION"
  done
fi

# Print formatted summary table
echo ""
echo "Successfully created tags:"
echo "----------------------------------------------------------------------------------------"
printf " %-22s | %-24s | %-32s\n" "Image Name" "Alias / Target" "Architecture Tag"
echo "----------------------------------------------------------------------------------------"

for image in "${IMAGES[@]}"; do
  # Determine alias target
  alias_target="-"
  if [[ "$image" == "oai-cucp" || "$image" == "oai-du" ]]; then
    alias_target="oai-gnb:${VERSION}"
  fi

  # Print first arch line with image name and alias
  arch_0="${ARCH_NAMES[0]}"
  printf " %-22s | %-24s | %-32s\n" "${image}:${VERSION}" "${alias_target}" "${image}:${VERSION}-${arch_0}"

  # Print subsequent archs if any
  for ((idx=1; idx<${#ARCH_NAMES[@]}; idx++)); do
    arch_next="${ARCH_NAMES[$idx]}"
    printf " %-22s | %-24s | %-32s\n" "" "" "${image}:${VERSION}-${arch_next}"
  done
done

echo "----------------------------------------------------------------------------------------"
echo ""
echo " Done!"
