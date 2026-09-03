#!/usr/bin/env bash
# Build Background IoT (Slice D) images and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.8-amd64}"
MOSQ_TAG="${MOSQ_TAG:-nws-v0.1-amd64}"

cd "${SCRIPT_DIR}"

echo "==> Building iot-mosquitto:${MOSQ_TAG}"
docker build --platform linux/amd64 -f mosquitto/Dockerfile -t "iot-mosquitto:${MOSQ_TAG}" -t "${REGISTRY}/iot-mosquitto:${MOSQ_TAG}" mosquitto

echo "==> Building sliced-edge:${IMAGE_TAG}"
docker build --platform linux/amd64 -f backend/Dockerfile -t "sliced-edge:${IMAGE_TAG}" -t "${REGISTRY}/sliced-edge:${IMAGE_TAG}" .

echo "==> Pushing images to ${REGISTRY}"
docker push "${REGISTRY}/iot-mosquitto:${MOSQ_TAG}"
docker push "${REGISTRY}/sliced-edge:${IMAGE_TAG}"

echo "Done:"
echo "  - ${REGISTRY}/iot-mosquitto:${MOSQ_TAG}"
echo "  - ${REGISTRY}/sliced-edge:${IMAGE_TAG}"
