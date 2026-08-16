#!/usr/bin/env bash
# Render NVIDIA GPU Operator manifests into repos/ for Config Sync GitOps push.
# GH200 nodes (gpu-gh81=central, gpu-gh82=regional) already have the host driver + container
# toolkit installed, so the operator runs with driver.enabled=false and lets its
# toolkit component configure containerd + advertise nvidia.com/gpu.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
GPU_OPERATOR_NS="${GPU_OPERATOR_NS:-gpu-operator}"
GPU_OPERATOR_VERSION="${GPU_OPERATOR_VERSION:-v26.3.3}"
GPU_OPERATOR_REPO="${GPU_OPERATOR_REPO:-https://helm.ngc.nvidia.com/nvidia}"
# GH200: host driver is preinstalled (Grace 64k kernel), so the operator must NOT
# ship its own driver container. Toolkit stays enabled to configure containerd.
GPU_DRIVER_ENABLED="${GPU_DRIVER_ENABLED:-false}"
GPU_TOOLKIT_ENABLED="${GPU_TOOLKIT_ENABLED:-true}"

# Clusters whose worker set includes a GPU node. Only these get the operator.
DEFAULT_GPU_CLUSTERS=(central regional)

render_chart() {
  local out values
  out="$(mktemp)"
  values="$(mktemp)"
  cat >"$values" <<EOF
driver:
  enabled: ${GPU_DRIVER_ENABLED}
toolkit:
  enabled: ${GPU_TOOLKIT_ENABLED}
operator:
  defaultRuntime: containerd
EOF
  helm repo add nvidia "$GPU_OPERATOR_REPO" >/dev/null 2>&1 || true
  helm repo update nvidia >/dev/null 2>&1 || true
  helm template gpu-operator nvidia/gpu-operator \
    --version "$GPU_OPERATOR_VERSION" \
    --namespace "$GPU_OPERATOR_NS" \
    --include-crds \
    -f "$values" >"$out"
  rm -f "$values"
  printf '%s' "$out"
}

write_namespace() {
  local dir="$1"
  # GPU Operator operands (toolkit, device-plugin, validator) require privileged pods.
  cat >"${dir}/namespace-${GPU_OPERATOR_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${GPU_OPERATOR_NS}
  labels:
    pod-security.kubernetes.io/enforce: privileged
    pod-security.kubernetes.io/audit: privileged
    pod-security.kubernetes.io/warn: privileged
EOF
}

split_chart_manifests() {
  local src="$1"
  local dest_cluster="$2"
  local dest_ns="$3"

  python3 - "$src" "$dest_cluster" "$dest_ns" "$GPU_OPERATOR_NS" <<'PY'
import sys
from pathlib import Path

import yaml

src, dest_cluster, dest_ns, gpu_ns = sys.argv[1:5]
dest_cluster = Path(dest_cluster)
dest_ns = Path(dest_ns)

cluster_kinds = {
    "CustomResourceDefinition",
    "ClusterRole",
    "ClusterRoleBinding",
    "ClusterPolicy",
    "NVIDIADriver",
    "RuntimeClass",
    "PriorityClass",
    "ValidatingWebhookConfiguration",
    "MutatingWebhookConfiguration",
}

# gpu-operator-owned filename prefixes we are allowed to prune from the SHARED
# cluster/ directory (must not touch flannel/metallb/multus/etc. resources).
managed_cluster_prefixes = (
    "customresourcedefinition-clusterpolicies.nvidia.com",
    "customresourcedefinition-nvidiadrivers.nvidia.com",
    "customresourcedefinition-nodefeatures.nfd.k8s-sigs.io",
    "customresourcedefinition-nodefeaturegroups.nfd.k8s-sigs.io",
    "customresourcedefinition-nodefeaturerules.nfd.k8s-sigs.io",
    "clusterrole-gpu-operator",
    "clusterrolebinding-gpu-operator",
    "clusterpolicy-",
)


def clean_metadata(meta):
    if not isinstance(meta, dict):
        return
    if meta.get("annotations") is None:
        meta.pop("annotations", None)


cluster_docs = []
ns_docs = []

for doc in yaml.safe_load_all(Path(src).read_text()):
    if not doc or not doc.get("kind"):
        continue
    meta = doc.setdefault("metadata", {})
    ann = meta.get("annotations") or {}
    # Drop Helm lifecycle hooks (crd-upgrade / nfd-prune): they are only relevant
    # to `helm upgrade|uninstall`, not to a Config Sync reconcile loop.
    if "helm.sh/hook" in ann:
        continue
    clean_metadata(meta)
    kind = doc["kind"]
    if kind in cluster_kinds:
        cluster_docs.append(doc)
    else:
        if kind != "Namespace" and "namespace" not in meta:
            meta["namespace"] = gpu_ns
        ns_docs.append(doc)

# Prune previously rendered gpu-operator files.
dest_cluster.mkdir(parents=True, exist_ok=True)
dest_ns.mkdir(parents=True, exist_ok=True)
for old in dest_cluster.glob("*.yaml"):
    if any(old.name.startswith(p) for p in managed_cluster_prefixes):
        old.unlink()
for old in dest_ns.glob("*.yaml"):
    if old.name == f"namespace-{gpu_ns}.yaml":
        continue
    old.unlink()


def write_docs(docs, directory):
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        (directory / f"{kind}-{name}.yaml").write_text(
            yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        )


write_docs(cluster_docs, dest_cluster)
write_docs(ns_docs, dest_ns)
print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/{gpu_ns}: {len(ns_docs)} resources")
PY
}

write_cluster_gpu_operator() {
  local cluster="$1"
  local chart_yaml="$2"
  local repo_name dest_ns dest_cluster
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${GPU_OPERATOR_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"

  mkdir -p "$dest_ns" "$dest_cluster"
  split_chart_manifests "$chart_yaml" "$dest_cluster" "$dest_ns"
  write_namespace "$dest_ns"

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (GPU Operator ${GPU_OPERATOR_VERSION}, driver.enabled=${GPU_DRIVER_ENABLED})"
}

main() {
  local clusters=("$@")
  local chart_yaml tmp

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=("${DEFAULT_GPU_CLUSTERS[@]}")
  fi

  if ! command -v helm >/dev/null 2>&1; then
    echo "error: helm not found" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  tmp="$(mktemp)"
  chart_yaml="$(render_chart)"
  cp "$chart_yaml" "$tmp"
  rm -f "$chart_yaml"

  for cluster in "${clusters[@]}"; do
    write_cluster_gpu_operator "$cluster" "$tmp"
  done

  rm -f "$tmp"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh   then   kubectl get node <gh8x> -o jsonpath='{.status.allocatable.nvidia\\.com/gpu}'"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Render NVIDIA GPU Operator into repos/<gitea-repo>/ for Config Sync (unstructured).
Splits helm output into cluster/ (CRDs, ClusterRole[Binding], ClusterPolicy) and
namespaces/${GPU_OPERATOR_NS}/ (one file per resource). Helm hook resources are dropped.

Default clusters: ${DEFAULT_GPU_CLUSTERS[*]} (the sites whose worker is a GH200 node:
gpu-gh81=central, gpu-gh82=regional). Pass explicit clusters to override.

GH200 notes:
  driver.enabled=${GPU_DRIVER_ENABLED}   (host driver preinstalled; Grace 64k kernel)
  toolkit.enabled=${GPU_TOOLKIT_ENABLED}  (operator configures containerd + nvidia runtime)

Chart: nvidia/gpu-operator ${GPU_OPERATOR_VERSION}

Environment:
  GPU_OPERATOR_VERSION   chart version (default: v26.3.3)
  GPU_OPERATOR_NS        namespace (default: gpu-operator)
  GPU_DRIVER_ENABLED     driver.enabled (default: false)
  GPU_TOOLKIT_ENABLED    toolkit.enabled (default: true)
  REPOS_DIR              source tree (default: repos/ at repo root)
EOF
  exit 0
fi

main "$@"
