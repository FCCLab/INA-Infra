#!/usr/bin/env bash
# Build linux/amd64 images for every Exp4 slice app.
# Usage:
#   ./applications/exp4/build_images.sh
#   ./applications/exp4/build_images.sh --push
#   ./applications/exp4/build_images.sh s1 s4
#   IMAGE_TAG=nws-v0.14-amd64 ./applications/exp4/build_images.sh --push
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.14-amd64}"
PLATFORM="${PLATFORM:-linux/amd64}"
PUSH=0
export DOCKER_BUILDKIT=1

SLICES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) PUSH=1; shift ;;
    --no-push) PUSH=0; shift ;;
    s1|s2|s3|s4|s5) SLICES+=("$1"); shift ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done
if [[ ${#SLICES[@]} -eq 0 ]]; then
  SLICES=(s1 s2 s3 s4 s5)
fi

build_one() {
  local name="$1" context="$2" dockerfile="$3"
  local local_tag="${name}:${IMAGE_TAG}"
  local remote_tag="${REGISTRY}/${name}:${IMAGE_TAG}"
  echo "==> ${local_tag}  (${PLATFORM})"
  docker build --platform "${PLATFORM}" \
    -f "${dockerfile}" \
    -t "${local_tag}" \
    -t "${remote_tag}" \
    "${context}"
  if [[ "${PUSH}" == "1" ]]; then
    echo "==> push ${remote_tag}"
    docker push "${remote_tag}"
  fi
  BUILT+=("${remote_tag}")
}

ensure_insecure_registry() {
  # Lab registry is HTTPS + self-signed. Docker must list it as insecure
  # or push uses https:// and fails x509 unknown authority.
  if docker info 2>/dev/null | grep -qF "${REGISTRY}"; then
    return 0
  fi
  echo "ERROR: Docker is not configured for insecure registry ${REGISTRY}." >&2
  echo "The registry presents a self-signed cert; push will fail without this." >&2
  echo "  sudo ${HERE}/../../scripts/setup-docker-insecure-registry.sh ${REGISTRY}" >&2
  echo "Then re-run:  IMAGE_TAG=${IMAGE_TAG} $0 --push ${SLICES[*]}" >&2
  exit 1
}

BUILT=()
echo "Exp4 images  tag=${IMAGE_TAG}  registry=${REGISTRY}  push=${PUSH}"
echo "slices: ${SLICES[*]}"
echo
if [[ "${PUSH}" == "1" ]]; then
  ensure_insecure_registry
fi

stage_s2_trt_engine() {
  # TensorRT engines are GPU/TRT-version specific (A40 + DeepStream 7.1).
  # They cannot be compiled in a CPU docker build; harvest a file already
  # built on gpu-a40 and COPY it into the image.
  local dest="${HERE}/exp4_s2_cctv/server/ds_engines"
  local name="model_b1_gpu0_fp16.engine"
  local ssh_cfg="${HERE}/../../utils/ssh_config/config"
  local host="${EXP4_S2_ENGINE_HOST:-gpu-a40}"
  mkdir -p "${dest}"
  if [[ -f "${dest}/${name}" ]]; then
    local sz
    sz="$(wc -c < "${dest}/${name}" | tr -d ' ')"
    if [[ "${sz}" -gt 1000000 ]]; then
      cp -f "${dest}/${name}" "${dest}/model.bin"
      echo "s2: baking TensorRT engine ${dest}/${name} (${sz} bytes)"
      return 0
    fi
  fi
  local src_dir="${EXP4_S2_ENGINE_DIR:-}"
  if [[ -z "${src_dir}" && -f "/var/lib/ina-infra/exp4-s2-models/${name}" ]]; then
    src_dir="/var/lib/ina-infra/exp4-s2-models"
  fi
  if [[ -n "${src_dir}" && -f "${src_dir}/${name}" ]]; then
    cp -f "${src_dir}/${name}" "${dest}/${name}"
    cp -f "${src_dir}/${name}" "${dest}/model.bin"
    echo "s2: staged TensorRT engine from ${src_dir}/${name}"
    return 0
  fi
  if [[ -f "${ssh_cfg}" ]] && command -v scp >/dev/null 2>&1; then
    echo "s2: fetching TensorRT engine from ${host}:/var/lib/ina-infra/exp4-s2-models/${name}"
    if scp -F "${ssh_cfg}" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
      "${host}:/var/lib/ina-infra/exp4-s2-models/${name}" \
      "${dest}/${name}"; then
      cp -f "${dest}/${name}" "${dest}/model.bin"
      echo "s2: staged TensorRT engine from ${host}"
      return 0
    fi
    rm -f "${dest}/${name}"
  fi
  echo "WARN: no prebuilt TensorRT engine in image; first A40 boot still ~20 min."
  echo "  scp ${host}:/var/lib/ina-infra/exp4-s2-models/${name} ${dest}/"
  echo "  then re-run: IMAGE_TAG=${IMAGE_TAG} $0 --push s2"
}

install_shared() {
  local dest="$1" kind="$2"
  case "${kind}" in
    server)
      cp -f "${HERE}/common/connected_clients.py" "${dest}/connected_clients.py"
      if [[ -f "${dest}/influx_publish.py" ]]; then
        cp -f "${HERE}/common/influx_publish.py" "${dest}/influx_publish.py"
      fi
      ;;
    client)
      mkdir -p "${dest}/backend"
      cp -f "${HERE}/common/heartbeat.py" "${dest}/backend/heartbeat.py"
      cp -f "${HERE}/common/influx_publish.py" "${dest}/influx_publish.py"
      ;;
  esac
}

for sid in "${SLICES[@]}"; do
  case "${sid}" in
    s1)
      install_shared "${HERE}/exp4_s1_iperf_sftp/server" server
      install_shared "${HERE}/exp4_s1_iperf_sftp/client" client
      build_one exp4-s1-iperf-sftp-server \
        "${HERE}/exp4_s1_iperf_sftp/server" \
        "${HERE}/exp4_s1_iperf_sftp/server/Dockerfile"
      build_one exp4-s1-iperf-sftp-client \
        "${HERE}/exp4_s1_iperf_sftp/client" \
        "${HERE}/exp4_s1_iperf_sftp/client/Dockerfile"
      ;;
    s2)
      cp -f "${HERE}/common/influx_publish.py" "${HERE}/exp4_s2_cctv/client/influx_publish.py"
      cp -f "${HERE}/common/influx_publish.py" "${HERE}/exp4_s2_cctv/server/influx_publish.py"
      stage_s2_trt_engine
      build_one exp4-s2-cctv-server \
        "${HERE}/exp4_s2_cctv/server" \
        "${HERE}/exp4_s2_cctv/server/Dockerfile"
      build_one exp4-s2-cctv-client \
        "${HERE}/exp4_s2_cctv/client" \
        "${HERE}/exp4_s2_cctv/client/Dockerfile.ue-console"
      ;;
    s3)
      cp -f "${HERE}/common/influx_publish.py" "${HERE}/exp4_s3_ott/client/influx_publish.py"
      cp -f "${HERE}/common/influx_publish.py" "${HERE}/exp4_s3_ott/server/influx_publish.py"
      build_one exp4-s3-ott-server \
        "${HERE}/exp4_s3_ott/server" \
        "${HERE}/exp4_s3_ott/server/Dockerfile"
      build_one exp4-s3-ott-client \
        "${HERE}/exp4_s3_ott/client" \
        "${HERE}/exp4_s3_ott/client/Dockerfile.ue-console"
      ;;
    s4)
      install_shared "${HERE}/exp4_s4_cpu_offload/server" server
      install_shared "${HERE}/exp4_s4_cpu_offload/client" client
      build_one exp4-s4-cpu-offload-server \
        "${HERE}/exp4_s4_cpu_offload/server" \
        "${HERE}/exp4_s4_cpu_offload/server/Dockerfile"
      build_one exp4-s4-cpu-offload-client \
        "${HERE}/exp4_s4_cpu_offload/client" \
        "${HERE}/exp4_s4_cpu_offload/client/Dockerfile"
      ;;
    s5)
      install_shared "${HERE}/exp4_s5_iot/server" server
      install_shared "${HERE}/exp4_s5_iot/client" client
      cp -f "${HERE}/common/influx_publish.py" "${HERE}/exp4_s5_iot/client/influx_publish.py"
      build_one exp4-s5-iot-server \
        "${HERE}/exp4_s5_iot/server" \
        "${HERE}/exp4_s5_iot/server/Dockerfile"
      build_one exp4-s5-iot-client \
        "${HERE}/exp4_s5_iot/client" \
        "${HERE}/exp4_s5_iot/client/Dockerfile.ue-console"
      ;;
  esac
done

echo
echo "Done (${#BUILT[@]} images):"
for t in "${BUILT[@]}"; do
  echo "  - ${t}"
done
if [[ "${PUSH}" != "1" ]]; then
  echo
  echo "Local only. Push with:  $0 --push"
fi
