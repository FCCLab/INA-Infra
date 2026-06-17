# shellcheck shell=bash
# Merge ~/.kube/config-central into KUBECONFIG when talking to central@central.
merge_kubeconfig_for_central() {
  local central="${CENTRAL_KUBECONFIG:-$HOME/.kube/config-central}"
  local default="${HOME}/.kube/config"
  if [[ ! -f "$central" ]]; then
    export KUBECONFIG="${KUBECONFIG:-$default}"
    return
  fi
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG="${default}:${central}"
  elif [[ ":${KUBECONFIG}:" != *":${central}:"* ]]; then
    export KUBECONFIG="${KUBECONFIG}:${central}"
  fi
}

require_kubectl_context() {
  local ctx="$1"
  if ! kubectl config get-contexts "$ctx" >/dev/null 2>&1; then
    echo "error: kubectl context not found: $ctx" >&2
    if [[ ! -f "${CENTRAL_KUBECONFIG:-$HOME/.kube/config-central}" ]]; then
      echo "Missing ~/.kube/config-central (run bringup-central-0.sh on the central node)." >&2
    else
      echo "Try: export KUBECONFIG=\$HOME/.kube/config:\$HOME/.kube/config-central" >&2
    fi
    return 1
  fi
}
