#!/usr/bin/env bash
# Render metrics-server into repos/ for Config Sync GitOps push.
# Adds --kubelet-insecure-tls for kubeadm lab clusters (self-signed kubelet certs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
# k8s 1.31 lab → 0.7.x / 0.8.x; pin for reproducible GitOps.
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.7.2}"
METRICS_SERVER_MANIFEST="${METRICS_SERVER_MANIFEST:-https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml}"
METRICS_SERVER_NS="${METRICS_SERVER_NS:-kube-system}"
# Lab kubeadm: kubelet serves self-signed certs.
METRICS_SERVER_INSECURE_TLS="${METRICS_SERVER_INSECURE_TLS:-1}"

fetch_manifest() {
  local out
  out="$(mktemp)"
  curl -fsSL "$METRICS_SERVER_MANIFEST" -o "$out"
  printf '%s' "$out"
}

write_cluster_metrics_server() {
  local cluster="$1"
  local src="$2"
  local repo_name dest_cluster dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${METRICS_SERVER_NS}"
  mkdir -p "$dest_cluster" "$dest_ns"

  python3 - "$src" "$dest_cluster" "$dest_ns" "$METRICS_SERVER_NS" "$METRICS_SERVER_INSECURE_TLS" <<'PY'
import sys
from pathlib import Path

import yaml

src, dest_cluster, dest_ns, ns, insecure = sys.argv[1:6]
dest_cluster = Path(dest_cluster)
dest_ns = Path(dest_ns)
insecure = insecure == "1"

cluster_kinds = {
    "ClusterRole",
    "ClusterRoleBinding",
    "APIService",
    "CustomResourceDefinition",
}
cluster_docs = []
ns_docs = []

for doc in yaml.safe_load_all(Path(src).read_text(encoding="utf-8")):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    meta = doc.setdefault("metadata", {})
    # Normalize namespace for namespaced objects.
    if kind not in cluster_kinds and kind != "Namespace":
        meta["namespace"] = ns
    if kind == "Deployment" and meta.get("name") == "metrics-server":
        spec = doc.setdefault("spec", {})
        tmpl = spec.setdefault("template", {})
        pod_spec = tmpl.setdefault("spec", {})
        # hostNetwork so kube-apiserver (host net) can reach the aggregated API
        # without depending on ClusterIP/pod-net hairpin from the control plane.
        # Use 4443 — 10250 collides with kubelet when hostNetwork=true.
        secure_port = 4443
        pod_spec["hostNetwork"] = True
        pod_spec["dnsPolicy"] = "ClusterFirstWithHostNet"
        containers = pod_spec.get("containers") or []
        for c in containers:
            if c.get("name") != "metrics-server":
                continue
            args = list(c.get("args") or [])
            args = [
                a
                for a in args
                if not str(a).startswith("--secure-port")
                and not str(a).startswith("--kubelet-preferred-address-types")
                and a != "--kubelet-insecure-tls"
            ]
            args.extend(
                [
                    f"--secure-port={secure_port}",
                    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
                ]
            )
            if insecure:
                args.append("--kubelet-insecure-tls")
            c["args"] = args
            ports = c.get("ports") or []
            for p in ports:
                if p.get("name") == "https":
                    p["containerPort"] = secure_port
            c["ports"] = ports or [
                {"containerPort": secure_port, "name": "https", "protocol": "TCP"}
            ]
    if kind in cluster_kinds:
        cluster_docs.append(doc)
    else:
        ns_docs.append(doc)


def purge(directory: Path, prefixes):
    if not directory.is_dir():
        return
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()


# Only remove metrics-server owned files (do not wipe Multus in kube-system).
cluster_prefixes = (
    "clusterrole-system-aggregated-metrics-reader",
    "clusterrole-system-metrics-server",
    "clusterrolebinding-metrics-server",
    "clusterrolebinding-system-metrics-server",
    "apiservice-v1beta1.metrics.k8s.io",
)
ns_prefixes = (
    "serviceaccount-metrics-server",
    "rolebinding-metrics-server",
    "service-metrics-server",
    "deployment-metrics-server",
)
purge(dest_cluster, cluster_prefixes)
purge(dest_ns, ns_prefixes)


def fname(doc):
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"].replace(":", "-").replace("/", "-")
    return f"{kind}-{name}.yaml"


for doc in cluster_docs:
    (dest_cluster / fname(doc)).write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
for doc in ns_docs:
    (dest_ns / fname(doc)).write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

print(f"  cluster/: {len(cluster_docs)} resources")
print(f"  namespaces/{ns}: {len(ns_docs)} resources")
PY
  echo "==> ${repo_name}: metrics-server ${METRICS_SERVER_VERSION} (ns=${METRICS_SERVER_NS}, insecure_tls=${METRICS_SERVER_INSECURE_TLS})"
}

main() {
  local clusters=("$@")
  local src=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt central regional edge)
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi
  if ! python3 -c "import yaml" 2>/dev/null; then
    echo "error: python3 PyYAML required (pip install pyyaml)" >&2
    exit 1
  fi

  echo "Fetching ${METRICS_SERVER_MANIFEST}"
  src="$(fetch_manifest)"
  trap 'rm -f "${src:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_cluster_metrics_server "$cluster" "$src"
  done

  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
  echo "Then: kubectl --context <ctx> -n kube-system get deploy,pods -l k8s-app=metrics-server"
  echo "      kubectl --context <ctx> top nodes"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write metrics-server manifests to repos/<gitea-repo>/ for Config Sync.
Default clusters: mgmt central regional edge

Env:
  METRICS_SERVER_VERSION   release tag (default: v0.7.2)
  METRICS_SERVER_MANIFEST  override components.yaml URL
  METRICS_SERVER_NS        namespace (default: kube-system)
  METRICS_SERVER_INSECURE_TLS  1=add --kubelet-insecure-tls (default: 1)
EOF
  exit 0
fi

main "$@"
