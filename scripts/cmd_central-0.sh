#!/usr/bin/env bash
# Run a command on the central cluster host via SSH.
set -euo pipefail

CENTRAL_SSH="${CENTRAL_SSH:-fcp@10.1.132.210}"
KUBE_CONTEXT="${KUBE_CONTEXT:-central@central}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [command [args...]]
       $(basename "$0") --script [bash-args...]

Run a command on the central kubeadm node via SSH.
With no arguments, opens an interactive shell on central.

--script runs bash -s on the remote host (read script from stdin).

If the command is kubectl, sets KUBECONFIG and --context on the remote host.

Environment:
  CENTRAL_SSH         SSH target (default: fcp@10.1.132.210)
  KUBE_CONTEXT        kubectl context (default: central@central)

Examples:
  $(basename "$0")
  $(basename "$0") ifconfig
  $(basename "$0") sudo modprobe br_netfilter
  $(basename "$0") kubectl get pods -A
  $(basename "$0") --script <<'EOF'
  set -euo pipefail
  hostname
  EOF
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

remote_cmd() {
  local q
  q="$(printf '%q ' "$@")"
  ssh "$CENTRAL_SSH" bash -lc "$q"
}

if [[ $# -eq 0 ]]; then
  exec ssh -t "$CENTRAL_SSH"
fi

if [[ "$1" == "--script" ]]; then
  shift
  exec ssh "$CENTRAL_SSH" bash -s "$@"
fi

if [[ "$1" == "kubectl" ]]; then
  shift
  kcmd="$(printf '%q ' "$@")"
  remote_cmd "export KUBECONFIG=\"\$HOME/.kube/config-central\"; kubectl --context=$(printf '%q' "$KUBE_CONTEXT") ${kcmd}"
else
  remote_cmd "$@"
fi
