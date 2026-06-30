#!/usr/bin/env bash
# Bootstrap kubeadm on central node 0 via SSH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CENTRAL_SSH="${CENTRAL_SSH:-fcp@10.1.132.210}"
CENTRAL_USER="${CENTRAL_USER:-${CENTRAL_SSH%%@*}}"
CENTRAL_API_IP="${CENTRAL_API_IP:-10.1.137.110}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
FLANNEL_MANIFEST="${FLANNEL_MANIFEST:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"
NODE_NAME="${NODE_NAME:-central-0}"
CLUSTER_NAME="${CLUSTER_NAME:-central}"
KUBE_CONTEXT="${KUBE_CONTEXT:-central@central}"
K8S_VERSION="${K8S_VERSION:-1.31}"
SKIP_LOCAL_KUBECONFIG="${SKIP_LOCAL_KUBECONFIG:-0}"

SUDO_PASSWORD="${SUDO_PASSWORD:-}"
HAS_NOPASSWD=0

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Install Kubernetes on central node 0 (SSH to ${CENTRAL_SSH}).

Prompts once for the remote sudo password (unless already NOPASSWD).
Password is kept in memory for step 1, then cleared after NOPASSWD is set.

Steps:
  1. Configure passwordless sudo for ${CENTRAL_USER} (aborts if this fails)
  2. Rename host to ${NODE_NAME}
  3. Host prep (swap, kernel modules, sysctl)
  4. Install containerd, kubelet, kubeadm, kubectl (${K8S_VERSION}.x)
  5. kubeadm init (skipped if already initialized)
  6. Remote kubeconfig (~/.kube/config-central) + rename to ${KUBE_CONTEXT}
  7. Flannel CNI
  8. Optional: copy kubeconfig to this workstation

Prerequisites on central node 0:
  Ubuntu 22.04+ with network access to pkgs.k8s.io

Options:
  -h, --help    Show this help

Environment:
  CENTRAL_SSH             SSH target (default: fcp@10.1.132.210)
  CENTRAL_USER            SSH user / sudoers name (default: user from CENTRAL_SSH)
  CENTRAL_API_IP          apiserver-advertise-address (default: 10.1.137.110)
  SUDO_PASSWORD           Optional: skip prompt (kept in memory only)
  NODE_NAME               Kubernetes node / host name (default: central-0)
  CLUSTER_NAME            kubeconfig cluster/user name (default: central)
  POD_NETWORK_CIDR        Flannel CIDR (default: 10.244.0.0/16)
  K8S_VERSION             Kubernetes minor version (default: 1.31)
  SKIP_LOCAL_KUBECONFIG   Set to 1 to skip scp kubeconfig to local ~/.kube

Example:
  $(basename "$0")
EOF
}

remote() {
  ssh "$CENTRAL_SSH" "$@"
}

remote_sudo() {
  local cmd="$1"
  if [[ "$HAS_NOPASSWD" -eq 1 ]]; then
    remote "sudo -n bash -lc $(printf '%q' "$cmd")"
  else
    printf '%s\n' "$SUDO_PASSWORD" | ssh "$CENTRAL_SSH" "sudo -S -p '' bash -lc $(printf '%q' "$cmd")" 2>/dev/null
  fi
}

prompt_sudo_password() {
  if remote "sudo -n true" 2>/dev/null; then
    HAS_NOPASSWD=1
    echo "Passwordless sudo already active for ${CENTRAL_USER}"
    return 0
  fi

  if [[ -n "$SUDO_PASSWORD" ]]; then
    if printf '%s\n' "$SUDO_PASSWORD" | remote "sudo -S -v" 2>/dev/null; then
      echo "Using SUDO_PASSWORD from environment"
      return 0
    fi
    echo "error: SUDO_PASSWORD is set but invalid on ${CENTRAL_SSH}" >&2
    exit 1
  fi

  read -rsp "sudo password for ${CENTRAL_USER} on ${CENTRAL_SSH}: " SUDO_PASSWORD
  echo
  if ! printf '%s\n' "$SUDO_PASSWORD" | remote "sudo -S -v" 2>/dev/null; then
    echo "error: invalid sudo password" >&2
    SUDO_PASSWORD=""
    exit 1
  fi
  echo "sudo password accepted"
}

run_remote_script() {
  local local_script remote_script
  local_script="$(mktemp)"
  remote_script="/tmp/bringup-central-${$}-${RANDOM}.sh"
  trap 'rm -f "$local_script"' RETURN

  cat >"$local_script"
  scp -q "$local_script" "${CENTRAL_SSH}:${remote_script}"
  ssh "$CENTRAL_SSH" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc"
}

remote_kubectl() {
  ssh "$CENTRAL_SSH" "KUBECONFIG=\$HOME/.kube/config-central kubectl $(printf '%q ' "$@")"
}

ensure_passwordless_sudo() {
  echo "==> [1/8] Configure passwordless sudo for ${CENTRAL_USER}"

  if [[ "$HAS_NOPASSWD" -eq 1 ]]; then
    return 0
  fi

  remote_sudo "printf '%s\n' '${CENTRAL_USER} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/${CENTRAL_USER} && chmod 440 /etc/sudoers.d/${CENTRAL_USER}"

  if ! remote "sudo -n true" 2>/dev/null; then
    echo "error: failed to configure passwordless sudo for ${CENTRAL_USER}" >&2
    exit 1
  fi

  HAS_NOPASSWD=1
  SUDO_PASSWORD=""
  echo "Passwordless sudo OK for ${CENTRAL_USER}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

echo "==> Central node 0 bring-up (${CENTRAL_SSH})"
echo "    API address: ${CENTRAL_API_IP}"
echo "    Node name:   ${NODE_NAME}"
echo

prompt_sudo_password
ensure_passwordless_sudo

echo "==> [2/8] Rename host to ${NODE_NAME}"
echo "==> [3/8] Host prep"
run_remote_script <<EOF
set -euo pipefail
echo "==> Rename host to ${NODE_NAME}"
sudo hostnamectl set-hostname '${NODE_NAME}'
if ! grep -qE '[[:space:]]${NODE_NAME}(\$|[[:space:]])' /etc/hosts; then
  echo '${CENTRAL_API_IP} ${NODE_NAME}' | sudo tee -a /etc/hosts >/dev/null
fi
hostname

echo "==> Host prep"
sudo swapoff -a
sudo sed -i '/ swap / s/^\(.*\)$/#\1/' /etc/fstab
sudo modprobe bridge
sudo modprobe br_netfilter
sudo modprobe overlay
test -f /proc/sys/net/bridge/bridge-nf-call-iptables
cat <<EOC | sudo tee /etc/modules-load.d/k8s.conf >/dev/null
bridge
br_netfilter
overlay
EOC
cat <<EOC | sudo tee /etc/sysctl.d/k8s.conf >/dev/null
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOC
sysctl --system 2>/dev/null || true
EOF

echo "==> [4/8] Install Kubernetes ${K8S_VERSION}.x"
run_remote_script <<EOF
set -euo pipefail

echo "==> kubeadm preflight packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \\
  conntrack ebtables ethtool socat ipset \\
  apt-transport-https ca-certificates curl gpg
command -v conntrack >/dev/null

if command -v kubeadm >/dev/null && command -v kubelet >/dev/null && command -v kubectl >/dev/null; then
  echo "kubeadm/kubelet/kubectl already installed:"
  kubeadm version -o short 2>/dev/null || true
  kubelet --version 2>/dev/null || true
  kubectl version --client=true 2>/dev/null | head -1 || true
else
  echo "==> containerd"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq containerd
  sudo mkdir -p /etc/containerd
  if [[ ! -f /etc/containerd/config.toml ]]; then
    containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
  fi
  sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
  sudo systemctl enable --now containerd

  echo "==> kubelet kubeadm kubectl"
  sudo install -d -m 0755 /etc/apt/keyrings
  curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" \\
    | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
  echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" \\
    | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq kubelet kubeadm kubectl
  sudo apt-mark hold kubelet kubeadm kubectl
  sudo systemctl enable kubelet
  kubeadm version -o short
fi
EOF

echo "==> [5/8] kubeadm init"
run_remote_script <<EOF
set -euo pipefail
command -v conntrack >/dev/null || { echo "error: conntrack missing" >&2; exit 1; }
if [[ -f /etc/kubernetes/admin.conf ]]; then
  echo 'Cluster already initialized; skipping kubeadm init'
  exit 0
fi
sudo kubeadm init \\
  --pod-network-cidr='${POD_NETWORK_CIDR}' \\
  --apiserver-advertise-address='${CENTRAL_API_IP}'
EOF

echo "==> [6/8] Remote kubeconfig"
run_remote_script <<'EOF'
set -euo pipefail
mkdir -p "$HOME/.kube"
sudo cp -f /etc/kubernetes/admin.conf "$HOME/.kube/config-central"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config-central"
chmod 600 "$HOME/.kube/config-central"
EOF
scp -q "$REPO_ROOT/rename.sh" "${CENTRAL_SSH}:/tmp/rename.sh"
run_remote_script <<EOF
set -euo pipefail
chmod +x /tmp/rename.sh
/tmp/rename.sh '${CLUSTER_NAME}' '${CLUSTER_NAME}' "\$HOME/.kube/config-central"
KUBECONFIG="\$HOME/.kube/config-central" kubectl config current-context
KUBECONFIG="\$HOME/.kube/config-central" kubectl cluster-info
EOF

echo "==> [7/8] Flannel CNI"
run_remote_script <<'EOF'
set -euo pipefail
sudo modprobe bridge
sudo modprobe br_netfilter
sudo modprobe overlay
test -f /proc/sys/net/bridge/bridge-nf-call-iptables
EOF
remote_kubectl apply -f "$FLANNEL_MANIFEST"

echo "==> Waiting for node Ready"
for _ in $(seq 1 60); do
  if remote_kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True; then
    break
  fi
  sleep 5
done

echo
remote_kubectl get nodes -o wide
remote_kubectl get pods -n kube-system

if [[ "$SKIP_LOCAL_KUBECONFIG" != "1" ]]; then
  echo "==> [8/8] Copy kubeconfig to local workstation"
  mkdir -p "$HOME/.kube"
  scp -q "${CENTRAL_SSH}:.kube/config-central" "$HOME/.kube/config-central"
  chmod 600 "$HOME/.kube/config-central"
  "$REPO_ROOT/rename.sh" "$CLUSTER_NAME" "$CLUSTER_NAME" "$HOME/.kube/config-central"
  echo
  echo "Local context: ${KUBE_CONTEXT}"
  KUBECONFIG="$HOME/.kube/config-central" kubectl config use-context "$KUBE_CONTEXT" >/dev/null
  KUBECONFIG="$HOME/.kube/config-central" kubectl cluster-info
else
  echo "==> [8/8] Skipped local kubeconfig copy (SKIP_LOCAL_KUBECONFIG=1)"
fi

echo
echo "Done. Use both clusters:"
echo "  export KUBECONFIG=\$HOME/.kube/config:\$HOME/.kube/config-central"
echo "  kubectl config use-context ${KUBE_CONTEXT}"
