#!/usr/bin/env bash
# Render MetalLB into repos/ for Config Sync GitOps push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
METALLB_VERSION="${METALLB_VERSION:-v0.14.8}"
METALLB_MANIFEST="${METALLB_MANIFEST:-https://raw.githubusercontent.com/metallb/metallb/${METALLB_VERSION}/config/manifests/metallb-native.yaml}"
METALLB_NS="${METALLB_NS:-metallb-system}"

fetch_manifest() {
  local out
  out="$(mktemp)"
  curl -fsSL "$METALLB_MANIFEST" -o "$out"
  printf '%s' "$out"
}

write_cluster_metallb() {
  local cluster="$1"
  local src="$2"
  local repo_name dest_ns dest_cluster pool_name pool_range l2_iface ost_vip

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${METALLB_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  pool_name="$(metallb_site_pool_name "$cluster")"
  pool_range="$(metallb_site_pool_for_cluster "$cluster")"
  l2_iface="$(metallb_l2_interface_for_cluster "$cluster")"
  ost_vip="$(openspeedtest_vip "$cluster")"
  mkdir -p "$dest_ns" "$dest_cluster"

  python3 - "$src" "$dest_ns" "$dest_cluster" "$METALLB_NS" \
    "$pool_name" "$pool_range" "$l2_iface" "$ost_vip" <<'PY'
import sys
from ipaddress import ip_address
from pathlib import Path

import yaml

(src, dest_ns, dest_cluster, metallb_ns, pool_name, pool_range, l2_iface, ost_vip) = sys.argv[1:9]
dest_ns = Path(dest_ns)
dest_cluster = Path(dest_cluster)


def vip_in_pool(vip: str, pool: str) -> bool:
    addr = ip_address(vip.strip())
    if "-" in pool:
        start_s, end_s = pool.split("-", 1)
        return ip_address(start_s.strip()) <= addr <= ip_address(end_s.strip())
    if "/" in pool:
        from ipaddress import ip_network
        return addr in ip_network(pool.strip(), strict=False)
    return addr == ip_address(pool.strip())


cluster_kinds = {
    "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition",
    "ValidatingWebhookConfiguration",
}
managed_ns_prefixes = (
    "namespace-metallb-system", "serviceaccount-", "role-metallb", "rolebinding-metallb",
    "configmap-metallb", "secret-metallb", "service-metallb", "deployment-metallb",
    "daemonset-metallb", "ipaddresspool-", "l2advertisement-",
)
managed_cluster_prefixes = (
    "customresourcedefinition-bfdprofiles.metallb.io",
    "customresourcedefinition-bgpadvertisements.metallb.io",
    "customresourcedefinition-bgppeers.metallb.io",
    "customresourcedefinition-communities.metallb.io",
    "customresourcedefinition-ipaddresspools.metallb.io",
    "customresourcedefinition-l2advertisements.metallb.io",
    "customresourcedefinition-servicel2statuses.metallb.io",
    "clusterrole-metallb",
    "clusterrolebinding-metallb",
    "validatingwebhookconfiguration-metallb",
)

for directory, prefixes in ((dest_ns, managed_ns_prefixes), (dest_cluster, managed_cluster_prefixes)):
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()

cluster_docs = []
ns_docs = []

for doc in yaml.safe_load_all(Path(src).read_text()):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    meta = doc.setdefault("metadata", {})
    if meta.get("annotations") is None:
        meta.pop("annotations", None)
    if kind in cluster_kinds:
        if kind == "CustomResourceDefinition" and meta.get("name") == "bgppeers.metallb.io":
            annotations = meta.setdefault("annotations", {})
            annotations["client.lifecycle.config.k8s.io/mutation"] = "ignore"
        cluster_docs.append(doc)
    else:
        if kind != "Namespace" and "namespace" not in meta:
            meta["namespace"] = metallb_ns
        ns_docs.append(doc)

addresses = [pool_range]
# OpenSpeedTest VIPs use a dedicated pool (see render_openspeedtest_gitops.sh).

pool_doc = {
    "apiVersion": "metallb.io/v1beta1",
    "kind": "IPAddressPool",
    "metadata": {"name": pool_name, "namespace": metallb_ns},
    "spec": {"addresses": addresses, "autoAssign": True},
}
# Workload site NICs differ (VMs: enp7s0; bare metal: eno1 / enp4s0f0 / enP*).
# Pinning only enp7s0 makes speakers on usrp/gh82 fail to announce .137 OST VIPs.
# Omit interfaces on workload clusters so MetalLB picks any iface on the VIP subnet.
l2_spec = {"ipAddressPools": [pool_name]}
if l2_iface and l2_iface != "any":
    l2_spec["interfaces"] = [l2_iface]
l2_doc = {
    "apiVersion": "metallb.io/v1beta1",
    "kind": "L2Advertisement",
    "metadata": {"name": f"{pool_name}-l2", "namespace": metallb_ns},
    "spec": l2_spec,
}
ns_docs.extend([pool_doc, l2_doc])

def write_docs(docs, directory):
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        (directory / f"{kind}-{name}.yaml").write_text(
            yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        )

write_docs(cluster_docs, dest_cluster)
write_docs(ns_docs, dest_ns)
iface_note = l2_iface if (l2_iface and l2_iface != "any") else "any (auto)"
print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/{metallb_ns}: {len(ns_docs)} resources")
print(f"  pool {pool_name}: {', '.join(addresses)} on {iface_note}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (MetalLB ${pool_name})"
}

main() {
  local clusters=("$@")
  local src=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt "${ALL_CLUSTERS[@]}")
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  src="$(fetch_manifest)"
  trap 'rm -f "${src:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_cluster_metallb "$cluster" "$src"
  done

  echo
  echo "Site pool range: ${CLUSTER_METALLB_SITE_POOL} (split per workload cluster)"
  echo "Pool name: site-pool (mgmt: mgmt-pool on ${MGMT_IFACE:-enp1s0})"
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write MetalLB controller + site IPAddressPool to repos/<gitea-repo>/.
Default: mgmt, central, regional, edge.

Workload site pool: ${CLUSTER_METALLB_SITE_POOL} (enp7s0), split per cluster:
  central  ${CLUSTER_METALLB_SITE_POOL_SLICE[central]}
  regional ${CLUSTER_METALLB_SITE_POOL_SLICE[regional]}
  edge     ${CLUSTER_METALLB_SITE_POOL_SLICE[edge]}

Environment:
  METALLB_VERSION       MetalLB release tag (default: v0.14.8)
  METALLB_SITE_POOL     Override pool range for a single render
  CLUSTER_METALLB_SITE_POOL  Full range (default: 10.1.138.100-10.1.138.199)
EOF
  exit 0
fi

main "$@"
