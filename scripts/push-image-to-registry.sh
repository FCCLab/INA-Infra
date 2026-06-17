#!/usr/bin/env bash
# Tag a local Docker image and push it to the mgmt cluster registry.
#
#   ./scripts/push-image-to-registry.sh 5gc-open5gs-5gc:latest
#   ./scripts/push-image-to-registry.sh 5gc-open5gs-5gc:latest --name open5gs/5gc --tag v1
#   REGISTRY=10.1.132.30:5000 ./scripts/push-image-to-registry.sh myimage:debug
#
# HTTP registry requires Docker insecure-registries (once per node):
#   sudo tee /etc/docker/daemon.json <<'EOF'
#   { "insecure-registries": ["10.1.132.30:5000"] }
#   EOF
#   sudo systemctl restart docker
set -euo pipefail

REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TARGET_NAME=""
TARGET_TAG=""

usage() {
  cat <<EOF
Usage: $(basename "$0") IMAGE [options]

Tag IMAGE and push it to the mgmt Docker registry.

Arguments:
  IMAGE           Local image reference (e.g. 5gc-open5gs-5gc:latest)

Options:
  -r, --registry HOST:PORT   Registry address (default: ${REGISTRY})
  -n, --name REPO            Repository path on the registry (default: image name without tag)
  -t, --tag TAG              Tag on the registry (default: tag from IMAGE, or latest)
  --tag-only                 Tag locally but do not push
  -h, --help                 Show this help

Environment:
  REGISTRY                   Default registry (default: 10.1.132.30:5000)

Examples:
  $(basename "$0") 5gc-open5gs-5gc:latest
  $(basename "$0") 5gc-open5gs-5gc:latest -n open5gs/5gc -t v1.0
EOF
}

TAG_ONLY=false

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
    -n|--name)
      TARGET_NAME="${2:?missing value for $1}"
      shift 2
      ;;
    -t|--tag)
      TARGET_TAG="${2:?missing value for $1}"
      shift 2
      ;;
    --tag-only)
      TAG_ONLY=true
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "${LOCAL_IMAGE:-}" ]]; then
        echo "Unexpected extra argument: $1" >&2
        usage >&2
        exit 1
      fi
      LOCAL_IMAGE="$1"
      shift
      ;;
  esac
done

if [[ -z "${LOCAL_IMAGE:-}" ]]; then
  echo "IMAGE is required." >&2
  usage >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH." >&2
  exit 1
fi

# Strip optional registry host from IMAGE (user:5000/foo:bar -> foo:bar).
image_ref="${LOCAL_IMAGE#*/}"
if [[ "$image_ref" == "$LOCAL_IMAGE" ]]; then
  image_ref="$LOCAL_IMAGE"
fi

if [[ "$image_ref" == *:* ]]; then
  default_name="${image_ref%:*}"
  default_tag="${image_ref##*:}"
else
  default_name="$image_ref"
  default_tag="latest"
fi

name="${TARGET_NAME:-$default_name}"
tag="${TARGET_TAG:-$default_tag}"
remote="${REGISTRY}/${name}:${tag}"

if ! docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
  echo "Local image not found: $LOCAL_IMAGE" >&2
  exit 1
fi

registry_host="${REGISTRY%%:*}"
registry_port="${REGISTRY#*:}"
if [[ "$registry_port" == "$registry_host" ]]; then
  registry_port="5000"
fi

echo "Registry:  http://${registry_host}:${registry_port}"
echo "Local:     ${LOCAL_IMAGE}"
echo "Remote:    ${remote}"

if ! curl -sf --connect-timeout 5 "http://${registry_host}:${registry_port}/v2/" >/dev/null; then
  echo "Registry not reachable at http://${registry_host}:${registry_port}/v2/" >&2
  exit 1
fi

docker_insecure_configured() {
  docker info 2>/dev/null | awk -v reg="$REGISTRY" '
    /^ Insecure Registries:/ { insec=1; next }
    insec && /^ / { gsub(/^ +/, ""); if ($0 == reg) found=1 }
    insec && /^[^ ]/ { insec=0 }
    END { exit(found ? 0 : 1) }
  '
}

if ! docker_insecure_configured; then
  cat >&2 <<EOF
Docker is not configured for HTTP registry ${REGISTRY}.

Run once on this node:

  sudo $(dirname "$(readlink -f "$0")")/setup-docker-insecure-registry.sh ${REGISTRY}

Or manually:

  sudo tee /etc/docker/daemon.json <<'JSON'
  { "insecure-registries": ["${REGISTRY}"] }
  JSON
  sudo systemctl restart docker

Then re-run: $(basename "$0") ${LOCAL_IMAGE}${TARGET_NAME:+ -n ${TARGET_NAME}}${TARGET_TAG:+ -t ${TARGET_TAG}}
EOF
  exit 1
fi

echo "Tagging ..."
docker tag "$LOCAL_IMAGE" "$remote"

if [[ "$TAG_ONLY" == true ]]; then
  echo "Tagged (not pushed): $remote"
  exit 0
fi

echo "Pushing ..."
if ! docker push "$remote"; then
  cat >&2 <<EOF

Push failed. For an HTTP registry, configure Docker on this node:

  sudo tee /etc/docker/daemon.json <<'JSON'
  { "insecure-registries": ["${REGISTRY}"] }
  JSON
  sudo systemctl restart docker

Then re-run: $(basename "$0") ${LOCAL_IMAGE}${TARGET_NAME:+ -n ${TARGET_NAME}}${TARGET_TAG:+ -t ${TARGET_TAG}}
EOF
  exit 1
fi

echo "Pushed: $remote"
echo "Pull:   docker pull $remote"
