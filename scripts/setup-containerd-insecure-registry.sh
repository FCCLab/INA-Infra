#!/usr/bin/env bash
# Allow containerd/CRI to pull from the HTTP registry on mgmt (once per node).
#
#   sudo ./scripts/setup-containerd-insecure-registry.sh
#   sudo ./scripts/setup-containerd-insecure-registry.sh 10.1.132.30:5000
#
# Run on every cluster node that must pull images (e.g. central-0, central-1).
set -euo pipefail

REGISTRY="${1:-10.1.132.30:5000}"
CERTS_DIR="/etc/containerd/certs.d/${REGISTRY}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0 [REGISTRY]" >&2
  exit 1
fi

mkdir -p "$CERTS_DIR"
cat >"${CERTS_DIR}/hosts.toml" <<EOF
server = "http://${REGISTRY}"

[host."http://${REGISTRY}"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
EOF

if [[ -f /etc/containerd/config.toml ]] && ! grep -q 'config_path = "/etc/containerd/certs.d"' /etc/containerd/config.toml; then
  mkdir -p /etc/containerd/conf.d
  cat >/etc/containerd/conf.d/registry-certs.d.toml <<'EOF'
[plugins.'io.containerd.cri.v1.images'.registry]
  config_path = "/etc/containerd/certs.d"
EOF
  echo "Wrote /etc/containerd/conf.d/registry-certs.d.toml"
fi

echo "Wrote ${CERTS_DIR}/hosts.toml"
systemctl restart containerd
echo "containerd restarted."
