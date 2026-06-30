#!/usr/bin/env bash
# Join central worker node 1 to the existing central cluster (no kubeadm init).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CENTRAL_SSH="${CENTRAL_SSH:-fcp@10.1.132.211}"
CENTRAL_USER="${CENTRAL_USER:-${CENTRAL_SSH%%@*}}"
NODE_IP="${NODE_IP:-10.1.137.111}"
NODE_NAME="${NODE_NAME:-central-1}"
CONTROL_PLANE_SSH="${CONTROL_PLANE_SSH:-fcp@10.1.132.210}"
KUBE_CONTEXT="${KUBE_CONTEXT:-central@central}"
K8S_VERSION="${K8S_VERSION:-1.31}"

SUDO_PASSWORD="${SUDO_PASSWORD:-}"
HAS_NOPASSWD=0

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Prepare ${CENTRAL_SSH} and join it to the central cluster as worker ${NODE_NAME}.
Does not run kubeadm init or install CNI — central-0 must already be up.

Steps:
  1. Configure passwordless sudo on the worker
  2. Rename host to ${NODE_NAME}
  3. Host prep (swap, kernel modules, sysctl)
  4. Install containerd, kubelet, kubeadm, kubectl (${K8S_VERSION}.x)
  5. kubeadm join (token from ${CONTROL_PLANE_SSH})
  6. Wait until ${NODE_NAME} is Ready (checked from control plane)

Prerequisites:
  - central-0 initialized (run bringup-central-0.sh first)
  - Passwordless sudo on ${CONTROL_PLANE_SSH} (for join token)
  - TCP 6443 reachable from ${NODE_IP} to the API server

Options:
  -h, --help    Show this help

Environment:
  CENTRAL_SSH         SSH target for worker (default: fcp@10.1.132.211)
  CENTRAL_USER        SSH user / sudoers name
  NODE_IP             Worker site IP / /etc/hosts entry (default: 10.1.137.111)
  NODE_NAME           Kubernetes node name (default: central-1)
  CONTROL_PLANE_SSH   SSH to control plane for join token (default: fcp@10.1.132.210)
  KUBE_CONTEXT        kubectl context on control plane (default: central@central)
  K8S_VERSION         Kubernetes minor version (default: 1.31)
  SUDO_PASSWORD       Optional sudo password for worker (memory only)

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

control_plane() {
  ssh "$CONTROL_PLANE_SSH" "$@"
}

control_plane_kubectl() {
  control_plane "KUBECONFIG=\$HOME/.kube/config-central kubectl --context=$(printf '%q' "$KUBE_CONTEXT") $(printf '%q ' "$@")"
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

ensure_passwordless_sudo() {
  echo "==> [1/6] Configure passwordless sudo for ${CENTRAL_USER}"

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

fetch_join_command() {
  local join_cmd
  echo "==> Fetching kubeadm join command from ${CONTROL_PLANE_SSH}" >&2
  if ! join_cmd="$(control_plane "sudo -n kubeadm token create --print-join-command" 2>/dev/null)"; then
    echo "error: could not create join token on control plane" >&2
    echo "Ensure ${CONTROL_PLANE_SSH} is initialized and has passwordless sudo." >&2
    exit 1
  fi
  [[ -n "$join_cmd" ]] || { echo "error: empty join command from control plane" >&2; exit 1; }
  printf '%s' "$join_cmd"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

echo "==> Central node 1 join (${CENTRAL_SSH} -> cluster on ${CONTROL_PLANE_SSH})"
echo "    Node name: ${NODE_NAME}"
echo "    Node IP:   ${NODE_IP}"
echo

if ! control_plane "test -f /etc/kubernetes/admin.conf" 2>/dev/null; then
  echo "error: control plane not initialized on ${CONTROL_PLANE_SSH}" >&2
  echo "Run bringup-central-0.sh first." >&2
  exit 1
fi

prompt_sudo_password
ensure_passwordless_sudo

echo "==> [2/6] Rename host to ${NODE_NAME}"
echo "==> [3/6] Host prep"
run_remote_script <<EOF
set -euo pipefail
echo "==> Rename host to ${NODE_NAME}"
sudo hostnamectl set-hostname '${NODE_NAME}'
if ! grep -qE '[[:space:]]${NODE_NAME}(\$|[[:space:]])' /etc/hosts; then
  echo '${NODE_IP} ${NODE_NAME}' | sudo tee -a /etc/hosts >/dev/null
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
sudo sysctl -p /etc/sysctl.d/k8s.conf
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.bridge.bridge-nf-call-iptables=1
sudo sysctl -w net.bridge.bridge-nf-call-ip6tables=1
[[ "\$(cat /proc/sys/net/ipv4/ip_forward)" == "1" ]]
EOF

echo "==> [4/6] Install Kubernetes ${K8S_VERSION}.x"
run_remote_script <<EOF
set -euo pipefail

echo "==> kubeadm preflight packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \\
  conntrack ebtables ethtool socat ipset \\
  apt-transport-https ca-certificates curl gpg
command -v conntrack >/dev/null

if command -v kubeadm >/dev/null && command -v kubelet >/dev/null; then
  echo "kubeadm/kubelet already installed:"
  kubeadm version -o short 2>/dev/null || true
  kubelet --version 2>/dev/null || true
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

echo "==> [5/6] kubeadm join"
if remote "test -f /etc/kubernetes/kubelet.conf" 2>/dev/null; then
  echo "Node already joined (kubelet.conf present); skipping kubeadm join"
else
  echo "==> Re-apply network sysctl before join"
  run_remote_script <<'EOF'
set -euo pipefail
sudo modprobe bridge
sudo modprobe br_netfilter
sudo modprobe overlay
sudo sysctl -p /etc/sysctl.d/k8s.conf
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.bridge.bridge-nf-call-iptables=1
sudo sysctl -w net.bridge.bridge-nf-call-ip6tables=1
if [[ "$(cat /proc/sys/net/ipv4/ip_forward)" != "1" ]]; then
  echo "error: net.ipv4.ip_forward is not 1" >&2
  exit 1
fi
EOF
  join_cmd="$(fetch_join_command)"
  echo "Running join on ${CENTRAL_SSH} ..."
  remote_sudo "$join_cmd"
fi

echo "==> [6/6] Wait for ${NODE_NAME} Ready"
for _ in $(seq 1 60); do
  status="$(control_plane_kubectl get node "$NODE_NAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  if [[ "$status" == "True" ]]; then
    break
  fi
  sleep 5
done

echo
control_plane_kubectl get nodes -o wide
control_plane_kubectl get pods -n kube-system -o wide --field-selector "spec.nodeName=${NODE_NAME}" 2>/dev/null || true

echo
echo "Done. Worker ${NODE_NAME} joined the central cluster."
echo "MetalLB L2 can now announce from ${NODE_NAME} (no exclude-from-external-load-balancers label on workers)."
