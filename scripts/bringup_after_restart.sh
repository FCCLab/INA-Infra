#!/usr/bin/env bash
# Restore Kubernetes CNI after testbed host reboot.
#
# Fixes the common post-reboot failure chain:
#   missing Flannel ClusterRole → Flannel CrashLoopBackOff → no subnet.env →
#   CoreDNS Unknown / ContainerCreating.
#
# Run from the operator machine (SSH to each cluster control plane).
#
#   ./scripts/bringup_after_restart.sh
#   ./scripts/bringup_after_restart.sh regional edge
#   ./scripts/bringup_after_restart.sh -n mgmt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_OPTS=(-F "$SSH_CONFIG" -o ConnectTimeout=10 -o RequestTTY=no)
POD_WAIT="${POD_WAIT:-30}"
DRY_RUN=0

ALL_RESTART_CLUSTERS=(mgmt "${ALL_CLUSTERS[@]}")

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Restore Flannel CNI after host reboot (default: mgmt, central, regional, edge).

Per cluster (via SSH to control plane):
  1. Ensure br_netfilter on control-plane and worker nodes
  2. mgmt only: kubelet --hostname-override=node-0 on mgmt-0
  3. Apply Flannel ClusterRole + ClusterRoleBinding (if missing)
  4. Restart kube-flannel and CoreDNS pods
  5. Print node / Flannel / CoreDNS status

Options:
  -n, --dry-run   Print actions only
  -h, --help      Show this help

Examples:
  $(basename "$0")
  $(basename "$0") regional
  $(basename "$0") -n central regional
EOF
}

ssh_host() {
  ssh "${SSH_OPTS[@]}" "$@"
}

cluster_hosts() {
  local cluster="$1"
  if [[ "$cluster" == "mgmt" ]]; then
    printf '%s\n' "$MGMT_CP_HOST" "$MGMT_WORKER_HOST"
  else
    printf '%s\n' "${CLUSTER_CP_HOST[$cluster]}" "${CLUSTER_WORKER_HOST[$cluster]}"
  fi
}

validate_cluster() {
  local cluster="$1" c
  for c in "${ALL_RESTART_CLUSTERS[@]}"; do
    [[ "$c" == "$cluster" ]] && return 0
  done
  echo "error: unknown cluster '${cluster}' (expected: ${ALL_RESTART_CLUSTERS[*]})" >&2
  return 1
}

remote_kubectl() {
  local host="$1"
  shift
  ssh_host "$host" "kubectl $(printf '%q ' "$@")"
}

ensure_br_netfilter() {
  local host="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: [${host}] modprobe br_netfilter"
    return 0
  fi
  ssh_host "$host" 'echo br_netfilter | sudo tee /etc/modules-load.d/br_netfilter.conf >/dev/null
sudo modprobe br_netfilter 2>/dev/null || true
if [[ -f /proc/sys/net/bridge/bridge-nf-call-iptables ]]; then
  sudo sysctl -w net.bridge.bridge-nf-call-iptables=1 >/dev/null
fi'
}

fix_mgmt_kubelet_hostname() {
  local host="$1"
  [[ "$host" == "$MGMT_CP_HOST" || "$host" == mgmt-0 ]] || return 0

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: [${host}] kubelet --hostname-override=node-0"
    return 0
  fi

  ssh_host "$host" 'h=$(hostname)
if [[ "$h" != "mgmt-0" ]]; then
  exit 0
fi
if grep -q "hostname-override=node-0" /etc/default/kubelet 2>/dev/null; then
  exit 0
fi
echo "KUBELET_EXTRA_ARGS=--hostname-override=node-0" | sudo tee /etc/default/kubelet >/dev/null
sudo systemctl restart kubelet
sleep 5'
}

apply_flannel_rbac() {
  local host="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: [${host}] apply Flannel ClusterRole + ClusterRoleBinding"
    return 0
  fi
  remote_kubectl "$host" apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  labels:
    k8s-app: flannel
  name: flannel
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get"]
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["nodes/status"]
  verbs: ["patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  labels:
    k8s-app: flannel
  name: flannel
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: flannel
subjects:
- kind: ServiceAccount
  name: flannel
  namespace: kube-flannel
EOF
}

restart_cni_pods() {
  local host="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: [${host}] delete kube-flannel + coredns pods"
    return 0
  fi
  remote_kubectl "$host" delete pods -n kube-flannel --all \
    --grace-period=0 --force --ignore-not-found 2>/dev/null || true
  remote_kubectl "$host" delete pods -n kube-system -l k8s-app=kube-dns \
    --grace-period=0 --force --ignore-not-found 2>/dev/null || true
  sleep "$POD_WAIT"
}

show_cluster_status() {
  local cluster="$1" host="$2"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  echo "  nodes:"
  remote_kubectl "$host" get nodes -o wide 2>/dev/null || true
  echo "  flannel:"
  remote_kubectl "$host" get pods -n kube-flannel -o wide 2>/dev/null || true
  echo "  coredns:"
  remote_kubectl "$host" get pods -n kube-system -l k8s-app=kube-dns -o wide 2>/dev/null || true
  echo "  subnet.env (${host}):"
  ssh_host "$host" 'ls -la /run/flannel/subnet.env 2>&1' || true
  echo "  not Running:"
  remote_kubectl "$host" get pods -A \
    --field-selector=status.phase!=Running,status.phase!=Succeeded \
    --no-headers 2>/dev/null | head -10 || echo "    (none)"
}

restore_cluster() {
  local cluster="$1"
  local host worker
  host="$(cluster_cp_host "$cluster")"

  echo "==> [${cluster}] restore CNI via ${host}"

  if ! ssh_host "$host" "echo ok" >/dev/null 2>&1; then
    echo "error: [${cluster}] cannot SSH to ${host}" >&2
    return 1
  fi

  while IFS= read -r worker; do
    [[ -z "$worker" ]] && continue
    echo "  [${worker}] br_netfilter"
    ensure_br_netfilter "$worker"
    fix_mgmt_kubelet_hostname "$worker"
  done < <(cluster_hosts "$cluster")

  echo "  [${host}] Flannel RBAC"
  apply_flannel_rbac "$host"

  echo "  [${host}] restart Flannel + CoreDNS"
  restart_cni_pods "$host"

  show_cluster_status "$cluster" "$host"
  echo
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_RESTART_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    clusters+=("$cluster")
  done
fi

if [[ ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: ${SSH_CONFIG}" >&2
  exit 1
fi

failed=0
for cluster in "${clusters[@]}"; do
  if ! restore_cluster "$cluster"; then
    failed=1
  fi
done

if [[ "$DRY_RUN" != "1" && "$failed" -eq 0 ]]; then
  echo "Done. Long-term fix: ./scripts/render_flannel_gitops.sh && ./bringup/03_push_to_git_repos/push_git_repos.sh"
fi

exit "$failed"
