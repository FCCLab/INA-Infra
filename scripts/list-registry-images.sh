#!/usr/bin/env bash
# List images stored on the mgmt cluster Docker registry (Registry HTTP API v2).
#
#   ./scripts/list-registry-images.sh
#   ./scripts/list-registry-images.sh -r 10.1.132.30:5000
#   ./scripts/list-registry-images.sh --repo 5gc-open5gs-5gc
set -euo pipefail

REGISTRY="${REGISTRY:-10.1.132.30:5000}"
REPO_FILTER=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

List repositories and tags on the mgmt Docker registry.

Options:
  -r, --registry HOST:PORT   Registry address (default: ${REGISTRY})
  --repo NAME                List tags for one repository only
  -h, --help                 Show this help

Environment:
  REGISTRY                   Default registry (default: 10.1.132.30:5000)

Examples:
  $(basename "$0")
  $(basename "$0") --repo 5gc-open5gs-5gc
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -r|--registry)
      REGISTRY="${2:?missing value for $1}"
      shift 2
      ;;
    --repo)
      REPO_FILTER="${2:?missing value for $1}"
      shift 2
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "Unexpected argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

registry_host="${REGISTRY%%:*}"
registry_port="${REGISTRY#*:}"
if [[ "$registry_port" == "$registry_host" ]]; then
  registry_port="5000"
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found in PATH." >&2
  exit 1
fi

# Detect protocol and curl options
if curl -k -sf --connect-timeout 3 "https://${registry_host}:${registry_port}/v2/" >/dev/null; then
  base_url="https://${registry_host}:${registry_port}"
  CURL_OPTS=(-k)
else
  base_url="http://${registry_host}:${registry_port}"
  CURL_OPTS=()
fi

parse_json_array() {
  local key="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".${key}[]? // empty"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(d.get('${key}') or []))"
  else
    echo "Need jq or python3 to parse registry JSON." >&2
    exit 1
  fi
}

registry_get() {
  curl -sf "${CURL_OPTS[@]}" --connect-timeout 10 "${base_url}$1"
}

if ! registry_get /v2/ >/dev/null; then
  echo "Registry not reachable at ${base_url}/v2/" >&2
  exit 1
fi

list_repos() {
  registry_get "/v2/_catalog?n=1000" | parse_json_array repositories
}

list_tags() {
  local repo="$1"
  registry_get "/v2/${repo}/tags/list" | parse_json_array tags
}

idx=0
print_image() {
  idx=$((idx + 1))
  printf '%3d  %s\n' "$idx" "$1"
}

echo "Registry: ${base_url}"

if [[ -n "$REPO_FILTER" ]]; then
  tags="$(list_tags "$REPO_FILTER" || true)"
  if [[ -z "$tags" ]]; then
    echo "No tags found for repository: ${REPO_FILTER}" >&2
    exit 1
  fi
  while IFS= read -r tag; do
    [[ -n "$tag" ]] && print_image "${REGISTRY}/${REPO_FILTER}:${tag}"
  done <<<"$tags"
  exit 0
fi

repos="$(list_repos || true)"
if [[ -z "$repos" ]]; then
  echo "(empty — no repositories)"
  exit 0
fi

found=0
while IFS= read -r repo; do
  [[ -z "$repo" ]] && continue
  tags="$(list_tags "$repo" 2>/dev/null || true)"
  if [[ -z "$tags" ]]; then
    print_image "${REGISTRY}/${repo}"
    found=1
    continue
  fi
  while IFS= read -r tag; do
    [[ -z "$tag" ]] && continue
    print_image "${REGISTRY}/${repo}:${tag}"
    found=1
  done <<<"$tags"
done <<<"$repos"

if [[ "$found" -eq 0 ]]; then
  echo "(empty — no tagged images)"
fi
