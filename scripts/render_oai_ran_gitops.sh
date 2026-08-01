#!/usr/bin/env bash
# Render OAI RAN operator + CU-CP NFDeployment into repos/ for Config Sync GitOps.
# Operator: nephio-project/catalog workloads/oai/oai-ran-operator
# Executor: nephio-project/oai (NFDeployment provider cucp.openairinterface.org)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_RAN_OPERATORS_NS="${OAI_RAN_OPERATORS_NS:-oai-ran-operators}"
OAI_RAN_CUCP_NS="${OAI_RAN_CUCP_NS:-oai-ran-cucp}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-docker.io/nephio/oai-ran-controller:latest}"
OAI_GNB_IMAGE="${OAI_GNB_IMAGE:-docker.io/oaisoftwarealliance/oai-gnb:v2.3.0}"
RAN_CATALOG_BASE="${RAN_CATALOG_BASE:-https://raw.githubusercontent.com/nephio-project/catalog/main/workloads/oai/oai-ran-operator/operator}"
NEPHIO_CRD_BASE="${NEPHIO_CRD_BASE:-https://raw.githubusercontent.com/nephio-project/api/main/config/crd/bases}"
NEPHIO_CRD_MANIFESTS=(
  ref.nephio.org_configs.yaml
  workload.nephio.org_nfdeployments.yaml
  workload.nephio.org_nfconfigs.yaml
)
# RAN CU-CP site (regional toward central AMF).
RAN_CUCP_CLUSTERS=(regional)

fetch_operator_manifests() {
  local out_dir f
  out_dir="$(mktemp -d)"
  for f in namespace.yaml serviceaccount.yaml deployment.yaml clusterrole.yaml clusterrolebinding.yaml; do
    curl -fsSL "${RAN_CATALOG_BASE}/${f}" -o "${out_dir}/${f}"
  done
  mkdir -p "${out_dir}/crd"
  for crd in "${NEPHIO_CRD_MANIFESTS[@]}"; do
    curl -fsSL "${NEPHIO_CRD_BASE}/${crd}" -o "${out_dir}/crd/${crd}"
  done
  printf '%s' "$out_dir"
}

write_ran_gitops() {
  local cluster="$1"
  local src_dir="$2"
  local repo_name dest_ops dest_cucp dest_cluster
  local cucp_n2 amf_n2 cucp_name cucp_f1c cucp_e1

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ops="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_OPERATORS_NS}"
  dest_cucp="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_CUCP_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_ops" "$dest_cucp" "$dest_cluster"

  cucp_n2="$(cucp_n2_vip "$cluster")"
  amf_n2="$(amf_n2_vip central)"
  cucp_f1c="$(oai_macvlan_ip regional 1)"
  cucp_e1="$(oai_macvlan_ip regional 2)"
  cucp_name="cucp-${cluster}"
  ran_nodes="${CLUSTER_CP_HOST[$cluster]},${CLUSTER_WORKER_HOST[$cluster]}"

  python3 - "$src_dir" "$dest_ops" "$dest_cucp" "$dest_cluster" \
    "$OAI_RAN_OPERATORS_NS" "$OAI_RAN_CUCP_NS" "$OAI_RAN_OPERATOR_IMAGE" \
    "$cucp_name" "$cucp_n2" "$amf_n2" "$cucp_f1c" "$cucp_e1" "$OAI_MACVLAN_GW" \
    "$OAI_GNB_IMAGE" "$SITE_IFACE" "$SCRIPT_DIR/oai_debug_sidecar.py" \
    "$OAI_DEBUG_SIDECAR_IMAGE" "$ran_nodes" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

import yaml

(src_dir, dest_ops, dest_cucp, dest_cluster, ops_ns, cucp_ns, operator_image,
 cucp_name, cucp_n2, amf_n2, f1c_ip, e1_ip, oai_gw, gnb_image, nad_parent,
 debug_lib, debug_image, ran_nodes) = sys.argv[1:19]
ran_node_list = [n for n in ran_nodes.split(",") if n]
spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_container = oai_debug.debug_sidecar_container(debug_image)

dest_ops = Path(dest_ops)
dest_cucp = Path(dest_cucp)
dest_cluster = Path(dest_cluster)
src_dir = Path(src_dir)

cluster_kinds = {"ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"}


def load_docs(path):
    for doc in yaml.safe_load_all(path.read_text()):
        if doc and doc.get("kind"):
            yield doc


def write_doc(doc, directory, prefix=""):
    directory.mkdir(parents=True, exist_ok=True)
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"]
    (directory / f"{prefix}{kind}-{name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )


def purge(directory, prefixes):
    if not directory.is_dir():
        return
    for old in directory.glob("*.yaml"):
        if any(old.name.startswith(p) for p in prefixes):
            old.unlink()


purge(dest_ops, ("deployment-", "serviceaccount-", "namespace-"))
purge(dest_cucp, (
    "namespace-", "nfdeployment-", "nfconfig-", "config-",
    "networkattachmentdefinition-", "configmap-oai-cu-cp-",
    "serviceaccount-oai-cu-cp-", "deployment-oai-cu-cp",
))
purge(dest_cluster, (
    "clusterrole-oai-ran-", "clusterrolebinding-oai-ran-",
    "customresourcedefinition-configs.ref.nephio.org",
    "customresourcedefinition-nfconfigs.workload.nephio.org",
    "customresourcedefinition-nfdeployments.workload.nephio.org",
))

for path in sorted(src_dir.glob("*.yaml")):
    for doc in load_docs(path):
        if doc["kind"] == "Deployment":
            pod_spec = doc["spec"]["template"]["spec"]
            pod_spec["containers"][0]["image"] = operator_image
            pod_spec["affinity"] = {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{
                            "matchExpressions": [{
                                "key": "kubernetes.io/hostname",
                                "operator": "In",
                                "values": ran_node_list,
                            }],
                        }],
                    },
                },
            }
        if doc["kind"] in cluster_kinds:
            write_doc(doc, dest_cluster)
        else:
            meta = doc.setdefault("metadata", {})
            if doc["kind"] != "Namespace":
                meta["namespace"] = ops_ns
            write_doc(doc, dest_ops)

for path in sorted((src_dir / "crd").glob("*.yaml")):
    for doc in load_docs(path):
        write_doc(doc, dest_cluster)

privileged_labels = {
    "pod-security.kubernetes.io/warn": "privileged",
    "pod-security.kubernetes.io/audit": "privileged",
    "pod-security.kubernetes.io/enforce": "privileged",
}

write_doc({
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {"name": ops_ns},
}, dest_ops)

write_doc({
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {"name": cucp_ns, "labels": privileged_labels},
}, dest_cucp)

nfconfig = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFConfig",
    "metadata": {"name": f"{cucp_name}-config", "namespace": cucp_ns},
    "spec": {
        "configRefs": [
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "RANConfig",
                "metadata": {"name": "ranconfig", "namespace": cucp_ns},
                "spec": {
                    "cellIdentity": 12345678,
                    "physicalCellID": 0,
                    "tac": 1,
                    "downlinkFrequencyBand": 78,
                    "downlinkSubCarrierSpacing": 1,
                    "downlinkCarrierBandwidth": 51,
                    "uplinkFrequencyBand": 78,
                    "uplinkSubCarrierSpacing": 1,
                    "uplinkCarrierBandwidth": 51,
                },
            },
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "PLMN",
                "metadata": {"name": "plmn", "namespace": cucp_ns},
                "spec": {
                    "PLMNInfo": [{
                        "plmnID": {"mcc": "001", "mnc": "01"},
                        "tac": 1,
                        "nssai": [{"sd": "ffffff", "sst": 1}],
                    }],
                },
            },
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "OAIConfig",
                "metadata": {"name": "oai-nf-config", "namespace": cucp_ns},
                "spec": {"image": gnb_image},
            },
        ],
    },
}
write_doc(nfconfig, dest_cucp, prefix="10-")

amf_ref = {
    "apiVersion": "ref.nephio.org/v1alpha1",
    "kind": "Config",
    "metadata": {"name": f"{cucp_name}-amf-central", "namespace": cucp_ns},
    "spec": {
        "config": {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFDeployment",
            "metadata": {"name": "amf-core", "namespace": "oai-cn"},
            "spec": {
                "provider": "amf.openairinterface.org",
                "interfaces": [{
                    "name": "n2",
                    "ipv4": {"address": f"{amf_n2}/24", "gateway": oai_gw},
                    "vlanID": 4,
                }],
            },
        },
    },
}
write_doc(amf_ref, dest_cucp, prefix="11-")

nfdeploy = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": cucp_name, "namespace": cucp_ns},
    "spec": {
        "provider": "cucp.openairinterface.org",
        "interfaces": [
            {
                "name": "n2",
                "ipv4": {"address": f"{cucp_n2}/24", "gateway": oai_gw},
                "vlanID": 4,
            },
            {
                "name": "f1c",
                "ipv4": {"address": f"{f1c_ip}/24", "gateway": oai_gw},
                "vlanID": 5,
            },
            {
                "name": "e1",
                "ipv4": {"address": f"{e1_ip}/24", "gateway": oai_gw},
                "vlanID": 6,
            },
        ],
        "networkInstances": [
            {"name": "vpc-ran", "interfaces": ["n2"]},
            {"name": "vpc-cudu-f1", "interfaces": ["f1c"]},
            {"name": "vpc-cu-e1", "interfaces": ["e1"]},
        ],
        "parametersRefs": [
            {
                "name": f"{cucp_name}-config",
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFConfig",
            },
            {
                "name": f"{cucp_name}-amf-central",
                "apiVersion": "ref.nephio.org/v1alpha1",
                "kind": "Config",
            },
        ],
    },
}
write_doc(nfdeploy, dest_cucp, prefix="20-")

# Executor manifests (Config Sync prunes operator-created resources not in git).
ifaces = (
    ("e1", e1_ip, oai_gw),
    ("f1c", f1c_ip, oai_gw),
    ("n2", cucp_n2, oai_gw),
)


def make_nad(suffix, address, gateway):
    nad_name = f"{cucp_name}-{suffix}"
    cfg = {
        "cniVersion": "0.3.1",
        "name": nad_name,
        "plugins": [
            {
                "type": "macvlan",
                "capabilities": {"ips": True},
                "master": nad_parent,
                "mode": "bridge",
                "ipam": {
                    "type": "static",
                    "addresses": [{"address": f"{address}/24", "gateway": gateway}],
                },
            },
            {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
        ],
    }
    return {
        "apiVersion": "k8s.cni.cncf.io/v1",
        "kind": "NetworkAttachmentDefinition",
        "metadata": {"name": nad_name, "namespace": cucp_ns},
        "spec": {"config": json.dumps(cfg)},
    }


for suffix, address, gateway in ifaces:
    write_doc(make_nad(suffix, address, gateway), dest_cucp, prefix="12-")

gnb_conf = f"""Active_gNBs = ( "oai-cu-cp");
Asn1_verbosity = "none";
sa = 1;

gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_name  =  "oai-cu-cp";
    tracking_area_code  =  1;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({{ sst = 1, sd = 0xffffff }})
                }});

    nr_cellid = 12345678;
    tr_s_preference = "f1";
    local_s_address = "{f1c_ip}";
    remote_s_address = "0.0.0.0";
    local_s_portc   = 501;
    local_s_portd   = 2152;
    remote_s_portc  = 500;
    remote_s_portd  = 2152;

    SCTP :
    {{
        SCTP_INSTREAMS  = 2;
        SCTP_OUTSTREAMS = 2;
    }};

    amf_ip_address      = ( {{ ipv4       = "{amf_n2}"; }});

    E1_INTERFACE =
    (
      {{
        type = "cp";
        ipv4_cucp = "{e1_ip}";
        port_cucp = 38462;
        ipv4_cuup = "0.0.0.0";
        port_cuup = 38462;
      }}
    )

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{cucp_n2}";
    }};
  }}
);

security = {{
  ciphering_algorithms = ( "nea0" );
  integrity_algorithms = ( "nia2", "nia0" );
  drb_ciphering = "yes";
  drb_integrity = "no";
}};
log_config :
{{
global_log_level                      ="info";
hw_log_level                          ="info";
phy_log_level                         ="info";
mac_log_level                         ="info";
rlc_log_level                         ="debug";
pdcp_log_level                        ="info";
rrc_log_level                         ="info";
f1ap_log_level                         ="info";
ngap_log_level                         ="debug";
}};
"""

write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-cu-cp-configmap", "namespace": cucp_ns},
    "data": {"gnb.conf": gnb_conf},
}, dest_cucp, prefix="13-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-cu-cp-sa", "namespace": cucp_ns},
}, dest_cucp, prefix="14-")

networks = []
for suffix, address, gateway in ifaces:
    networks.append({
        "name": f"{cucp_name}-{suffix}",
        "interface": suffix,
        "ips": [f"{address}/24"],
        "gateways": [gateway],
    })
networks_json = "[\n" + ",\n".join(
    " {\n"
    f'  "name": {json.dumps(n["name"])},\n'
    f'  "interface": {json.dumps(n["interface"])},\n'
    f'  "ips": [{json.dumps(n["ips"][0])}],\n'
    f'  "gateways": [{json.dumps(n["gateways"][0])}]\n'
    " }"
    for n in networks
) + "\n]"

write_doc({
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "oai-cu-cp",
        "namespace": cucp_ns,
        "labels": {"app.kubernetes.io/name": "oai-cu-cp"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-cu-cp"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-cu-cp-cp",
                    "app.kubernetes.io/name": "oai-cu-cp",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-cu-cp-sa",
                "terminationGracePeriodSeconds": 5,
                "containers": [{
                    "name": "cucp",
                    "image": gnb_image,
                    "securityContext": {"privileged": True},
                    "env": [
                        {"name": "TZ", "value": "Asia/Singapore"},
                        {
                            "name": "USE_ADDITIONAL_OPTIONS",
                            "value": "--sa --log_config.global_log_options level,nocolor,time",
                        },
                        {"name": "USE_VOLUMED_CONF", "value": "yes"},
                    ],
                    "ports": [
                        {"name": "n2", "containerPort": 36412, "protocol": "SCTP"},
                        {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                        {"name": "f1c", "containerPort": 38472, "protocol": "UDP"},
                    ],
                    "volumeMounts": [{
                        "name": "configuration",
                        "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                        "subPath": "gnb.conf",
                    }],
                }, debug_container],
                "volumes": [{
                    "name": "configuration",
                    "configMap": {"name": "oai-cu-cp-configmap"},
                }],
            },
        },
    },
}, dest_cucp, prefix="15-")

print(f"  namespaces/{ops_ns}: operator")
print(f"  namespaces/{cucp_ns}: CU-CP NFDeployment + executor ({cucp_name})")
print(f"  CU-CP N2 {cucp_n2} -> central AMF N2 {amf_n2}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI RAN → ${OAI_RAN_OPERATORS_NS}, ${OAI_RAN_CUCP_NS})"
}

main() {
  local clusters=("$@")
  local src_dir=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(regional)
  fi

  for cluster in "${clusters[@]}"; do
    local ok=0
    for allowed in "${RAN_CUCP_CLUSTERS[@]}"; do
      [[ "$cluster" == "$allowed" ]] && ok=1
    done
    if [[ "$ok" -ne 1 ]]; then
      echo "error: OAI RAN CU-CP deploys on regional only, not '${cluster}'" >&2
      exit 1
    fi
  done

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  src_dir="$(fetch_operator_manifests)"
  trap 'rm -rf "${src_dir:-}"' EXIT

  for cluster in "${clusters[@]}"; do
    write_ran_gitops "$cluster" "$src_dir"
  done

  echo
  echo "Namespaces: ${OAI_RAN_OPERATORS_NS} (operator), ${OAI_RAN_CUCP_NS} (CU-CP executor)"
  echo "Prerequisites on target cluster: Multus (./scripts/render_multus_gitops.sh <cluster>)"
  echo "Central AMF must be running; CU-CP N2 $(oai_macvlan_ip regional 0) → AMF N2 $(amf_n2_vip central) on 10.1.139.0/24"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write OAI RAN operator + CU-CP NFDeployment to repos/<gitea-repo>/ for Config Sync.
Default cluster: regional.

Namespaces:
  ${OAI_RAN_OPERATORS_NS}  — nephio/oai-ran-controller
  ${OAI_RAN_CUCP_NS}       — CU-CP NFDeployment + executor (NADs, ConfigMap, Deployment)

Requires Multus on the workload cluster. Central OAI AMF must be deployed first.

Environment:
  OAI_RAN_OPERATOR_IMAGE  Operator image (default: docker.io/nephio/oai-ran-controller:latest)
  OAI_GNB_IMAGE           CU-CP gNB image (default: docker.io/oaisoftwarealliance/oai-gnb:v2.3.0)
  REPOS_DIR               Output tree (default: repos/)
EOF
  exit 0
fi

main "$@"
