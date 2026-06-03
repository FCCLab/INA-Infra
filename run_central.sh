#!/usr/bin/env bash
# Run a command on the central cluster host (10.1.101.22) via SSH.
set -euo pipefail

CENTRAL_SSH="${CENTRAL_SSH:-fcp@10.1.101.22}"
KUBE_CONTEXT="${KUBE_CONTEXT:-central@central}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [command [args...]]

Run a command on the central kubeadm node via SSH.
With no arguments, opens an interactive shell on central.

If the command is kubectl, sets KUBECONFIG and --context on the remote host.

Environment:
  CENTRAL_SSH         SSH target (default: fcp@10.1.101.22)
  KUBE_CONTEXT        kubectl context (default: central@central)

Examples:
  $(basename "$0")
  $(basename "$0") ifconfig
  $(basename "$0") sudo modprobe br_netfilter
  $(basename "$0") kubectl get pods -A
  $(basename "$0") kubectl delete pod -n kube-flannel -l app=flannel
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

remote_cmd() {
  local q
  q="$(printf '%q ' "$@")"
  ssh -t "$CENTRAL_SSH" bash -lc "$q"
}

if [[ $# -eq 0 ]]; then
  exec ssh -t "$CENTRAL_SSH"
fi

if [[ "$1" == "kubectl" ]]; then
  shift
  kcmd="$(printf '%q ' "$@")"
  remote_cmd "export KUBECONFIG=\"\$HOME/.kube/config-central\"; kubectl --context=$(printf '%q' "$KUBE_CONTEXT") ${kcmd}"
else
  remote_cmd "$@"
fi
