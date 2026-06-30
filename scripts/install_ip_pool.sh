#!/usr/bin/env bash
# Install MetalLB and the shared IPAddressPool on mgmt and workload clusters.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
METALLB_CHART_VERSION="${METALLB_CHART_VERSION:-0.14.9}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Install MetalLB and IPAddressPool on cluster control planes.
Mgmt pool: ${MGMT_METALLB_POOL}. Workload pool: ${CLUSTER_METALLB_POOL}.
With no arguments, installs on mgmt, central, regional, edge, and ue.

Examples:
  $(basename "$0")
  $(basename "$0") central regional
  METALLB_POOL=10.1.137.40-10.1.137.99 $(basename "$0") central

Environment:
  SSH_CONFIG            SSH config (default: utils/ssh_config/config)
  METALLB_CHART_VERSION Helm chart version (default: 0.14.9)
  MGMT_METALLB_POOL     Mgmt cluster pool (default: 10.1.132.10-10.1.132.99)
  CLUSTER_METALLB_POOL  Workload cluster pool (default: 10.1.137.40-10.1.137.99)
  METALLB_POOL          Override pool for all clusters (optional)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  local_script="$(mktemp)"
  remote_script="/tmp/install-ip-pool-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp -q -F "$SSH_CONFIG" "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

install_helm_remote_script() {
  cat <<'EOF'
if command -v helm >/dev/null 2>&1; then
  helm version --short
else
  echo "==> install helm"
  if curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash; then
    helm version --short
  else
    echo "==> install helm via apt (raw.githubusercontent.com unavailable)"
    sudo apt-get -o Dpkg::Use-Pty=0 update -qq
    sudo apt-get -o Dpkg::Use-Pty=0 install -y curl gpg apt-transport-https
    curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" \
      | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list >/dev/null
    sudo apt-get -o Dpkg::Use-Pty=0 update -qq
    sudo apt-get -o Dpkg::Use-Pty=0 install -y helm
    helm version --short
  fi
fi
EOF
}

install_ip_pool_on_cluster() {
  local cluster="$1"
  local host pool
  host="$(cluster_cp_host "$cluster")"
  pool="$(metallb_pool_for_cluster "$cluster")"

  echo
  echo "========================================"
  echo " MetalLB IP pool: ${cluster}"
  echo " Control plane: ${host}"
  echo " Pool: ${pool}"
  echo "========================================"

  run_remote_script "$host" <<EOF
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"
export DEBIAN_FRONTEND=noninteractive

if [[ ! -f "\$KUBECONFIG" ]]; then
  echo "error: missing \$KUBECONFIG (bring up ${cluster} first)" >&2
  exit 1
fi

$(install_helm_remote_script)

echo "==> MetalLB (chart ${METALLB_CHART_VERSION})"
helm repo add metallb https://metallb.github.io/metallb 2>/dev/null || true
helm repo update metallb
if ! kubectl get namespace metallb-system >/dev/null 2>&1; then
  helm upgrade --install metallb metallb/metallb \\
    --namespace metallb-system --create-namespace \\
    --version ${METALLB_CHART_VERSION} --wait --timeout 10m
else
  helm upgrade --install metallb metallb/metallb \\
    --namespace metallb-system \\
    --version ${METALLB_CHART_VERSION} --wait --timeout 10m
fi

echo "==> IPAddressPool local-pool (${pool})"
kubectl apply -f - <<METALLB
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: local-pool
  namespace: metallb-system
spec:
  addresses:
    - ${pool}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: local-advertisement
  namespace: metallb-system
METALLB

kubectl get ipaddresspool -n metallb-system local-pool -o wide
kubectl get l2advertisement -n metallb-system local-advertisement -o wide
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_METALLB_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    case "$cluster" in
      mgmt) ;;
      *)
        if [[ -z "${CLUSTER_CP_HOST[$cluster]:-}" ]]; then
          echo "error: unknown cluster '${cluster}' (expected mgmt, central, regional, edge, or ue)" >&2
          exit 1
        fi
        ;;
    esac
    clusters+=("$cluster")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! install_ip_pool_on_cluster "$cluster"; then
    failed=1
  fi
done

exit "$failed"
