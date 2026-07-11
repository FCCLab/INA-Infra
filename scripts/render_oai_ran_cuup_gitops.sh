#!/usr/bin/env bash
# Render OAI RAN operator + CU-UP NFDeployment into repos/ for Config Sync GitOps.
# Operator: nephio-project/catalog workloads/oai/oai-ran-operator
# Executor: nephio-project/oai (NFDeployment provider cuup.openairinterface.org)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_RAN_OPERATORS_NS="${OAI_RAN_OPERATORS_NS:-oai-ran-operators}"
OAI_RAN_CUUP_NS="${OAI_RAN_CUUP_NS:-oai-ran-cuup}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-docker.io/nephio/oai-ran-controller:latest}"
OAI_CUUP_IMAGE="${OAI_CUUP_IMAGE:-docker.io/oaisoftwarealliance/oai-nr-cuup:v2.3.0}"
RAN_CATALOG_BASE="${RAN_CATALOG_BASE:-https://raw.githubusercontent.com/nephio-project/catalog/main/workloads/oai/oai-ran-operator/operator}"
NEPHIO_CRD_BASE="${NEPHIO_CRD_BASE:-https://raw.githubusercontent.com/nephio-project/api/main/config/crd/bases}"
NEPHIO_CRD_MANIFESTS=(
  ref.nephio.org_configs.yaml
  workload.nephio.org_nfdeployments.yaml
  workload.nephio.org_nfconfigs.yaml
)
CUUP_CLUSTERS=(edge)
CUCP_CLUSTER="${CUCP_CLUSTER:-regional}"
UPF_CLUSTER="${UPF_CLUSTER:-central}"
CUCP_E1_IP="${CUCP_E1_IP:-$(oai_macvlan_ip regional 2)}"
CUCP_E1_GW="${CUCP_E1_GW:-$OAI_MACVLAN_GW}"
UPF_N3_IP="${UPF_N3_IP:-$(oai_macvlan_ip central 1)}"
UPF_N3_GW="${UPF_N3_GW:-$OAI_MACVLAN_GW}"
declare -A CUUP_E1_IP=([edge]="$(oai_macvlan_ip edge 0)")
declare -A CUUP_F1U_IP=([edge]="$(oai_macvlan_ip edge 1)")
declare -A CUUP_N3_IP=([edge]="$(oai_macvlan_ip edge 2)")
CUUP_F1U_GW="${CUUP_F1U_GW:-$OAI_MACVLAN_GW}"

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

write_cuup_gitops() {
  local cluster="$1"
  local src_dir="$2"
  local repo_name dest_ops dest_cuup dest_cluster
  local cuup_e1 cuup_f1u cuup_n3 cuup_name cucp_name

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ops="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_OPERATORS_NS}"
  dest_cuup="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_CUUP_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_ops" "$dest_cuup" "$dest_cluster"

  cuup_e1="${CUUP_E1_IP[$cluster]}"
  cuup_f1u="${CUUP_F1U_IP[$cluster]}"
  cuup_n3="${CUUP_N3_IP[$cluster]}"
  cuup_name="cuup-${cluster}"
  cucp_name="cucp-${CUCP_CLUSTER}"
  # Pin CU-UP to CP/worker only (exclude usrp and any other edge nodes).
  ran_nodes="${CLUSTER_CP_HOST[$cluster]},${CLUSTER_WORKER_HOST[$cluster]}"

  python3 - "$src_dir" "$dest_ops" "$dest_cuup" "$dest_cluster" \
    "$OAI_RAN_OPERATORS_NS" "$OAI_RAN_CUUP_NS" "$OAI_RAN_OPERATOR_IMAGE" \
    "$cuup_name" "$cuup_e1" "$cuup_f1u" "$cuup_n3" \
    "$CUCP_E1_IP" "$CUCP_E1_GW" "$CUUP_F1U_GW" "$UPF_N3_IP" "$UPF_N3_GW" \
    "$cucp_name" "$CUCP_CLUSTER" "$UPF_CLUSTER" "$OAI_CUUP_IMAGE" "$SITE_IFACE" \
    "$SCRIPT_DIR/oai_debug_sidecar.py" "$OAI_DEBUG_SIDECAR_IMAGE" "$ran_nodes" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

import yaml

(src_dir, dest_ops, dest_cuup, dest_cluster, ops_ns, cuup_ns, operator_image,
 cuup_name, cuup_e1, cuup_f1u, cuup_n3, cucp_e1, cucp_e1_gw, f1u_gw, upf_n3, upf_n3_gw,
 cucp_name, cucp_cluster, upf_cluster, cuup_image, nad_parent,
 debug_lib, debug_image, ran_nodes) = sys.argv[1:25]
ran_node_list = [n for n in ran_nodes.split(",") if n]
spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_container = oai_debug.debug_sidecar_container(debug_image)

dest_ops = Path(dest_ops)
dest_cuup = Path(dest_cuup)
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
purge(dest_cuup, (
    "namespace-", "nfdeployment-", "nfconfig-", "config-",
    "networkattachmentdefinition-", "configmap-oai-cu-up-",
    "serviceaccount-oai-cu-up-", "deployment-oai-cu-up",
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
    "metadata": {"name": cuup_ns, "labels": privileged_labels},
}, dest_cuup)

nfconfig = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFConfig",
    "metadata": {"name": f"{cuup_name}-config", "namespace": cuup_ns},
    "spec": {
        "configRefs": [
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "PLMN",
                "metadata": {"name": "plmn", "namespace": cuup_ns},
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
                "metadata": {"name": "oai-nf-config", "namespace": cuup_ns},
                "spec": {"image": cuup_image},
            },
        ],
    },
}
write_doc(nfconfig, dest_cuup, prefix="10-")

cucp_ref = {
    "apiVersion": "ref.nephio.org/v1alpha1",
    "kind": "Config",
    "metadata": {"name": f"{cuup_name}-cucp-{cucp_cluster}", "namespace": cuup_ns},
    "spec": {
        "config": {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFDeployment",
            "metadata": {"name": cucp_name, "namespace": "oai-ran-cucp"},
            "spec": {
                "provider": "cucp.openairinterface.org",
                "interfaces": [{
                    "name": "e1",
                    "ipv4": {"address": f"{cucp_e1}/24", "gateway": cucp_e1_gw},
                    "vlanID": 6,
                }],
            },
        },
    },
}
write_doc(cucp_ref, dest_cuup, prefix="11-")

upf_ref = {
    "apiVersion": "ref.nephio.org/v1alpha1",
    "kind": "Config",
    "metadata": {"name": f"{cuup_name}-upf-{upf_cluster}", "namespace": cuup_ns},
    "spec": {
        "config": {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFDeployment",
            "metadata": {"name": "upf-core", "namespace": "oai-upf"},
            "spec": {
                "provider": "upf.openairinterface.org",
                "interfaces": [{
                    "name": "n3",
                    "ipv4": {"address": f"{upf_n3}/24", "gateway": upf_n3_gw},
                    "vlanID": 4,
                }],
            },
        },
    },
}
write_doc(upf_ref, dest_cuup, prefix="12-")

nfdeploy = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": cuup_name, "namespace": cuup_ns},
    "spec": {
        "provider": "cuup.openairinterface.org",
        "interfaces": [
            {
                "name": "e1",
                "ipv4": {"address": f"{cuup_e1}/24", "gateway": cucp_e1_gw},
                "vlanID": 6,
            },
            {
                "name": "f1u",
                "ipv4": {"address": f"{cuup_f1u}/24", "gateway": f1u_gw},
                "vlanID": 5,
            },
            {
                "name": "n3",
                "ipv4": {"address": f"{cuup_n3}/24", "gateway": upf_n3_gw},
                "vlanID": 4,
            },
        ],
        "networkInstances": [
            {"name": "vpc-cu-e1", "interfaces": ["e1"]},
            {"name": "vpc-cudu-f1", "interfaces": ["f1u"]},
            {"name": "vpc-ran", "interfaces": ["n3"]},
        ],
        "parametersRefs": [
            {
                "name": f"{cuup_name}-config",
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFConfig",
            },
            {
                "name": f"{cuup_name}-cucp-{cucp_cluster}",
                "apiVersion": "ref.nephio.org/v1alpha1",
                "kind": "Config",
            },
            {
                "name": f"{cuup_name}-upf-{upf_cluster}",
                "apiVersion": "ref.nephio.org/v1alpha1",
                "kind": "Config",
            },
        ],
    },
}
write_doc(nfdeploy, dest_cuup, prefix="20-")


def make_nad(suffix, address, gateway):
    nad_name = f"{cuup_name}-{suffix}"
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
        "metadata": {"name": nad_name, "namespace": cuup_ns},
        "spec": {"config": json.dumps(cfg)},
    }


for suffix, address, gateway in (
    ("e1", cuup_e1, cucp_e1_gw),
    ("f1u", cuup_f1u, f1u_gw),
    ("n3", cuup_n3, upf_n3_gw),
):
    write_doc(make_nad(suffix, address, gateway), dest_cuup, prefix="30-")

gnb_conf = f"""Active_gNBs = ( "oai-cu-up");
Asn1_verbosity = "none";
sa = 1;
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_CU_UP_ID = 0xe00;
    gNB_name  =  "oai-cu-up";
    tracking_area_code  =  1;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({{ sst = 1, sd = 0xffffff }})
                }});

    tr_s_preference = "f1";
    local_s_address = "{cuup_f1u}";
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

    E1_INTERFACE =
    (
      {{
        type = "up";
        ipv4_cucp = "{cucp_e1}";
        ipv4_cuup = "{cuup_e1}";
      }}
    )

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{cuup_n3}";
        GNB_IPV4_ADDRESS_FOR_NGU                 = "{cuup_n3}";
        GNB_PORT_FOR_S1U                         = 2152;
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
pdcp_log_level                        ="info";
f1ap_log_level                        ="info";
ngap_log_level                        ="info";
}};
"""

write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-cu-up-configmap", "namespace": cuup_ns},
    "data": {"gnb.conf": gnb_conf},
}, dest_cuup, prefix="31-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-cu-up-sa", "namespace": cuup_ns},
}, dest_cuup, prefix="32-")

ifaces = (
    ("e1", cuup_e1, cucp_e1_gw),
    ("f1u", cuup_f1u, f1u_gw),
    ("n3", cuup_n3, upf_n3_gw),
)
networks = []
for suffix, address, gateway in ifaces:
    networks.append({
        "name": f"{cuup_name}-{suffix}",
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
        "name": "oai-cu-up",
        "namespace": cuup_ns,
        "labels": {"app.kubernetes.io/name": "oai-cu-up"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-cu-up"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-cu-up",
                    "app.kubernetes.io/name": "oai-cu-up",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-cu-up-sa",
                "terminationGracePeriodSeconds": 5,
                "affinity": {
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
                },
                "containers": [{
                    "name": "cuup",
                    "image": cuup_image,
                    "securityContext": {"privileged": True},
                    "env": [
                        {"name": "TZ", "value": "Europe/Paris"},
                        {
                            "name": "USE_ADDITIONAL_OPTIONS",
                            "value": "--sa --log_config.global_log_options level,nocolor,time",
                        },
                        {"name": "USE_VOLUMED_CONF", "value": "yes"},
                    ],
                    "ports": [
                        {"name": "n3", "containerPort": 2152, "protocol": "UDP"},
                        {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                    ],
                    "volumeMounts": [{
                        "name": "configuration",
                        "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                        "subPath": "gnb.conf",
                    }],
                }, debug_container],
                "volumes": [{
                    "name": "configuration",
                    "configMap": {"name": "oai-cu-up-configmap"},
                }],
            },
        },
    },
}, dest_cuup, prefix="33-")

print(f"  namespaces/{ops_ns}: operator")
print(f"  namespaces/{cuup_ns}: CU-UP NFDeployment + executor ({cuup_name})")
print(f"  E1 {cuup_e1} -> {cucp_cluster} CU-CP E1 {cucp_e1}")
print(f"  F1U {cuup_f1u}, N3 {cuup_n3} -> {upf_cluster} UPF N3 {upf_n3}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI RAN CU-UP → ${OAI_RAN_OPERATORS_NS}, ${OAI_RAN_CUUP_NS})"
}

main() {
  local clusters=("$@")
  local src_dir=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(edge)
  fi

  for cluster in "${clusters[@]}"; do
    local ok=0
    for allowed in "${CUUP_CLUSTERS[@]}"; do
      [[ "$cluster" == "$allowed" ]] && ok=1
    done
    if [[ "$ok" -ne 1 ]]; then
      echo "error: OAI RAN CU-UP deploys on edge, not '${cluster}'" >&2
      exit 1
    fi
    if [[ -z "${CUUP_E1_IP[$cluster]:-}" ]]; then
      echo "error: no CU-UP E1 IP defined for cluster '${cluster}'" >&2
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
    write_cuup_gitops "$cluster" "$src_dir"
  done

  echo
  echo "Namespaces: ${OAI_RAN_OPERATORS_NS} (operator), ${OAI_RAN_CUUP_NS} (CU-UP executor)"
  echo "Prerequisites: Multus; regional CU-CP (E1 ${CUCP_E1_IP}); central UPF (N3 ${UPF_N3_IP})"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write OAI RAN operator + CU-UP to repos/<gitea-repo>/ for Config Sync.
Default cluster: edge.

Namespaces:
  ${OAI_RAN_OPERATORS_NS}  — nephio/oai-ran-controller
  ${OAI_RAN_CUUP_NS}       — CU-UP NFDeployment + executor (NADs, ConfigMap, Deployment)

Requires Multus, regional CU-CP, and central UPF.

Environment:
  OAI_RAN_OPERATOR_IMAGE  Operator image (default: docker.io/nephio/oai-ran-controller:latest)
  OAI_CUUP_IMAGE          CU-UP image (default: docker.io/oaisoftwarealliance/oai-nr-cuup:v2.3.0)
  CUCP_CLUSTER            CU-CP site (default: regional)
  CUCP_E1_IP              Regional CU-CP E1 IP (default: ${CUCP_E1_IP})
  UPF_N3_IP               Central UPF N3 IP (default: ${UPF_N3_IP})
  REPOS_DIR               Output tree (default: repos/)
EOF
  exit 0
fi

main "$@"
