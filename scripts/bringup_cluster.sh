#!/usr/bin/env bash
# Bootstrap kubeadm clusters on central, regional, edge, and ue (control plane + worker each).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
RENAME_SH="${RENAME_SH:-$REPO_ROOT/scripts/rename.sh}"

K8S_VERSION="${K8S_VERSION:-1.31}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
FLANNEL_MANIFEST="${FLANNEL_MANIFEST:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"
DNS_SERVER="${DNS_SERVER:-10.1.132.200}"
MGMT_GATEWAY="${MGMT_GATEWAY:-10.1.132.1}"
NETPLAN_DIR="${NETPLAN_DIR:-$REPO_ROOT/utils/netplan}"
SKIP_LOCAL_KUBECONFIG="${SKIP_LOCAL_KUBECONFIG:-0}"
SSH_USER="${SSH_USER:-fcp}"
INSTALL_DASHBOARD="${INSTALL_DASHBOARD:-1}"

SUDO_PASSWORD="${SUDO_PASSWORD:-}"
ACTIVE_HOST=""
HAS_NOPASSWD=0

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

usage() {
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Bring up Kubernetes on central, regional, edge, and/or ue workload sites.
Each cluster: kubeadm init on *-0, join *-1, Flannel CNI, kubeconfig, optional Dashboard.

With no arguments, brings up all four clusters (central, regional, edge, ue).

Examples:
  $(basename "$0")
  $(basename "$0") regional
  $(basename "$0") edge ue

Clusters:
  central    ${CLUSTER_CP_HOST[central]} (${CLUSTER_API_IP[central]}) + ${CLUSTER_WORKER_HOST[central]}  dashboard ${CLUSTER_DASHBOARD_VIP[central]}
  regional   ${CLUSTER_CP_HOST[regional]} (${CLUSTER_API_IP[regional]}) + ${CLUSTER_WORKER_HOST[regional]}  dashboard ${CLUSTER_DASHBOARD_VIP[regional]}
  edge       ${CLUSTER_CP_HOST[edge]} (${CLUSTER_API_IP[edge]}) + ${CLUSTER_WORKER_HOST[edge]}  dashboard ${CLUSTER_DASHBOARD_VIP[edge]}
  ue         ${CLUSTER_CP_HOST[ue]} (${CLUSTER_API_IP[ue]}) + ${CLUSTER_WORKER_HOST[ue]}  dashboard ${CLUSTER_DASHBOARD_VIP[ue]}

Environment:
  SSH_CONFIG              SSH config (default: utils/ssh_config/config)
  K8S_VERSION             Kubernetes minor version (default: 1.31)
  POD_NETWORK_CIDR        Flannel CIDR (default: 10.244.0.0/16)
  DNS_SERVER              Resolver for apt on remote hosts (default: 10.1.132.200)
  SKIP_LOCAL_KUBECONFIG   Set to 1 to skip copying kubeconfigs to local ~/.kube
  INSTALL_DASHBOARD       Set to 0 to skip MetalLB + Dashboard (default: 1)
  SUDO_PASSWORD           Optional sudo password (prompted per host if needed)
EOF
}

ssh_cmd() {
  ssh -F "$SSH_CONFIG" "$@"
}

scp_cmd() {
  scp -F "$SSH_CONFIG" "$@"
}

remote() {
  ssh_cmd "$ACTIVE_HOST" "$@"
}

remote_sudo() {
  local cmd="$1"
  echo ">>> [${ACTIVE_HOST}] sudo"
  if [[ "$HAS_NOPASSWD" -eq 1 ]]; then
    ssh_cmd "$ACTIVE_HOST" "sudo -n bash -lc $(printf '%q' "$cmd")"
  else
    printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd "$ACTIVE_HOST" \
      "sudo -S -p '' bash -lc $(printf '%q' "$cmd")"
  fi
}

prompt_sudo_password() {
  local host="$1"
  ACTIVE_HOST="$host"
  HAS_NOPASSWD=0

  if remote "sudo -n true" 2>/dev/null; then
    HAS_NOPASSWD=1
    echo "Passwordless sudo already active on ${host}"
    return 0
  fi

  if [[ -n "$SUDO_PASSWORD" ]]; then
    if printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd "$host" "sudo -S -v" 2>/dev/null; then
      echo "Using SUDO_PASSWORD on ${host}"
      return 0
    fi
    echo "error: SUDO_PASSWORD invalid on ${host}" >&2
    exit 1
  fi

  read -rsp "sudo password for ${SSH_USER} on ${host}: " SUDO_PASSWORD
  echo
  if ! printf '%s\n' "$SUDO_PASSWORD" | ssh_cmd "$host" "sudo -S -v" 2>/dev/null; then
    echo "error: invalid sudo password on ${host}" >&2
    SUDO_PASSWORD=""
    exit 1
  fi
}

ensure_passwordless_sudo() {
  local host="$1"
  ACTIVE_HOST="$host"

  if [[ "$HAS_NOPASSWD" -eq 1 ]]; then
    return 0
  fi

  remote_sudo "printf '%s\n' '${SSH_USER} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/${SSH_USER} && chmod 440 /etc/sudoers.d/${SSH_USER}"

  if ! remote "sudo -n true" 2>/dev/null; then
    echo "error: failed to configure passwordless sudo on ${host}" >&2
    exit 1
  fi

  HAS_NOPASSWD=1
  SUDO_PASSWORD=""
}

run_remote_script() {
  local host="$1"
  local local_script remote_script rc=0
  ACTIVE_HOST="$host"
  local_script="$(mktemp)"
  remote_script="/tmp/bringup-cluster-${$}-${RANDOM}.sh"

  cat >"$local_script"
  scp_cmd "$local_script" "${host}:${remote_script}"
  echo ""
  echo ">>> [${host}] --- remote output ---"
  ssh_cmd -o RequestTTY=no "$host" "bash '${remote_script}'; rc=\$?; rm -f '${remote_script}'; exit \$rc" || rc=$?
  echo ">>> [${host}] --- done (exit ${rc}) ---"
  rm -f "$local_script"
  return "$rc"
}

remote_kubectl() {
  local host="$1"
  shift
  echo ">>> [${host}] kubectl $*"
  ssh_cmd -o RequestTTY=no "$host" "kubectl $(printf '%q ' "$@")"
}

configure_dns_script() {
  cat <<EOF
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/nephio-dns.conf >/dev/null <<EOC
[Resolve]
DNS=${DNS_SERVER}
FallbackDNS=8.8.8.8
Domains=~.
EOC
if systemctl is-active systemd-resolved &>/dev/null; then
  sudo systemctl restart systemd-resolved
fi
if ! getent ahostsv4 archive.ubuntu.com &>/dev/null; then
  sudo rm -f /etc/resolv.conf
  echo "nameserver ${DNS_SERVER}" | sudo tee /etc/resolv.conf >/dev/null
fi
EOF
}

noninteractive_apt_script() {
  cat <<'EOF'
export DEBIAN_FRONTEND=noninteractive
export DEBIAN_PRIORITY=critical
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1
sudo debconf-set-selections <<'DEBCONF' || true
needrestart needrestart/restart string a
needrestart needrestart/kernelhints boolean false
DEBCONF
if [[ -f /etc/needrestart/needrestart.conf ]]; then
  sudo sed -i 's/^\$nrconf{kernelhints}.*/$nrconf{kernelhints} = 0;/' /etc/needrestart/needrestart.conf
fi
if [[ -x /etc/kernel/postinst.d/zz-update-notifier ]]; then
  sudo chmod -x /etc/kernel/postinst.d/zz-update-notifier
fi
APT_OPTS="-o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=120"
EOF
}

configure_mgmt_network() {
  local host="$1"
  local mgmt_file="${NETPLAN_DIR}/${host}/55-nephio-mgmt.yaml"
  local remote_file="/tmp/55-nephio-mgmt.yaml.$$"

  if [[ ! -f "$mgmt_file" ]]; then
    echo "error: missing ${mgmt_file}" >&2
    return 1
  fi

  scp_cmd "$mgmt_file" "${host}:${remote_file}"
  run_remote_script "$host" <<EOF
set -euo pipefail
echo "==> apply mgmt netplan (default via ${MGMT_GATEWAY})"
sudo install -m 600 '${remote_file}' /etc/netplan/55-nephio-mgmt.yaml
rm -f '${remote_file}'
sudo netplan apply
echo "==> verify default route"
ip -4 route show default
getent ahostsv4 archive.ubuntu.com
EOF
}

host_prep() {
  local host="$1" node_name="$2" node_ip="$3"
  configure_mgmt_network "$host"
  run_remote_script "$host" <<EOF
set -euo pipefail
echo "==> set hostname ${node_name}"
sudo hostnamectl set-hostname '${node_name}'
if ! grep -qE '[[:space:]]${node_name}(\$|[[:space:]])' /etc/hosts; then
  echo '${node_ip} ${node_name}' | sudo tee -a /etc/hosts
fi
echo "==> disable swap"
sudo swapoff -a
sudo sed -i '/ swap / s/^\(.*\)$/#\1/' /etc/fstab
$(cni_prereqs_remote_script)
EOF
}

cni_prereqs_remote_script() {
  cat <<'EOF'
echo "==> CNI kernel modules and sysctl"
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
for mod in bridge br_netfilter overlay; do
  if ! sudo modprobe "$mod" 2>/dev/null; then
    echo "==> install linux-modules-extra-$(uname -r) for ${mod}"
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get -o Dpkg::Use-Pty=0 update -qq
    sudo apt-get -o Dpkg::Use-Pty=0 install -y "linux-modules-extra-$(uname -r)" || true
    sudo modprobe "$mod"
  fi
done
sudo sysctl -p /etc/sysctl.d/k8s.conf
if [[ ! -f /proc/sys/net/bridge/bridge-nf-call-iptables ]]; then
  echo "error: br_netfilter sysctl missing after modprobe" >&2
  exit 1
fi
echo "==> CNI prerequisites OK"
sysctl net.bridge.bridge-nf-call-iptables net.ipv4.ip_forward
EOF
}

ensure_cni_prereqs() {
  local host="$1"
  echo "==> [${host}] ensure CNI prerequisites"
  run_remote_script "$host" <<EOF
set -euo pipefail
$(cni_prereqs_remote_script)
EOF
}

install_kubernetes() {
  local host="$1"
  run_remote_script "$host" <<EOF
set -euo pipefail
$(noninteractive_apt_script)
echo "==> configure DNS (${DNS_SERVER})"
$(configure_dns_script)
echo "==> apt-get update"
sudo apt-get \$APT_OPTS update
echo "==> install kubeadm preflight packages"
sudo apt-get \$APT_OPTS install -y \\
  conntrack ebtables ethtool socat ipset \\
  apt-transport-https ca-certificates curl gpg
command -v conntrack >/dev/null

if command -v kubeadm >/dev/null && command -v kubelet >/dev/null && command -v kubectl >/dev/null; then
  echo "==> kubernetes already installed"
  kubeadm version -o short || true
  kubelet --version || true
  exit 0
fi

echo "==> install containerd"
sudo apt-get \$APT_OPTS install -y containerd
sudo mkdir -p /etc/containerd
if [[ ! -f /etc/containerd/config.toml ]]; then
  containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
fi
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl enable --now containerd

echo "==> install kubelet kubeadm kubectl (${K8S_VERSION}.x)"
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" \\
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" \\
  | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get \$APT_OPTS update
sudo apt-get \$APT_OPTS install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
sudo systemctl enable kubelet
kubeadm version -o short
EOF
}

kubeadm_init_cp() {
  local host="$1" api_ip="$2"
  run_remote_script "$host" <<EOF
set -euo pipefail
if [[ -f /etc/kubernetes/admin.conf ]]; then
  echo 'Cluster already initialized; skipping kubeadm init'
  exit 0
fi
echo "==> kubeadm init (apiserver ${api_ip}, pods ${POD_NETWORK_CIDR})"
sudo kubeadm init \\
  --pod-network-cidr='${POD_NETWORK_CIDR}' \\
  --apiserver-advertise-address='${api_ip}'
EOF
}

setup_remote_kubeconfig() {
  local host="$1" cluster="$2"
  run_remote_script "$host" <<'EOF'
set -euo pipefail
echo "==> install kubeconfig (~/.kube/config)"
mkdir -p "$HOME/.kube"
sudo cp -f /etc/kubernetes/admin.conf "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
chmod 600 "$HOME/.kube/config"
EOF
  scp_cmd -q "$RENAME_SH" "${host}:/tmp/rename.sh"
  run_remote_script "$host" <<EOF
set -euo pipefail
echo "==> rename kubeconfig context to ${cluster}"
chmod +x /tmp/rename.sh
/tmp/rename.sh '${cluster}' '${cluster}' "\$HOME/.kube/config"
rm -f /tmp/rename.sh
kubectl cluster-info
EOF
}

install_flannel() {
  local host="$1"
  ensure_cni_prereqs "$host" || return 1
  remote_kubectl "$host" apply -f "$FLANNEL_MANIFEST"
}

wait_flannel_ready() {
  local host="$1"
  local node_count running_count total_count

  echo "==> [${host}] wait for Flannel on all nodes"
  for _ in $(seq 1 60); do
    node_count="$(ssh_cmd -o RequestTTY=no "$host" \
      "kubectl get nodes --no-headers 2>/dev/null | wc -l")"
    total_count="$(ssh_cmd -o RequestTTY=no "$host" \
      "kubectl get pods -n kube-flannel --no-headers 2>/dev/null | wc -l")"
    running_count="$(ssh_cmd -o RequestTTY=no "$host" \
      "kubectl get pods -n kube-flannel --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l")"
    if [[ "$node_count" -gt 0 && "$running_count" -ge "$node_count" && "$total_count" -ge "$node_count" ]]; then
      remote_kubectl "$host" get pods -n kube-flannel -o wide
      return 0
    fi
    sleep 5
  done
  echo "warning: Flannel not Running on all nodes within timeout" >&2
  remote_kubectl "$host" get pods -n kube-flannel -o wide || true
  return 1
}

restart_flannel() {
  local host="$1"
  echo "==> [${host}] restart Flannel pods"
  remote_kubectl "$host" delete pods -n kube-flannel --all --ignore-not-found=true || true
  wait_flannel_ready "$host" || true
}

wait_node_ready() {
  local host="$1" node_name="$2"
  for _ in $(seq 1 60); do
    if remote_kubectl "$host" get node "$node_name" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True; then
      return 0
    fi
    sleep 5
  done
  echo "warning: ${node_name} not Ready within timeout" >&2
  return 1
}

fetch_join_command() {
  local cp_host="$1"
  local join_cmd
  echo ">>> [${cp_host}] kubeadm token create --print-join-command" >&2
  join_cmd="$(ssh_cmd "$cp_host" "sudo -n kubeadm token create --print-join-command")"
  echo ">>> join command: ${join_cmd}" >&2
  [[ -n "$join_cmd" ]] || { echo "error: empty join command from ${cp_host}" >&2; exit 1; }
  printf '%s' "$join_cmd"
}

kubeadm_join_worker() {
  local worker_host="$1" cp_host="$2" node_name="$3" node_ip="$4"
  if ssh_cmd "$worker_host" "test -f /etc/kubernetes/kubelet.conf" 2>/dev/null; then
    echo ">>> [${worker_host}] already joined; skipping"
    return 0
  fi
  local join_cmd
  join_cmd="$(fetch_join_command "$cp_host")"
  ACTIVE_HOST="$worker_host"
  HAS_NOPASSWD=1
  echo ">>> [${worker_host}] kubeadm join"
  remote_sudo "$join_cmd"
}

copy_local_kubeconfig() {
  local host="$1" cluster="$2"
  local kcfg local_path
  kcfg="$(kubeconfig_file "$cluster")"
  local_path="${HOME}/.kube/${kcfg}"
  mkdir -p "${HOME}/.kube"
  scp_cmd -q "${host}:.kube/config" "$local_path"
  chmod 600 "$local_path"
  "$RENAME_SH" "$cluster" "$cluster" "$local_path"
}

bringup_cluster() {
  local cluster="$1"
  local cp_host worker_host api_ip worker_ip ctx
  cp_host="${CLUSTER_CP_HOST[$cluster]}"
  worker_host="${CLUSTER_WORKER_HOST[$cluster]}"
  api_ip="${CLUSTER_API_IP[$cluster]}"
  worker_ip="${CLUSTER_WORKER_IP[$cluster]}"
  ctx="$(kube_context "$cluster")"

  echo
  echo "========================================"
  echo " Cluster: ${cluster}"
  echo " Control plane: ${cp_host} (${api_ip})"
  echo " Worker:        ${worker_host} (${worker_ip})"
  echo "========================================"

  echo "==> [${cluster}] Control plane prep"
  prompt_sudo_password "$cp_host"
  ensure_passwordless_sudo "$cp_host"
  host_prep "$cp_host" "$cp_host" "$api_ip"
  install_kubernetes "$cp_host" || return 1

  echo "==> [${cluster}] kubeadm init"
  kubeadm_init_cp "$cp_host" "$api_ip" || return 1
  setup_remote_kubeconfig "$cp_host" "$cluster" || return 1
  install_flannel "$cp_host" || return 1
  wait_flannel_ready "$cp_host" || true
  wait_node_ready "$cp_host" "$cp_host" || true
  remote_kubectl "$cp_host" get nodes -o wide

  echo "==> [${cluster}] Worker join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep "$worker_host" "$worker_host" "$worker_ip"
  install_kubernetes "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$worker_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  echo "==> [${cluster}] CNI prerequisites and Flannel on all nodes"
  ensure_cni_prereqs "$cp_host" || return 1
  ensure_cni_prereqs "$worker_host" || return 1
  restart_flannel "$cp_host"

  remote_kubectl "$cp_host" get nodes -o wide

  if [[ "$SKIP_LOCAL_KUBECONFIG" != "1" ]]; then
    echo "==> [${cluster}] Copy kubeconfig locally"
    copy_local_kubeconfig "$cp_host" "$cluster"
    echo "    Local context: ${ctx}"
  fi

  if [[ "$INSTALL_DASHBOARD" == "1" ]]; then
    echo "==> [${cluster}] MetalLB + Kubernetes Dashboard"
    "$SCRIPT_DIR/install_dashboard.sh" "$cluster" || return 1
  fi

  echo "==> [${cluster}] Done"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG" >&2
  exit 1
fi

if [[ ! -x "$RENAME_SH" ]]; then
  echo "error: rename.sh not found: $RENAME_SH" >&2
  exit 1
fi

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_CLUSTERS[@]}")
else
  for c in "$@"; do
    if [[ -z "${CLUSTER_CP_HOST[$c]:-}" ]]; then
      echo "error: unknown cluster '${c}' (expected central, regional, edge, or ue)" >&2
      exit 1
    fi
    clusters+=("$c")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! bringup_cluster "$cluster"; then
    failed=1
  fi
done

echo
echo "Kubeconfig contexts:"
for cluster in "${clusters[@]}"; do
  echo "  $(kube_context "$cluster")  ->  ~/.kube/$(kubeconfig_file "$cluster")"
done

if [[ ${#clusters[@]} -gt 0 ]]; then
  kcfg_paths=( )
  [[ -f "${HOME}/.kube/config" ]] && kcfg_paths+=("${HOME}/.kube/config")
  [[ -f "${HOME}/.kube/config-central" ]] && kcfg_paths+=("${HOME}/.kube/config-central")
  for cluster in "${clusters[@]}"; do
    kcfg_paths+=("${HOME}/.kube/$(kubeconfig_file "$cluster")")
  done
  echo
  echo "Example:"
  echo "  export KUBECONFIG=$(IFS=:; echo "${kcfg_paths[*]}")"
  echo "  kubectl config use-context $(kube_context "${clusters[0]}")"
fi

exit "$failed"
