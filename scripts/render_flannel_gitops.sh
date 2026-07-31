#!/usr/bin/env bash
# Render Flannel CNI manifests into repos/ for Config Sync GitOps push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
FLANNEL_MANIFEST="${FLANNEL_MANIFEST:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"
SITE_IFACE="${SITE_IFACE:-enp7s0}"
# Site NICs across VMs (enp7s0), GH (aerial03/enP2s2f1np1), usrp (enp4s0f0), bare-metal (eno1|ens12f0).
FLANNEL_IFACE_REGEX="${FLANNEL_IFACE_REGEX:-^(aerial03|enP2s2f1np1|enp7s0|enp4s0f0|eno1|ens12f0)$}"

fetch_manifest() {
  local out
  out="$(mktemp)"
  curl -fsSL "$FLANNEL_MANIFEST" -o "$out"
  printf '%s' "$out"
}

write_cluster_flannel() {
  local cluster="$1"
  local repo_name src iface_flag reach_ip dest_cluster dest_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  src="$2"
  reach_ip=""
  if [[ "$cluster" == "mgmt" ]]; then
    iface_flag=""
  else
    iface_flag="$FLANNEL_IFACE_REGEX"
    # Prefer the NIC that can reach this cluster's site-plane CP (kube InternalIP).
    reach_ip="$(cluster_k8s_node_ip "$(cluster_cp_host "$cluster")")"
  fi
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/kube-flannel"
  mkdir -p "$dest_cluster" "$dest_ns"

  python3 - "$src" "$dest_cluster" "$dest_ns" "$iface_flag" "$reach_ip" <<'PY'
import shlex
import sys
from pathlib import Path

import yaml

src, dest_cluster, dest_ns, iface_regex, reach_ip = sys.argv[1:6]
docs = list(yaml.safe_load_all(Path(src).read_text()))
cluster_docs = []
ns_docs = []

for doc in docs:
    if not doc:
        continue
    kind = doc.get("kind", "")
    if kind in ("ClusterRole", "ClusterRoleBinding"):
        cluster_docs.append(doc)
        continue
    if kind == "DaemonSet":
        c0 = doc["spec"]["template"]["spec"]["containers"][0]
        args = list(c0.get("args") or [])
        # Drop iface / public-ip flags; we rebuild them below.
        args = [
            a
            for a in args
            if not a.startswith("--iface=")
            and not a.startswith("--iface-regex=")
            and not a.startswith("--iface-can-reach=")
            and not a.startswith("--public-ip=")
        ]
        if iface_regex:
            args.append(f"--iface-regex={iface_regex}")
        if reach_ip:
            # Prefer site-plane NIC even when default route is on mgmt/wifi.
            args.append(f"--iface-can-reach={reach_ip}")
        env = c0.setdefault("env", [])
        env = [e for e in env if e.get("name") != "NODE_IP"]
        env.append(
            {
                "name": "NODE_IP",
                "valueFrom": {
                    "fieldRef": {
                        "apiVersion": "v1",
                        "fieldPath": "status.hostIP",
                    }
                },
            }
        )
        c0["env"] = env
        # Force VXLAN public IP = kubelet InternalIP. Needed when the selected
        # NIC has multiple addresses (edge-3: 10.5.5.1 before 10.1.137.133).
        quoted = " ".join(shlex.quote(a) for a in (["/opt/bin/flanneld"] + args))
        c0["command"] = ["/bin/sh", "-c"]
        c0["args"] = [f'exec {quoted} --public-ip="${{NODE_IP}}"']
    ns_docs.append(doc)

def write_docs(docs, directory, prefix):
    directory = Path(directory)
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        path = directory / f"{prefix}{kind}-{name}.yaml"
        path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))

write_docs(cluster_docs, dest_cluster, "")
write_docs(ns_docs, dest_ns, "")

print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/kube-flannel: {len(ns_docs)} resources")
if iface_regex:
    print(f"  daemonset: --iface-regex={iface_regex}")
if reach_ip:
    print(f"  daemonset: --iface-can-reach={reach_ip}")
print("  daemonset: --public-ip=${NODE_IP} (status.hostIP)")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (Flannel CNI)"
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

  for cluster in "${clusters[@]}"; do
    write_cluster_flannel "$cluster" "$src"
  done

  rm -f "$src"

  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh"
  echo "Remove imperative CNI first: ./scripts/uninstall_flannel.sh"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write Flannel manifests to repos/<gitea-repo>/ for Config Sync.
Default: mgmt, central, regional, edge.

Workload clusters: --iface-regex=${FLANNEL_IFACE_REGEX}, --iface-can-reach=<site CP>,
and --public-ip=\${NODE_IP} from status.hostIP (kube InternalIP) so multi-address
NICs (e.g. edge-3) do not advertise the wrong VTEP IP.

Mgmt: same hostIP public-ip; no site iface-regex.

Source: ${FLANNEL_MANIFEST}
EOF
  exit 0
fi

main "$@"
