#!/usr/bin/env bash
# Restore containerd CRI after Docker install overwrote /etc/containerd/config.toml.
# Run on mgmt node-0 (or any kubeadm node that became NotReady).
set -euo pipefail

NODE="${1:-$(hostname -s)}"
echo "==> Fix containerd CRI on ${NODE}"

if [[ ! -f /etc/containerd/config.toml ]]; then
  echo "error: /etc/containerd/config.toml not found" >&2
  exit 1
fi

if grep -q 'disabled_plugins = \["cri"\]' /etc/containerd/config.toml; then
  echo "Found Docker-style config (CRI disabled) — restoring Kubernetes containerd config"
else
  echo "CRI does not appear disabled; continuing anyway"
fi

sudo cp -a /etc/containerd/config.toml "/etc/containerd/config.toml.bak.$(date +%Y%m%d%H%M%S)"

# Match working worker config (node-1): full default + SystemdCgroup.
containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

# Ensure CRI is not disabled (Docker sets disabled_plugins = ["cri"]).
sudo sed -i 's/disabled_plugins = \["cri"\]/disabled_plugins = []/' /etc/containerd/config.toml

echo "==> Restart containerd + kubelet"
sudo systemctl restart containerd
sleep 2
sudo systemctl restart kubelet

echo "==> Wait for CRI"
for _ in $(seq 1 30); do
  if sudo crictl info >/dev/null 2>&1; then
    echo "CRI OK"
    break
  fi
  sleep 2
done

sudo crictl info 2>/dev/null | head -3 || echo "warning: crictl still failing — check journalctl -u containerd -u kubelet"

if command -v kubectl >/dev/null 2>&1; then
  echo
  kubectl --context=mgmt@mgmt get nodes -o wide 2>/dev/null || kubectl get nodes -o wide
fi

echo
echo "Done. Docker may still work; Kubernetes uses containerd CRI on the same daemon."
echo "If node stays NotReady, check: journalctl -u kubelet -n 30 --no-pager"
