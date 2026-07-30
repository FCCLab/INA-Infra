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
NETPLAN_SITE="${NETPLAN_SITE:-60-nephio.yaml}"
SKIP_LOCAL_KUBECONFIG="${SKIP_LOCAL_KUBECONFIG:-0}"
SSH_USER="${SSH_USER:-fcp}"
INSTALL_FLANNEL="${INSTALL_FLANNEL:-0}"
JOIN_WORKER_ONLY=0

SUDO_PASSWORD="${SUDO_PASSWORD:-}"
ACTIVE_HOST=""
HAS_NOPASSWD=0

cleanup() {
  unset SUDO_PASSWORD
}
trap cleanup EXIT

usage() {
  cat <<EOF
Usage: $(basename "$0") [--join] [cluster ...]

Bring up Kubernetes on mgmt and/or workload clusters (central, regional, edge, ue).
Mgmt uses 10.1.132/24 only (${MGMT_CP_HOST} ${MGMT_API_IP}). Workload sites use site
netplan (10.1.137/24), kubeadm on site IPs, Flannel on ${SITE_IFACE}. SSH on all nodes
stays on 10.1.132/24 (enp1s0).

With no arguments, brings up all four workload clusters (central, regional, edge, ue).

Options:
  --join    Join worker node(s) only (control plane must already be running)

Examples:
  $(basename "$0")
  $(basename "$0") mgmt
  $(basename "$0") mgmt central regional
  $(basename "$0") regional
  $(basename "$0") --join edge
  $(basename "$0") --join mgmt

Clusters (SSH / cluster API / dashboard):
  mgmt       ${MGMT_CP_HOST} ${MGMT_API_IP}  dashboard MetalLB ${MGMT_DASHBOARD_VIP}
  central    ${CLUSTER_CP_HOST[central]} ${CLUSTER_MGMT_IP[central]} / ${CLUSTER_API_IP[central]}  dashboard NodePort :30443
  regional   ${CLUSTER_CP_HOST[regional]} ${CLUSTER_MGMT_IP[regional]} / ${CLUSTER_API_IP[regional]}  dashboard NodePort :30443
  edge       ${CLUSTER_CP_HOST[edge]} ${CLUSTER_MGMT_IP[edge]} / ${CLUSTER_API_IP[edge]}  dashboard NodePort :30443
  ue         ${CLUSTER_CP_HOST[ue]} ${CLUSTER_MGMT_IP[ue]} / ${CLUSTER_API_IP[ue]}  dashboard NodePort :30443

Environment:
  SSH_CONFIG              SSH config (default: utils/ssh_config/config)
  SITE_IFACE              Cluster/data-plane NIC (default: enp7s0)
  K8S_VERSION             Kubernetes minor version (default: 1.31)
  POD_NETWORK_CIDR        Flannel CIDR (default: 10.244.0.0/16)
  DNS_SERVER              Resolver for apt on remote hosts (default: 10.1.132.200)
  SKIP_LOCAL_KUBECONFIG   Set to 1 to skip copying kubeconfigs to local ~/.kube
  INSTALL_FLANNEL         Set to 1 to install Flannel CNI (default: 0, use GitOps)
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

kubectl_on_remote() {
  local host="$1"
  shift
  ssh_cmd -o RequestTTY=no "$host" "kubectl $(printf '%q ' "$@")"
}

is_cluster_cp_host() {
  local name="$1"
  [[ "$name" == "$MGMT_CP_HOST" ]] && return 0
  local cluster
  for cluster in "${ALL_CLUSTERS[@]}"; do
    [[ "${CLUSTER_CP_HOST[$cluster]}" == "$name" ]] && return 0
  done
  return 1
}

is_cluster_worker_host() {
  local name="$1"
  [[ "$name" == "$MGMT_WORKER_HOST" ]] && return 0
  local cluster
  for cluster in "${ALL_CLUSTERS[@]}"; do
    [[ "${CLUSTER_WORKER_HOST[$cluster]}" == "$name" ]] && return 0
  done
  return 1
}

k8s_node_for_ip() {
  local cp_host="$1" ip="$2"
  kubectl_on_remote "$cp_host" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .status.addresses[*]}{.address}{" "}{end}{"\n"}{end}' \
    | awk -v ip="$ip" '{
        name = $1
        for (i = 2; i <= NF; i++) if ($i == ip) { print name; exit }
      }'
}

resolve_k8s_node_name() {
  local cp_host="$1" logical_name="$2"
  local node_ip k8s_name ip

  if kubectl_on_remote "$cp_host" get node "$logical_name" &>/dev/null; then
    printf '%s' "$logical_name"
    return 0
  fi

  for ip in "$(cluster_k8s_node_ip "$logical_name" 2>/dev/null)" \
            "$(cluster_mgmt_ip "$logical_name" 2>/dev/null)"; do
    [[ -z "$ip" || "$ip" == "$logical_name" ]] && continue
    k8s_name="$(k8s_node_for_ip "$cp_host" "$ip")"
    if [[ -n "$k8s_name" ]]; then
      if [[ "$k8s_name" != "$logical_name" ]]; then
        echo "note: [${cp_host}] node ${logical_name} registered as ${k8s_name} (address ${ip})" >&2
      fi
      printf '%s' "$k8s_name"
      return 0
    fi
  done

  if is_cluster_cp_host "$logical_name"; then
    k8s_name="$(kubectl_on_remote "$cp_host" get nodes \
      -l 'node-role.kubernetes.io/control-plane' \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    if [[ -n "$k8s_name" ]]; then
      echo "note: [${cp_host}] using control-plane node ${k8s_name} for ${logical_name} (no name/IP match; reset cluster if stale)" >&2
      printf '%s' "$k8s_name"
      return 0
    fi
  elif is_cluster_worker_host "$logical_name"; then
    k8s_name="$(kubectl_on_remote "$cp_host" get nodes \
      -l '!node-role.kubernetes.io/control-plane' \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    if [[ -n "$k8s_name" ]]; then
      echo "note: [${cp_host}] using worker node ${k8s_name} for ${logical_name} (no name/IP match; reset cluster if stale)" >&2
      printf '%s' "$k8s_name"
      return 0
    fi
  fi

  printf '%s' "$logical_name"
  return 1
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
    echo "==> [${host}] no ${mgmt_file}; skipping mgmt netplan"
    return 0
  fi

  scp_cmd "$mgmt_file" "${host}:${remote_file}"
  run_remote_script "$host" <<EOF
set -euo pipefail
echo "==> apply mgmt netplan (default via ${MGMT_GATEWAY})"
sudo install -m 600 '${remote_file}' /etc/netplan/55-nephio-mgmt.yaml
rm -f '${remote_file}'
sudo bash -lc 'chmod 600 /etc/netplan/*.yaml 2>/dev/null || true'
sudo netplan apply
echo "==> verify default route"
ip -4 route show default
getent ahostsv4 archive.ubuntu.com || echo "warning: DNS lookup failed (continuing)"
EOF
}

configure_site_network() {
  local host="$1"
  local site_file="${NETPLAN_DIR}/${host}/${NETPLAN_SITE}"
  local remote_file="/tmp/${NETPLAN_SITE}.$$"

  if [[ ! -f "$site_file" ]]; then
    echo "error: missing ${site_file}" >&2
    return 1
  fi

  scp_cmd "$site_file" "${host}:${remote_file}"
  run_remote_script "$host" <<EOF
set -euo pipefail
echo "==> apply site netplan (${SITE_IFACE}, 10.1.137.0/24)"
sudo install -m 600 '${remote_file}' /etc/netplan/${NETPLAN_SITE}
rm -f '${remote_file}'
sudo bash -lc 'chmod 600 /etc/netplan/*.yaml 2>/dev/null || true'
sudo netplan apply
ip -4 -br addr show ${SITE_IFACE} || true
EOF
}

host_prep_workload() {
  local host="$1" node_name="$2" node_ip="$3"
  configure_mgmt_network "$host"
  configure_site_network "$host"
  host_prep_common "$host" "$node_name" "$node_ip"
}

host_prep_mgmt() {
  local host="$1" node_name="$2" node_ip="$3"
  configure_mgmt_network "$host"
  host_prep_common "$host" "$node_name" "$node_ip"
}

host_prep_common() {
  local host="$1" node_name="$2" node_ip="$3"
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
echo "==> configure KUBECONFIG in ~/.bashrc and ~/.zshrc"
for file in "\$HOME/.bashrc" "\$HOME/.zshrc"; do
  if [[ -f "\$file" ]]; then
    if ! grep -q "KUBECONFIG" "\$file"; then
      echo "" >> "\$file"
      echo 'export KUBECONFIG="\${HOME}/.kube/config:\${HOME}/.kube/config-central:\${HOME}/.kube/config-regional:\${HOME}/.kube/config-edge:\${HOME}/.kube/config-ue"' >> "\$file"
      echo "    added KUBECONFIG to \$file"
    fi
  fi
done
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

configure_containerd_for_k8s_remote_script() {
  cat <<'EOF'
echo "==> configure containerd CRI for Kubernetes (keep Docker CE)"
sudo mkdir -p /etc/containerd
if [[ ! -f /etc/containerd/config.toml ]] || grep -q 'disabled_plugins = \["cri"\]' /etc/containerd/config.toml; then
  if [[ -f /etc/containerd/config.toml ]]; then
    sudo cp -a /etc/containerd/config.toml "/etc/containerd/config.toml.bak.$(date +%Y%m%d%H%M%S)"
  fi
  containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
fi
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo sed -i 's/disabled_plugins = \["cri"\]/disabled_plugins = []/' /etc/containerd/config.toml
# Lab registry (mgmt) uses a self-signed cert — skip verify for pulls.
# Covers containerd config v2 (cri.grpc) and v3 (cri.v1.images), including imports = [].
REGISTRY="${LAB_REGISTRY:-10.1.132.30:5000}"
sudo mkdir -p "/etc/containerd/certs.d/${REGISTRY}" /etc/containerd/conf.d
sudo tee "/etc/containerd/certs.d/${REGISTRY}/hosts.toml" >/dev/null <<EOR
server = "https://${REGISTRY}"

[host."https://${REGISTRY}"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
EOR
sudo tee /etc/containerd/conf.d/registry-certs.d.toml >/dev/null <<'EOR'
[plugins.'io.containerd.cri.v1.images'.registry]
  config_path = "/etc/containerd/certs.d"

[plugins."io.containerd.grpc.v1.cri".registry]
  config_path = "/etc/containerd/certs.d"
EOR
if grep -qE '^\s*imports\s*=\s*\[\s*\]\s*$' /etc/containerd/config.toml 2>/dev/null; then
  sudo sed -i "s|^[[:space:]]*imports[[:space:]]*=[[:space:]]*\\[\\][[:space:]]*$|imports = ['/etc/containerd/conf.d/*.toml']|" /etc/containerd/config.toml
elif ! grep -qE "imports\s*=\s*\[.*/etc/containerd/conf\.d" /etc/containerd/config.toml 2>/dev/null; then
  if grep -qE '^\s*imports\s*=' /etc/containerd/config.toml 2>/dev/null; then
    sudo sed -i "s|^[[:space:]]*imports[[:space:]]*=.*$|imports = ['/etc/containerd/conf.d/*.toml']|" /etc/containerd/config.toml
  else
    tmp="$(mktemp)"
    if head -1 /etc/containerd/config.toml | grep -qE '^\s*version\s*='; then
      { head -1 /etc/containerd/config.toml; echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; tail -n +2 /etc/containerd/config.toml; } >"$tmp"
    else
      { echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; cat /etc/containerd/config.toml; } >"$tmp"
    fi
    sudo mv "$tmp" /etc/containerd/config.toml
  fi
fi
sudo sed -i 's|config_path = '\'''\''|config_path = "/etc/containerd/certs.d"|' /etc/containerd/config.toml || true
sudo sed -i 's|config_path = ""|config_path = "/etc/containerd/certs.d"|' /etc/containerd/config.toml || true
# Docker CE (kept on external workers) also needs insecure-registries for pushes/pulls.
if command -v docker >/dev/null 2>&1; then
  sudo mkdir -p /etc/docker
  if command -v jq >/dev/null 2>&1 && [[ -f /etc/docker/daemon.json ]]; then
    tmp="$(mktemp)"
    sudo jq --arg reg "$REGISTRY" '
      .["insecure-registries"] = (
        (.["insecure-registries"] // [])
        | if index($reg) then . else . + [$reg] end
        | unique
      )
    ' /etc/docker/daemon.json >"$tmp"
    sudo mv "$tmp" /etc/docker/daemon.json
  else
    sudo tee /etc/docker/daemon.json >/dev/null <<EOD
{
  "insecure-registries": ["${REGISTRY}"]
}
EOD
  fi
  sudo systemctl restart docker 2>/dev/null || true
fi
sudo systemctl enable --now containerd
sudo systemctl restart containerd
EOF
}

ensure_docker_ce_remote_script() {
  cat <<'EOF'
DOCKER_PKGS=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)

if command -v docker >/dev/null && dpkg -l docker-ce 2>/dev/null | grep -q '^ii'; then
  echo "==> Docker CE already installed; keeping current packages"
  sudo systemctl enable --now docker 2>/dev/null || true
else
  echo "==> install Docker CE (containerd.io from docker.com)"
  # Drop stale/conflicting docker apt sources (list vs sources, bad Signed-By).
  sudo rm -f /etc/apt/sources.list.d/docker.list \
             /etc/apt/sources.list.d/docker.sources
  sudo install -m 0755 -d /etc/apt/keyrings
  # Official key is ASCII-armored; do not --dearmor into *.asc (leaves apt without PUBKEY).
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOA
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOA
  sudo apt-get $APT_OPTS update
  sudo apt-get $APT_OPTS install -y "${DOCKER_PKGS[@]}"
  sudo systemctl enable --now docker
  sudo systemctl enable --now containerd
fi
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
# Release.key is ASCII-armored; avoid gpg --dearmor (needs /dev/tty under non-interactive sudo).
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" \\
  | sudo tee /etc/apt/keyrings/kubernetes-apt-keyring.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/kubernetes-apt-keyring.asc
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.asc] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" \\
  | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get \$APT_OPTS update
sudo apt-get \$APT_OPTS install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
sudo systemctl enable kubelet
kubeadm version -o short
EOF
}

install_kubernetes_keep_docker() {
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
  $(ensure_docker_ce_remote_script)
  $(configure_containerd_for_k8s_remote_script)
  exit 0
fi

$(ensure_docker_ce_remote_script)
$(configure_containerd_for_k8s_remote_script)

echo "==> install kubelet kubeadm kubectl (${K8S_VERSION}.x)"
sudo install -d -m 0755 /etc/apt/keyrings
# Release.key is ASCII-armored; avoid gpg --dearmor (needs /dev/tty under non-interactive sudo).
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" \\
  | sudo tee /etc/apt/keyrings/kubernetes-apt-keyring.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/kubernetes-apt-keyring.asc
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.asc] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" \\
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
  # Optional mgmt IP (enp1s0) — included in apiserver cert SANs so operator hosts
  # can reach the API via 10.1.132.0/24 when site-IP hairpin TLS fails.
  local mgmt_ip="${3:-}"
  local cert_sans="\"${api_ip}\""
  if [[ -n "$mgmt_ip" && "$mgmt_ip" != "$api_ip" ]]; then
    cert_sans+=$'\n'"  - \"${mgmt_ip}\""
  fi
  run_remote_script "$host" <<EOF
set -euo pipefail
if [[ -f /etc/kubernetes/admin.conf ]]; then
  echo 'Cluster already initialized; skipping kubeadm init'
  echo 'Existing nodes (reset with ./scripts/reset_clusters.sh -y mgmt if stale):'
  sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide 2>/dev/null || true
  exit 0
fi
echo "==> kubeadm init (apiserver ${api_ip}, node-ip ${api_ip}, pods ${POD_NETWORK_CIDR})"
cat > /tmp/kubeadm-init.yaml <<INIT
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: "${api_ip}"
nodeRegistration:
  kubeletExtraArgs:
  - name: node-ip
    value: "${api_ip}"
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
networking:
  podSubnet: "${POD_NETWORK_CIDR}"
apiServer:
  certSANs:
  - ${cert_sans}
INIT
sudo kubeadm init --config /tmp/kubeadm-init.yaml
rm -f /tmp/kubeadm-init.yaml
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

install_flannel_workload() {
  install_flannel "$1"
  configure_flannel_site_iface "$1"
}

configure_flannel_site_iface() {
  local host="$1"
  echo "==> [${host}] configure Flannel to use ${SITE_IFACE}"
  remote_kubectl "$host" patch daemonset kube-flannel-ds -n kube-flannel --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--iface='${SITE_IFACE}'"}]' \
    2>/dev/null || true
  remote_kubectl "$host" rollout status daemonset/kube-flannel-ds -n kube-flannel --timeout=120s \
    2>/dev/null || true
}

wait_flannel_ready() {
  local host="$1"
  local node_count running_count total_count

  echo "==> [${host}] wait for Flannel on all nodes"
  for attempt in $(seq 1 60); do
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
    if (( attempt % 6 == 0 )); then
      echo "    Flannel: ${running_count}/${node_count} Running (${attempt}/60)..." >&2
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
  local k8s_node
  k8s_node="$(resolve_k8s_node_name "$host" "$node_name")"

  echo "==> [${host}] wait for node ${node_name} (${k8s_node}) Ready"
  for attempt in $(seq 1 60); do
    if remote_kubectl "$host" get node "$k8s_node" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True; then
      return 0
    fi
    if (( attempt % 6 == 0 )); then
      echo "    still waiting for ${k8s_node} (${attempt}/60)..." >&2
    fi
    sleep 5
  done
  echo "warning: ${node_name} (${k8s_node}) not Ready within timeout" >&2
  return 1
}

fetch_join_params() {
  local cp_host="$1"
  local join_cmd api token hash
  echo ">>> [${cp_host}] kubeadm token create --print-join-command" >&2
  join_cmd="$(ssh_cmd "$cp_host" "sudo -n kubeadm token create --print-join-command")"
  echo ">>> join command: ${join_cmd}" >&2
  [[ -n "$join_cmd" ]] || { echo "error: empty join command from ${cp_host}" >&2; return 1; }

  api="$(sed -n 's/^kubeadm join \([^[:space:]]*\).*/\1/p' <<< "$join_cmd")"
  token="$(sed -n 's/.*--token \([^[:space:]]*\).*/\1/p' <<< "$join_cmd")"
  hash="$(sed -n 's/.*--discovery-token-ca-cert-hash \([^[:space:]]*\).*/\1/p' <<< "$join_cmd")"

  if [[ -z "$api" || -z "$token" || -z "$hash" ]]; then
    echo "error: could not parse join command from ${cp_host}" >&2
    return 1
  fi

  JOIN_API="$api"
  JOIN_TOKEN="$token"
  JOIN_CA_HASH="$hash"
}

kubeadm_join_worker() {
  local worker_host="$1" cp_host="$2" node_name="$3" node_ip="$4"
  local JOIN_API JOIN_TOKEN JOIN_CA_HASH
  if ssh_cmd "$worker_host" "test -f /etc/kubernetes/kubelet.conf" 2>/dev/null; then
    echo ">>> [${worker_host}] already joined; skipping"
    return 0
  fi
  fetch_join_params "$cp_host" || return 1
  echo ">>> [${worker_host}] kubeadm join (node-ip ${node_ip})"
  run_remote_script "$worker_host" <<EOF
set -euo pipefail
cat > /tmp/kubeadm-join.yaml <<JOIN
apiVersion: kubeadm.k8s.io/v1beta4
kind: JoinConfiguration
discovery:
  bootstrapToken:
    apiServerEndpoint: ${JOIN_API}
    token: "${JOIN_TOKEN}"
    caCertHashes:
    - "${JOIN_CA_HASH}"
nodeRegistration:
  kubeletExtraArgs:
  - name: node-ip
    value: "${node_ip}"
JOIN
sudo kubeadm join --config /tmp/kubeadm-join.yaml
rm -f /tmp/kubeadm-join.yaml
EOF
}

ensure_kubeconfig_profile_export() {
  local kcfg_export='export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge:${HOME}/.kube/config-ue"'
  local file
  for file in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    if [[ -f "$file" ]]; then
      if ! grep -q "KUBECONFIG" "$file"; then
        echo "" >> "$file"
        echo "$kcfg_export" >> "$file"
        echo "==> Auto-added KUBECONFIG export to $file"
      fi
    fi
  done
}

copy_local_kubeconfig() {
  local host="$1" cluster="$2"
  local local_path api_ip mgmt_ip
  if [[ "$cluster" == "mgmt" ]]; then
    local_path="${HOME}/.kube/config"
  else
    local_path="${HOME}/.kube/$(kubeconfig_file "$cluster")"
  fi
  mkdir -p "${HOME}/.kube"
  scp_cmd -q "${host}:.kube/config" "$local_path"
  chmod 600 "$local_path"
  "$RENAME_SH" "$cluster" "$cluster" "$local_path"
  # Prefer mgmt-plane API URL from operator hosts (site .137 hairpin can break TLS).
  if [[ "$cluster" != "mgmt" ]]; then
    api_ip="$(cluster_api_ip "$cluster")"
    mgmt_ip="$(cluster_mgmt_ip "$cluster")"
    if [[ -n "$api_ip" && -n "$mgmt_ip" && "$api_ip" != "$mgmt_ip" ]]; then
      sed -i "s|https://${api_ip}:6443|https://${mgmt_ip}:6443|g" "$local_path"
    fi
  fi
  ensure_kubeconfig_profile_export
}

bringup_mgmt_cluster() {
  local cluster="mgmt"
  local cp_host worker_host api_ip worker_ip ctx
  cp_host="$MGMT_CP_HOST"
  worker_host="$MGMT_WORKER_HOST"
  api_ip="$MGMT_API_IP"
  worker_ip="$MGMT_WORKER_IP"
  ctx="$(kube_context "$cluster")"

  echo
  echo "========================================"
  echo " Cluster: mgmt (10.1.132/24 only)"
  echo " Control plane: ${cp_host}  ${api_ip}"
  echo " Worker:        ${worker_host}  ${worker_ip}"
  echo " Dashboard:     ${MGMT_DASHBOARD_VIP}"
  echo "========================================"

  echo "==> [mgmt] Control plane prep"
  prompt_sudo_password "$cp_host"
  ensure_passwordless_sudo "$cp_host"
  host_prep_mgmt "$cp_host" "$cp_host" "$api_ip"
  install_kubernetes "$cp_host" || return 1

  echo "==> [mgmt] kubeadm init"
  kubeadm_init_cp "$cp_host" "$api_ip" || return 1
  setup_remote_kubeconfig "$cp_host" "$cluster" || return 1
  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    install_flannel "$cp_host" || return 1
    wait_flannel_ready "$cp_host" || true
  fi
  wait_node_ready "$cp_host" "$cp_host" || true
  remote_kubectl "$cp_host" get nodes -o wide

  echo "==> [mgmt] Worker join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep_mgmt "$worker_host" "$worker_host" "$worker_ip"
  install_kubernetes "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$worker_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    echo "==> [mgmt] CNI prerequisites and Flannel on all nodes"
    ensure_cni_prereqs "$cp_host" || return 1
    ensure_cni_prereqs "$worker_host" || return 1
    restart_flannel "$cp_host"
  fi

  remote_kubectl "$cp_host" get nodes -o wide

  if [[ "$SKIP_LOCAL_KUBECONFIG" != "1" ]]; then
    echo "==> [mgmt] Copy kubeconfig locally"
    copy_local_kubeconfig "$cp_host" "$cluster"
    echo "    Local context: ${ctx}  (~/.kube/config)"
  fi

  echo "==> [mgmt] Done"
}

join_worker_mgmt() {
  local cp_host worker_host worker_ip
  cp_host="$MGMT_CP_HOST"
  worker_host="$MGMT_WORKER_HOST"
  worker_ip="$MGMT_WORKER_IP"

  echo
  echo "========================================"
  echo " Join worker: mgmt"
  echo " Control plane: ${cp_host}  (must be running)"
  echo " Worker:        ${worker_host}  ${worker_ip}"
  echo "========================================"

  if ! ssh_cmd "$cp_host" "test -f /etc/kubernetes/admin.conf" 2>/dev/null; then
    echo "error: mgmt control plane not initialized on ${cp_host}" >&2
    return 1
  fi

  echo "==> [mgmt] Worker join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep_mgmt "$worker_host" "$worker_host" "$worker_ip"
  install_kubernetes "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$worker_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    echo "==> [mgmt] CNI prerequisites and Flannel on worker"
    ensure_cni_prereqs "$cp_host" || return 1
    ensure_cni_prereqs "$worker_host" || return 1
    restart_flannel "$cp_host"
  fi

  remote_kubectl "$cp_host" get nodes -o wide
  echo "==> [mgmt] Worker join done"
}

join_worker_cluster() {
  local cluster="$1"
  local cp_host worker_host worker_ip mgmt_worker_ip
  cp_host="${CLUSTER_CP_HOST[$cluster]}"
  worker_host="${CLUSTER_WORKER_HOST[$cluster]}"
  worker_ip="${CLUSTER_WORKER_IP[$cluster]}"
  mgmt_worker_ip="${CLUSTER_MGMT_WORKER_IP[$cluster]}"

  echo
  echo "========================================"
  echo " Join worker: ${cluster}"
  echo " Control plane: ${cp_host}  (must be running)"
  echo " Worker:        ${worker_host}  SSH ${mgmt_worker_ip}  node ${worker_ip}"
  echo "========================================"

  if ! ssh_cmd "$cp_host" "test -f /etc/kubernetes/admin.conf" 2>/dev/null; then
    echo "error: control plane not initialized on ${cp_host}" >&2
    return 1
  fi

  echo "==> [${cluster}] Worker join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep_workload "$worker_host" "$worker_host" "$worker_ip"
  install_kubernetes "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$worker_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    echo "==> [${cluster}] CNI prerequisites and Flannel on worker"
    ensure_cni_prereqs "$cp_host" || return 1
    ensure_cni_prereqs "$worker_host" || return 1
    restart_flannel "$cp_host"
  fi

  remote_kubectl "$cp_host" get nodes -o wide
  echo "==> [${cluster}] Worker join done"
}

bringup_cluster() {
  local cluster="$1"
  local cp_host worker_host api_ip worker_ip mgmt_cp_ip mgmt_worker_ip ctx
  cp_host="${CLUSTER_CP_HOST[$cluster]}"
  worker_host="${CLUSTER_WORKER_HOST[$cluster]}"
  api_ip="${CLUSTER_API_IP[$cluster]}"
  worker_ip="${CLUSTER_WORKER_IP[$cluster]}"
  mgmt_cp_ip="${CLUSTER_MGMT_IP[$cluster]}"
  mgmt_worker_ip="${CLUSTER_MGMT_WORKER_IP[$cluster]}"
  ctx="$(kube_context "$cluster")"

  echo
  echo "========================================"
  echo " Cluster: ${cluster}"
  echo " Control plane: ${cp_host}  SSH ${mgmt_cp_ip}  API ${api_ip}"
  echo " Worker:        ${worker_host}  SSH ${mgmt_worker_ip}  node ${worker_ip}"
  echo "========================================"

  echo "==> [${cluster}] Control plane prep"
  prompt_sudo_password "$cp_host"
  ensure_passwordless_sudo "$cp_host"
  host_prep_workload "$cp_host" "$cp_host" "$api_ip"
  install_kubernetes "$cp_host" || return 1

  echo "==> [${cluster}] kubeadm init"
  kubeadm_init_cp "$cp_host" "$api_ip" "$mgmt_cp_ip" || return 1
  setup_remote_kubeconfig "$cp_host" "$cluster" || return 1
  # Operator reachability: admin.conf uses mgmt IP (cert SAN includes both).
  run_remote_script "$cp_host" <<EOF
set -euo pipefail
if [[ -f "\$HOME/.kube/config" ]]; then
  sed -i 's|https://${api_ip}:6443|https://${mgmt_cp_ip}:6443|g' "\$HOME/.kube/config"
fi
if sudo test -f /etc/kubernetes/admin.conf; then
  sudo sed -i 's|https://${api_ip}:6443|https://${mgmt_cp_ip}:6443|g' /etc/kubernetes/admin.conf
fi
EOF
  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    install_flannel_workload "$cp_host" || return 1
    wait_flannel_ready "$cp_host" || true
  fi
  wait_node_ready "$cp_host" "$cp_host" || true
  remote_kubectl "$cp_host" get nodes -o wide

  echo "==> [${cluster}] Worker join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep_workload "$worker_host" "$worker_host" "$worker_ip"
  install_kubernetes "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$worker_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    echo "==> [${cluster}] CNI prerequisites and Flannel on all nodes"
    ensure_cni_prereqs "$cp_host" || return 1
    ensure_cni_prereqs "$worker_host" || return 1
    restart_flannel "$cp_host"
  fi

  remote_kubectl "$cp_host" get nodes -o wide

  if [[ "$SKIP_LOCAL_KUBECONFIG" != "1" ]]; then
    echo "==> [${cluster}] Copy kubeconfig locally"
    copy_local_kubeconfig "$cp_host" "$cluster"
    echo "    Local context: ${ctx}"
  fi

  echo "==> [${cluster}] Done"
}

kubeconfig_path_for_cluster() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s' "${HOME}/.kube/config"
  else
    printf '%s' "${HOME}/.kube/$(kubeconfig_file "$cluster")"
  fi
}

bringup_one_cluster() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    if [[ "$JOIN_WORKER_ONLY" == "1" ]]; then
      join_worker_mgmt
    else
      bringup_mgmt_cluster
    fi
  elif [[ "$JOIN_WORKER_ONLY" == "1" ]]; then
    join_worker_cluster "$cluster"
  else
    bringup_cluster "$cluster"
  fi
}

parse_args() {
  local arg
  cluster_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --join)
        JOIN_WORKER_ONLY=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        cluster_args+=("$1")
        shift
        ;;
    esac
  done
}

bringup_cluster_main() {
parse_args "$@"

if [[ "${BRINGUP_MGMT_CLUSTER:-}" == "1" ]]; then
  if [[ "$JOIN_WORKER_ONLY" == "1" ]]; then
    if ! join_worker_mgmt; then
      exit 1
    fi
  else
    if ! bringup_mgmt_cluster; then
      exit 1
    fi
    echo
    echo "Kubeconfig context: mgmt@mgmt  ->  ~/.kube/config"
  fi
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
if [[ ${#cluster_args[@]} -eq 0 ]]; then
  clusters=("${ALL_CLUSTERS[@]}")
else
  for c in "${cluster_args[@]}"; do
    case "$c" in
      mgmt) ;;
      *)
        if [[ -z "${CLUSTER_CP_HOST[$c]:-}" ]]; then
          echo "error: unknown cluster '${c}' (expected mgmt, central, regional, edge, or ue)" >&2
          exit 1
        fi
        ;;
    esac
    clusters+=("$c")
  done
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! bringup_one_cluster "$cluster"; then
    failed=1
  fi
done

if [[ "$JOIN_WORKER_ONLY" == "1" ]]; then
  exit "$failed"
fi

echo
echo "Kubeconfig contexts:"
for cluster in "${clusters[@]}"; do
  echo "  $(kube_context "$cluster")  ->  $(kubeconfig_path_for_cluster "$cluster")"
done

if [[ ${#clusters[@]} -gt 0 ]]; then
  kcfg_paths=( )
  for cluster in "${clusters[@]}"; do
    kcfg="$(kubeconfig_path_for_cluster "$cluster")"
    [[ -f "$kcfg" ]] || continue
    case " ${kcfg_paths[*]:-} " in
      *" $kcfg "*) ;;
      *) kcfg_paths+=("$kcfg") ;;
    esac
  done
  if [[ ${#kcfg_paths[@]} -gt 0 ]]; then
    echo
    echo "Example:"
    echo "  export KUBECONFIG=$(IFS=:; echo "${kcfg_paths[*]}")"
    echo "  kubectl config use-context $(kube_context "${clusters[0]}")"
  fi
fi

ensure_kubeconfig_profile_export
exit "$failed"
}

join_external_worker() {
  local cluster="$1" worker_host="$2" node_ip="$3"
  local cp_host="${CLUSTER_CP_HOST[$cluster]}"

  echo
  echo "========================================"
  echo " Join external worker: ${worker_host} -> ${cluster}"
  echo " Control plane: ${cp_host}  (must be running)"
  echo " Worker:        ${worker_host}  node-ip ${node_ip}"
  echo "========================================"

  if ! ssh_cmd "$cp_host" "test -f /etc/kubernetes/admin.conf" 2>/dev/null; then
    echo "error: control plane not initialized on ${cp_host}" >&2
    return 1
  fi

  echo "==> [${cluster}] External worker prep + join"
  prompt_sudo_password "$worker_host"
  ensure_passwordless_sudo "$worker_host"
  host_prep_common "$worker_host" "$worker_host" "$node_ip"
  install_kubernetes_keep_docker "$worker_host" || return 1
  kubeadm_join_worker "$worker_host" "$cp_host" "$worker_host" "$node_ip" || return 1
  wait_node_ready "$cp_host" "$worker_host" || true

  if [[ "$INSTALL_FLANNEL" == "1" ]]; then
    echo "==> [${cluster}] CNI prerequisites and Flannel on external worker"
    ensure_cni_prereqs "$cp_host" || return 1
    ensure_cni_prereqs "$worker_host" || return 1
    restart_flannel "$cp_host"
  fi

  remote_kubectl "$cp_host" get nodes -o wide
  echo "==> [${cluster}] External worker ${worker_host} join done"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  bringup_cluster_main "$@"
fi
