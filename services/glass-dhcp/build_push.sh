#!/usr/bin/env bash
# Clone (via Docker build), build Glass + mirror dhcpd, push to lab registry.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
TAG="${GLASS_DHCP_TAG:-latest}"
GLASS_IMAGE_LOCAL="glass-isc-dhcp:${TAG}"
DHCPD_IMAGE_SRC="${DHCPD_IMAGE_SRC:-networkboot/dhcpd:latest}"
GLASS_REPO="${GLASS_REPO:-https://github.com/Akkadius/glass-isc-dhcp.git}"
GLASS_REF="${GLASS_REF:-master}"

echo "==> Building Glass UI image (${GLASS_IMAGE_LOCAL})"
docker build \
  --build-arg "GLASS_REPO=${GLASS_REPO}" \
  --build-arg "GLASS_REF=${GLASS_REF}" \
  -t "${GLASS_IMAGE_LOCAL}" \
  -f "${ROOT}/Dockerfile" \
  "${ROOT}"

echo "==> Pushing glass-isc-dhcp -> ${REGISTRY}/glass-isc-dhcp:${TAG}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "${GLASS_IMAGE_LOCAL}" \
  -n glass-isc-dhcp -t "${TAG}"

echo "==> Pulling + pushing ${DHCPD_IMAGE_SRC} -> ${REGISTRY}/networkboot/dhcpd:${TAG}"
docker pull "${DHCPD_IMAGE_SRC}"
docker tag "${DHCPD_IMAGE_SRC}" "networkboot/dhcpd:${TAG}"
"${REPO_ROOT}/scripts/push-image-to-registry.sh" "networkboot/dhcpd:${TAG}" \
  -n networkboot/dhcpd -t "${TAG}"

echo "==> Done. Images:"
echo "    ${REGISTRY}/glass-isc-dhcp:${TAG}"
echo "    ${REGISTRY}/networkboot/dhcpd:${TAG}"
echo "Next:"
echo "  ./scripts/render_glass_dhcp_gitops.sh central"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Glass DHCP' central"
