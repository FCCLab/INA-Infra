#!/usr/bin/env bash
# Build arm64 vLLM/Cosmos3 image on gh82 and push to the lab registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_CONFIG="${SSH_CONFIG:-/home/fcp/INA-Infra/utils/ssh_config/config}"
REMOTE_HOST="${REMOTE_HOST:-gpu-gh82}"
REMOTE_DIR="${REMOTE_DIR:-/tmp/cosmo3-vllm-build}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-nws-v0.5-arm64}"
LOCAL_TAG="cosmo3-vllm:${IMAGE_TAG}"
REMOTE_TAG="${REGISTRY}/cosmo3-vllm:${IMAGE_TAG}"

ssh_cmd() { ssh -F "${SSH_CONFIG}" "${REMOTE_HOST}" "$@"; }
scp_cmd() { scp -F "${SSH_CONFIG}" "$@"; }

echo "==> Ensuring gh82 Docker allows insecure registry ${REGISTRY}"
ssh_cmd "sudo mkdir -p /etc/docker && echo '${REGISTRY}' | sudo tee /tmp/cosmo3-registry.txt >/dev/null && sudo python3 - <<'PY'
import json
from pathlib import Path
reg = Path('/tmp/cosmo3-registry.txt').read_text().strip()
p = Path('/etc/docker/daemon.json')
data = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
regs = set(data.get('insecure-registries') or [])
regs.add(reg)
data['insecure-registries'] = sorted(regs)
Path('/tmp/daemon.json').write_text(json.dumps(data, indent=2) + '\n')
print('prepared', data['insecure-registries'])
PY
sudo mv /tmp/daemon.json /etc/docker/daemon.json
sudo systemctl restart docker
docker info | grep -A5 'Insecure Registries' || true"

echo "==> Syncing build context to ${REMOTE_HOST}:${REMOTE_DIR}"
ssh_cmd "sudo rm -rf '${REMOTE_DIR}' && mkdir -p '${REMOTE_DIR}' && sudo chown -R \$(id -u):\$(id -g) '${REMOTE_DIR}'"
scp_cmd \
  "${SCRIPT_DIR}/Dockerfile.vllm" \
  "${SCRIPT_DIR}/entrypoint-vllm.sh" \
  "${REMOTE_HOST}:${REMOTE_DIR}/"

echo "==> Building ${REMOTE_TAG} on ${REMOTE_HOST} (this can take a long time)"
ssh_cmd "set -euo pipefail; cd '${REMOTE_DIR}'; docker build -f Dockerfile.vllm -t '${LOCAL_TAG}' -t '${REMOTE_TAG}' .; echo '==> Pushing ${REMOTE_TAG}'; docker push '${REMOTE_TAG}'; docker image ls '${REMOTE_TAG}'"

echo "Done: ${REMOTE_TAG}"
