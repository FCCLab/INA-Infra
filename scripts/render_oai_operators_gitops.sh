#!/usr/bin/env bash
# Render OpenAirInterface 5G CN operators into repos/ for Config Sync GitOps push.
# Upstream: https://github.com/openairinterface/oai-operators
#
# DEPRECATED for this lab: profile controllers live in ina-infra only.
# Prefer: ./scripts/render_ina_cn_operators_gitops.sh [profile_ns]
# Set FORCE_OAI_CN_OPERATORS=1 to run this legacy path anyway.
set -euo pipefail

if [[ "${FORCE_OAI_CN_OPERATORS:-}" != "1" ]]; then
  echo "error: oai-cn-operators is retired; use ./scripts/render_ina_cn_operators_gitops.sh" >&2
  echo "       (override with FORCE_OAI_CN_OPERATORS=1 if you really need this script)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_OPERATORS_REF="${OAI_OPERATORS_REF:-main}"
OAI_OPERATORS_BASE="${OAI_OPERATORS_BASE:-https://raw.githubusercontent.com/openairinterface/oai-operators/${OAI_OPERATORS_REF}}"
OAI_CN_OPERATORS_NS="${OAI_CN_OPERATORS_NS:-oai-cn-operators}"
OAI_CN_NS="${OAI_CN_NS:-oai-cn}"
UPSTREAM_CN_NS="${UPSTREAM_CN_NS:-oaicp}"
UPSTREAM_OPERATORS_NS="${UPSTREAM_OPERATORS_NS:-oaiops}"
OAI_NAD_PARENT="${OAI_NAD_PARENT:-$SITE_IFACE}"
NEPHIO_CRD_BASE="${NEPHIO_CRD_BASE:-https://raw.githubusercontent.com/nephio-project/api/main/config/crd/bases}"

OAI_OPERATOR_MANIFESTS=(
  amf.yaml
  ausf.yaml
  nrf.yaml
  smf.yaml
  udm.yaml
  udr.yaml
  upf.yaml
)

NEPHIO_CRD_MANIFESTS=(
  ref.nephio.org_configs.yaml
  workload.nephio.org_nfdeployments.yaml
  workload.nephio.org_nfconfigs.yaml
)

fetch_upstream_manifests() {
  local out_dir nf crd
  out_dir="$(mktemp -d)"
  for nf in "${OAI_OPERATOR_MANIFESTS[@]}"; do
    curl -fsSL "${OAI_OPERATORS_BASE}/oai5gcore/controllerdeploy/${nf}" \
      -o "${out_dir}/${nf}"
  done
  mkdir -p "${out_dir}/crd"
  for crd in "${NEPHIO_CRD_MANIFESTS[@]}"; do
    curl -fsSL "${NEPHIO_CRD_BASE}/${crd}" -o "${out_dir}/crd/${crd}"
  done
  printf '%s' "$out_dir"
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${OAI_CN_OPERATORS_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${OAI_CN_OPERATORS_NS}
EOF
}

split_operator_manifests() {
  local src_dir="$1"
  local dest_cluster="$2"
  local dest_ns="$3"
  local cluster="$4"
  local amf_n2_vip upf_n3_vip

  amf_n2_vip="$(amf_n2_vip "$cluster")"
  upf_n3_vip="$(upf_n3_vip "$cluster")"

  python3 - "$src_dir" "$dest_cluster" "$dest_ns" "$OAI_CN_OPERATORS_NS" "$UPSTREAM_OPERATORS_NS" \
    "$OAI_CN_NS" "$UPSTREAM_CN_NS" "$OAI_NAD_PARENT" "$amf_n2_vip" "$upf_n3_vip" \
    "$OAI_OPERATORS_REF" "$SCRIPT_DIR/oai_debug_sidecar.py" "$OAI_DEBUG_SIDECAR_IMAGE" <<'PY'
import importlib.util
import re
import sys
import urllib.request
from pathlib import Path

import yaml

(src_dir, dest_cluster, dest_ns, target_ns, upstream_ns, cn_ns, upstream_cn_ns,
 nad_parent, amf_n2_vip, upf_n3_vip, operators_ref, debug_lib, debug_image) = sys.argv[1:14]
spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
cluster_kinds = {"ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"}
cluster_docs = []
ns_docs = []

def clean_metadata(meta):
    if not isinstance(meta, dict):
        return
    if meta.get("annotations") is None:
        meta.pop("annotations", None)


def rewrite_namespace(obj):
    if not isinstance(obj, dict):
        return
    meta = obj.get("metadata")
    if isinstance(meta, dict) and meta.get("namespace") == upstream_ns:
        meta["namespace"] = target_ns
    subjects = obj.get("subjects")
    if isinstance(subjects, list):
        for subject in subjects:
            if isinstance(subject, dict) and subject.get("namespace") == upstream_ns:
                subject["namespace"] = target_ns


def patch_operator_controller_debug(doc, name):
    spec = doc["spec"]["template"]["spec"]
    containers = spec["containers"]
    env = containers[0].setdefault("env", [])
    env = [item for item in env if item.get("name") not in ("DEBUG_SIDECAR", "DEBUG_SIDECAR_IMAGE")]
    env.extend([
        {"name": "DEBUG_SIDECAR", "value": "yes"},
        {"name": "DEBUG_SIDECAR_IMAGE", "value": debug_image},
    ])
    containers[0]["env"] = env
    nf = "amf" if "amf" in name else "upf"
    volumes = [v for v in spec.get("volumes", []) if v.get("name") != "utils-patch"]
    volumes.append({
        "name": "utils-patch",
        "configMap": {"name": f"oai-{nf}-controller-utils"},
    })
    spec["volumes"] = volumes
    mounts = [m for m in containers[0].get("volumeMounts", []) if m.get("name") != "utils-patch"]
    mounts.append({
        "name": "utils-patch",
        "mountPath": "/root/.local/utils.py",
        "subPath": "utils.py",
    })
    containers[0]["volumeMounts"] = mounts


def patch_operator_arch(doc):
    """OAI CN controller images are amd64-only; keep off arm64 GPU workers (gh81/gh82)."""
    if doc.get("kind") != "Deployment":
        return
    spec = doc.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
    node_selector = spec.setdefault("nodeSelector", {})
    node_selector["kubernetes.io/arch"] = "amd64"


def patch_operator_svc(doc):
    if doc.get("kind") != "Deployment":
        return
    name = doc.get("metadata", {}).get("name", "")
    svc_type = None
    lb_ip = None
    # AMF/UPF N3 are macvlan on 10.1.139.0/24, not Kubernetes LoadBalancer VIPs.
    if name in ("oai-amf-controller", "oai-upf-controller"):
        svc_type = "ClusterIP"
        patch_operator_controller_debug(doc, name)
    elif name in ("oai-nrf-controller", "oai-udr-controller"):
        svc_type = "ClusterIP"
    else:
        return
    containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        return
    env = containers[0].setdefault("env", [])
    env = [item for item in env if item.get("name") not in ("SVC_TYPE", "LOADBALANCER_IP")]
    env.append({"name": "SVC_TYPE", "value": svc_type})
    if lb_ip:
        env.append({"name": "LOADBALANCER_IP", "value": lb_ip})
    containers[0]["env"] = env


def patch_nf_conf(doc, upf_n3_vip):
    if doc.get("kind") != "ConfigMap":
        return
    name = doc["metadata"].get("name", "")
    data = doc.get("data")
    if not isinstance(data, dict):
        return
    # NOTE: Do NOT inject interfaceUpfInfoList into UPF upf_info.
    # OAI UPF (this image) fails to parse that block:
    #   "Could not parse upf: cannot use operator[] with a string argument with array"
    # and then NRF-registers SST=1/SD=FFFFFF DNN=default — breaking SMF UPF
    # selection by S-NSSAI. N3 UL FTEID is fixed via SMF n3_local_ipv4 instead.
    for key, val in list(data.items()):
        if not isinstance(val, str):
            continue
        if name == "oai-smf-nf-conf":
            # Prefer NRF UPF discovery by S-NSSAI when multiple slice UPFs are present.
            val = val.replace("discover_upf: no", "discover_upf: yes")
            val = val.replace(
                "enable_usage_reporting: no",
                f"enable_usage_reporting: no\n        n3_local_ipv4: {upf_n3_vip}",
            )
        elif name == "oai-upf-nf-conf":
            # Strip any previously injected interfaceUpfInfoList block.
            val = re.sub(
                r"upf_info:\n    interfaceUpfInfoList:.*?    sNssaiUpfInfoList:",
                "upf_info:\n    sNssaiUpfInfoList:",
                val,
                count=1,
                flags=re.S,
            )
        data[key] = val


def patch_op_conf(doc):
    if doc.get("kind") != "ConfigMap" or "-op-conf" not in doc["metadata"].get("name", ""):
        return
    data = doc.get("data")
    if not isinstance(data, dict):
        return
    for key, val in list(data.items()):
        if not isinstance(val, str):
            continue
        val = val.replace(f".{upstream_cn_ns}.svc.cluster.local", f".{cn_ns}.svc.cluster.local")
        val = val.replace("parent: 'eth0'", f"parent: '{nad_parent}'")
        data[key] = val


def load_docs(path):
    text = Path(path).read_text()
    for doc in yaml.safe_load_all(text):
        if doc and doc.get("kind"):
            yield doc


src = Path(src_dir)
for path in sorted(src.glob("*.yaml")):
    for doc in load_docs(path):
        rewrite_namespace(doc)
        patch_op_conf(doc)
        patch_nf_conf(doc, upf_n3_vip)
        patch_operator_svc(doc)
        patch_operator_arch(doc)
        clean_metadata(doc.get("metadata"))
        if doc["kind"] == "Deployment":
            template = doc.get("spec", {}).get("template", {})
            clean_metadata(template.get("metadata"))
        kind = doc["kind"]
        if kind in cluster_kinds:
            cluster_docs.append(doc)
        else:
            meta = doc.setdefault("metadata", {})
            if kind != "Namespace" and "namespace" not in meta:
                meta["namespace"] = target_ns
            ns_docs.append(doc)

for path in sorted((src / "crd").glob("*.yaml")):
    for doc in load_docs(path):
        clean_metadata(doc.get("metadata"))
        cluster_docs.append(doc)

for nf_type in ("amf", "upf"):
    utils_url = (
        f"https://raw.githubusercontent.com/openairinterface/oai-operators/"
        f"{operators_ref}/operators/{nf_type}/controllers/utils.py"
    )
    with urllib.request.urlopen(utils_url) as resp:
        utils_text = resp.read().decode("utf-8")
    ns_docs.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"oai-{nf_type}-controller-utils",
            "namespace": target_ns,
        },
        "data": {"utils.py": oai_debug.patch_operator_utils_py(utils_text)},
    })


def write_docs(docs, directory, managed_prefixes):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(prefix) for prefix in managed_prefixes):
            old.unlink()
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        path = directory / f"{kind}-{name}.yaml"
        path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))


managed_cluster_prefixes = (
    "clusterrole-oai-",
    "clusterrolebinding-oai-",
    "customresourcedefinition-configs.ref.nephio.org",
    "customresourcedefinition-nfconfigs.workload.nephio.org",
    "customresourcedefinition-nfdeployments.workload.nephio.org",
)
managed_ns_prefixes = (
    "deployment-oai-",
    "serviceaccount-oai-",
    "configmap-oai-",
)

write_docs(cluster_docs, dest_cluster, managed_cluster_prefixes)
write_docs(ns_docs, dest_ns, managed_ns_prefixes)
print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/{target_ns}: {len(ns_docs)} resources")
PY
}

write_cluster_operators() {
  local cluster="$1"
  local src_dir="$2"
  local repo_name dest_dir dest_cluster

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_dir="${REPOS_DIR}/${repo_name}/namespaces/${OAI_CN_OPERATORS_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_dir" "$dest_cluster"

  split_operator_manifests "$src_dir" "$dest_cluster" "$dest_dir" "$cluster"
  write_namespace "$dest_dir"

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI CN operators → ${OAI_CN_OPERATORS_NS})"
}

main() {
  local clusters=("$@")
  local src_dir=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi

  for cluster in "${clusters[@]}"; do
    if [[ "$cluster" != "central" ]]; then
      echo "error: OAI CN operators deploy on central (core site) only, not '${cluster}'" >&2
      exit 1
    fi
  done

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  src_dir="$(fetch_upstream_manifests)"
  trap 'rm -rf "${src_dir:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_cluster_operators "$cluster" "$src_dir"
  done

  echo
  echo "Operators: amf ausf nrf smf udm udr upf"
  echo "Namespace: ${OAI_CN_OPERATORS_NS}"
  echo "Nephio CRDs: ref.nephio.org/config, workload.nephio.org/nfdeployments|nfconfigs"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "NRF/UDR use ClusterIP (in-cluster SBI only). AMF/UPF data-plane N2/N3 are macvlan on 10.1.139.0/24."
  echo "Optional docker hub pull secret in ${OAI_CN_OPERATORS_NS}: regcred"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write OAI 5G core network function operators to repos/<gitea-repo>/ for Config Sync.
Default cluster: central (core site).

Upstream: openairinterface/oai-operators (${OAI_OPERATORS_REF})
Namespace: ${OAI_CN_OPERATORS_NS} (upstream uses ${UPSTREAM_OPERATORS_NS})

Includes Nephio CRDs required by the operators (workload.nephio.org NFDeployment).

Environment:
  OAI_OPERATORS_REF       Git ref (default: main)
  OAI_CN_OPERATORS_NS     Target namespace (default: oai-cn-operators)
  REPOS_DIR               Output tree (default: repos/)
EOF
  exit 0
fi

main "$@"
