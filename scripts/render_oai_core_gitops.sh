#!/usr/bin/env bash
# Render OAI 5G core workloads (NFDeployment CRs, NADs, MySQL) into repos/ for GitOps.
# Requires operators in oai-cn-operators (render_oai_operators_gitops.sh) and Multus CNI.
# Upstream: https://github.com/openairinterface/oai-operators
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_OPERATORS_REF="${OAI_OPERATORS_REF:-main}"
OAI_OPERATORS_BASE="${OAI_OPERATORS_BASE:-https://raw.githubusercontent.com/openairinterface/oai-operators/${OAI_OPERATORS_REF}}"
OAI_CN_NS="${OAI_CN_NS:-oai-cn}"
OAI_UPF_NS="${OAI_UPF_NS:-oai-upf}"
UPF_NF_NAME="${UPF_NF_NAME:-upf-core}"
UPF_UPSTREAM_NAME="${UPF_UPSTREAM_NAME:-upf-edge}"
UPSTREAM_CN_NS="${UPSTREAM_CN_NS:-oaicp}"
OAI_NAD_PARENT="${OAI_NAD_PARENT:-$SITE_IFACE}"
# emptyDir: no StorageClass needed. Set MYSQL_STORAGE=pvc when local-path is deployed.
MYSQL_STORAGE="${MYSQL_STORAGE:-pvc}"
MYSQL_STORAGE_CLASS="${MYSQL_STORAGE_CLASS:-local-path}"

NFDEPLOY_MANIFESTS=(
  nrfdeploy.yaml
  ausfdeploy.yaml
  udmdeploy.yaml
  udrdeploy.yaml
  amfdeploy.yaml
  smfdeploy.yaml
  upfdeploy.yaml
)

NAD_MANIFESTS=(
  amf.yaml
  smf.yaml
  upf-edge.yaml
)

fetch_upstream_core() {
  local out_dir f
  out_dir="$(mktemp -d)"
  mkdir -p "${out_dir}/nfdeploy" "${out_dir}/nad"
  for f in "${NFDEPLOY_MANIFESTS[@]}"; do
    curl -fsSL "${OAI_OPERATORS_BASE}/oai5gcore/nfdeploy/${f}" \
      -o "${out_dir}/nfdeploy/${f}"
  done
  for f in "${NAD_MANIFESTS[@]}"; do
    curl -fsSL "${OAI_OPERATORS_BASE}/oai5gcore/nad/${f}" \
      -o "${out_dir}/nad/${f}"
  done
  printf '%s' "$out_dir"
}

write_core_manifests() {
  local cluster="$1"
  local src_dir="$2"
  local mysql_chart="$3"
  local repo_name dest_cn dest_upf dest_cluster

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_cn="${REPOS_DIR}/${repo_name}/namespaces/${OAI_CN_NS}"
  dest_upf="${REPOS_DIR}/${repo_name}/namespaces/${OAI_UPF_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_cn" "$dest_upf" "$dest_cluster"

  python3 - "$src_dir" "$mysql_chart" "$dest_cn" "$dest_upf" "$dest_cluster" \
    "$OAI_CN_NS" "$OAI_UPF_NS" "$UPSTREAM_CN_NS" "$OAI_NAD_PARENT" \
    "$MYSQL_STORAGE" "$MYSQL_STORAGE_CLASS" "$UPF_NF_NAME" "$UPF_UPSTREAM_NAME" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

import yaml

(src_dir, mysql_chart, dest_cn, dest_upf, dest_cluster,
 cn_ns, upf_ns, upstream_cn_ns, nad_parent, mysql_storage, mysql_sc,
 upf_name, upf_upstream) = sys.argv[1:14]

dest_cn = Path(dest_cn)
dest_upf = Path(dest_upf)
dest_cluster = Path(dest_cluster)

NS_REWRITE = {upstream_cn_ns: cn_ns, "oai-upf": upf_ns}

def clean_metadata(meta):
    if isinstance(meta, dict) and meta.get("annotations") is None:
        meta.pop("annotations", None)


def rewrite_ns(obj):
    if not isinstance(obj, dict):
        return
    meta = obj.get("metadata")
    if isinstance(meta, dict):
        ns = meta.get("namespace")
        if ns in NS_REWRITE:
            meta["namespace"] = NS_REWRITE[ns]
    spec = obj.get("spec")
    if isinstance(spec, dict) and "config" in spec:
        embedded = spec.get("config")
        if isinstance(embedded, dict):
            rewrite_ns(embedded)
    config_refs = obj.get("spec", {}).get("configRefs") if isinstance(obj.get("spec"), dict) else None
    if isinstance(config_refs, list):
        for ref in config_refs:
            rewrite_ns(ref)


def fix_plmn_typos(obj):
    if isinstance(obj, dict):
        plmn_id = obj.get("plmnID")
        if isinstance(plmn_id, dict) and "mnc" not in plmn_id and "mcc" in plmn_id:
            mcc = plmn_id.get("mcc")
            if isinstance(mcc, str) and len(mcc) <= 2:
                plmn_id["mnc"] = mcc
                plmn_id["mcc"] = "001"
        for value in obj.values():
            fix_plmn_typos(value)
    elif isinstance(obj, list):
        for item in obj:
            fix_plmn_typos(item)


def rename_upf(obj):
    smf_ref_src = f"smf-core-{upf_upstream}"
    smf_ref_dst = f"smf-core-{upf_name}"
    if isinstance(obj, dict):
        for key, val in list(obj.items()):
            if key == "name" and isinstance(val, str):
                if val == upf_upstream:
                    obj[key] = upf_name
                elif val == f"{upf_upstream}-config":
                    obj[key] = f"{upf_name}-config"
                elif val == smf_ref_src:
                    obj[key] = smf_ref_dst
                elif val.startswith(f"{upf_upstream}-"):
                    obj[key] = val.replace(upf_upstream, upf_name, 1)
            else:
                rename_upf(val)
    elif isinstance(obj, list):
        for item in obj:
            rename_upf(item)


def patch_nad(doc):
    if doc.get("kind") != "NetworkAttachmentDefinition":
        return
    raw = doc.get("spec", {}).get("config")
    if not isinstance(raw, str):
        return
    cfg = json.loads(raw)
    if cfg.get("name", "").startswith(f"{upf_upstream}-"):
        cfg["name"] = cfg["name"].replace(upf_upstream, upf_name, 1)
    for plugin in cfg.get("plugins", []):
        if plugin.get("type") == "macvlan" and "master" in plugin:
            plugin["master"] = nad_parent
    doc["spec"]["config"] = json.dumps(cfg)


def target_dir(doc):
    ns = doc.get("metadata", {}).get("namespace", cn_ns)
    if ns == upf_ns:
        return dest_upf
    return dest_cn


def load_docs(path):
    for doc in yaml.safe_load_all(Path(path).read_text()):
        if doc and doc.get("kind"):
            yield doc


def write_doc(doc, prefix=""):
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"]
    fname = f"{prefix}{kind}-{name}.yaml"
    target_dir(doc).mkdir(parents=True, exist_ok=True)
    (target_dir(doc) / fname).write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )


def purge_managed(directory, prefixes):
    directory = Path(directory)
    if not directory.is_dir():
        return
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()


managed_cn = (
    "namespace-", "nfdeployment-", "nfconfig-", "config-",
    "networkattachmentdefinition-",
    "05-deployment-mysql", "05-service-mysql", "05-secret-mysql",
    "05-persistentvolumeclaim-mysql", "05-configmap-mysql",
)
managed_upf = managed_cn
purge_managed(dest_cn, managed_cn)
purge_managed(dest_upf, managed_upf)
for directory in (dest_cn, dest_upf):
    for old in directory.glob(f"*{upf_upstream}*.yaml"):
        old.unlink()

# Namespaces
for ns_name, labels in (
    (cn_ns, {}),
    (upf_ns, {
        "pod-security.kubernetes.io/warn": "privileged",
        "pod-security.kubernetes.io/audit": "privileged",
        "pod-security.kubernetes.io/enforce": "privileged",
    }),
):
    doc = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns_name}}
    if labels:
        doc["metadata"]["labels"] = labels
    directory = dest_cn if ns_name == cn_ns else dest_upf
    (directory / f"namespace-{ns_name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )

# NADs (before NF deployments)
for path in sorted(Path(src_dir, "nad").glob("*.yaml")):
    for doc in load_docs(path):
        rewrite_ns(doc)
        rename_upf(doc)
        patch_nad(doc)
        clean_metadata(doc.get("metadata"))
        write_doc(doc, prefix="10-")

# NFDeployment / NFConfig / ref Config CRs
order = {
    "nrfdeploy.yaml": "20-",
    "ausfdeploy.yaml": "21-",
    "udmdeploy.yaml": "22-",
    "udrdeploy.yaml": "23-",
    "amfdeploy.yaml": "24-",
    "smfdeploy.yaml": "25-",
    "upfdeploy.yaml": "30-",
}
for fname, prefix in order.items():
    path = Path(src_dir, "nfdeploy", fname)
    for doc in load_docs(path):
        rewrite_ns(doc)
        rename_upf(doc)
        fix_plmn_typos(doc)
        clean_metadata(doc.get("metadata"))
        write_doc(doc, prefix=prefix)

# MySQL for UDR (helm → plain manifests in oai-cn)
helm_out = subprocess.check_output(
    ["helm", "template", "mysql", mysql_chart, "-n", cn_ns],
    text=True,
)
for doc in yaml.safe_load_all(helm_out):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    if kind == "PersistentVolumeClaim" and mysql_storage != "pvc":
        continue
    rewrite_ns(doc)
    clean_metadata(doc.get("metadata"))
    if kind == "Deployment":
        pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
        clean_metadata(doc.get("spec", {}).get("template", {}).get("metadata", {}))
        pod_spec.pop("imagePullSecrets", None)
        if mysql_storage != "pvc":
            pod_spec.pop("initContainers", None)
            for vol in pod_spec.get("volumes", []):
                if vol.get("name") == "data":
                    vol.pop("persistentVolumeClaim", None)
                    vol["emptyDir"] = {}
    if kind == "PersistentVolumeClaim" and mysql_storage == "pvc":
        doc.setdefault("spec", {})["storageClassName"] = mysql_sc
    meta = doc.setdefault("metadata", {})
    if kind != "Namespace":
        meta["namespace"] = cn_ns
    name = doc["metadata"]["name"]
    (dest_cn / f"05-{kind.lower()}-{name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )

cn_count = len(list(dest_cn.glob("*.yaml")))
upf_count = len(list(dest_upf.glob("*.yaml")))
print(f"  namespaces/{cn_ns}: {cn_count} resources")
print(f"  namespaces/{upf_ns}: {upf_count} resources")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI core → ${OAI_CN_NS}, ${OAI_UPF_NS})"
}

main() {
  local clusters=("$@")
  local src_dir mysql_chart=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi

  for cluster in "${clusters[@]}"; do
    if [[ "$cluster" != "central" ]]; then
      echo "error: OAI core NFs deploy on central (core site) only, not '${cluster}'" >&2
      exit 1
    fi
  done

  for cmd in python3 helm curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "error: $cmd not found" >&2
      exit 1
    fi
  done

  src_dir="$(fetch_upstream_core)"
  mysql_root="$(mktemp -d)"
  mysql_chart="${mysql_root}/chart"
  archive="$(mktemp --suffix=.tar.gz)"
  strip_dir="oai-operators-main"
  [[ "${OAI_OPERATORS_REF}" != "main" ]] && strip_dir="oai-operators-${OAI_OPERATORS_REF}"
  curl -fsSL "https://github.com/openairinterface/oai-operators/archive/refs/heads/${OAI_OPERATORS_REF}.tar.gz" \
    -o "$archive"
  tar -xzf "$archive" -C "$mysql_root" "${strip_dir}/helm-charts/mysql"
  mv "${mysql_root}/${strip_dir}/helm-charts/mysql" "$mysql_chart"
  rm -rf "${mysql_root}/${strip_dir}" "$archive"
  trap 'rm -rf "${src_dir:-}" "${mysql_root:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_core_manifests "$cluster" "$src_dir" "$mysql_chart"
  done

  echo
  echo "UPF NF name: ${UPF_NF_NAME} (namespace ${OAI_UPF_NS})"
  echo "Namespaces: ${OAI_CN_NS} (control plane), ${OAI_UPF_NS} (UPF)"
  echo "NAD parent NIC: ${OAI_NAD_PARENT}"
  echo "MySQL storage: ${MYSQL_STORAGE} (MYSQL_STORAGE=pvc + local-path provisioner for persistent)"
  echo
  echo "Prerequisites on cluster:"
  echo "  1. Operators: ./scripts/render_oai_operators_gitops.sh ${clusters[*]}"
  echo "  2. Multus CNI: ./scripts/render_multus_gitops.sh ${clusters[*]}"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write OAI 5G core NFDeployment intent to repos/<gitea-repo>/ for Config Sync.
Default cluster: central.

Creates:
  namespaces/${OAI_CN_NS}/     NRF, AUSF, UDM, UDR, AMF, SMF + MySQL
  namespaces/${OAI_UPF_NS}/    UPF + Multus NADs

Upstream NFDeployment examples: oai5gcore/nfdeploy/
Operators reconcile CRs into real OAI NF pods.

Environment:
  OAI_CN_NS           Core namespace (default: oai-cn)
  OAI_UPF_NS          UPF namespace (default: oai-upf)
  UPF_NF_NAME         UPF NFDeployment name (default: upf-core)
  UPF_UPSTREAM_NAME   Upstream name to rewrite (default: upf-edge)
  OAI_NAD_PARENT      Macvlan master NIC (default: ${SITE_IFACE})
  MYSQL_STORAGE       emptyDir (default) or pvc
  MYSQL_STORAGE_CLASS StorageClass when MYSQL_STORAGE=pvc (default: local-path)
  OAI_OPERATORS_REF   Git ref (default: main)
EOF
  exit 0
fi

main "$@"
