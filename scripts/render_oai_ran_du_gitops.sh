#!/usr/bin/env bash
# Render OAI RAN operator + DU NFDeployment (with rfsim RU) into repos/ for Config Sync GitOps.
# Operator: nephio-project/catalog workloads/oai/oai-ran-operator
# Executor: nephio-project/oai (NFDeployment provider du.openairinterface.org)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_RAN_OPERATORS_NS="${OAI_RAN_OPERATORS_NS:-oai-ran-operators}"
OAI_RAN_DU_NS="${OAI_RAN_DU_NS:-oai-ran-du}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-docker.io/nephio/oai-ran-controller:latest}"
OAI_GNB_IMAGE="${OAI_GNB_IMAGE:-docker.io/oaisoftwarealliance/oai-gnb:v2.3.0}"
RAN_CATALOG_BASE="${RAN_CATALOG_BASE:-https://raw.githubusercontent.com/nephio-project/catalog/main/workloads/oai/oai-ran-operator/operator}"
NEPHIO_CRD_BASE="${NEPHIO_CRD_BASE:-https://raw.githubusercontent.com/nephio-project/api/main/config/crd/bases}"
NEPHIO_CRD_MANIFESTS=(
  ref.nephio.org_configs.yaml
  workload.nephio.org_nfdeployments.yaml
  workload.nephio.org_nfconfigs.yaml
)
# DU + simulated RU (rfsim) at edge.
DU_CLUSTERS=(edge)
# Regional CU-CP F1-C on shared vpc-cudu-f1 L2.
CUCP_CLUSTER="${CUCP_CLUSTER:-regional}"
CUCP_F1C_IP="${CUCP_F1C_IP:-$(oai_macvlan_ip regional 1)}"
CUCP_F1C_GW="${CUCP_F1C_GW:-$OAI_MACVLAN_GW}"
declare -A DU_F1_IP=(
  [edge]="$(oai_macvlan_ip edge 3)"
)

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

write_du_gitops() {
  local cluster="$1"
  local src_dir="$2"
  local repo_name dest_ops dest_du dest_cluster
  local du_f1 du_name cucp_name

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ops="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_OPERATORS_NS}"
  dest_du="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_DU_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_ops" "$dest_du" "$dest_cluster"

  du_f1="${DU_F1_IP[$cluster]}"
  du_name="du-${cluster}"
  cucp_name="cucp-${CUCP_CLUSTER}"
  # Pin DU to CP/worker only (exclude usrp and any other edge nodes).
  ran_nodes="${CLUSTER_CP_HOST[$cluster]},${CLUSTER_WORKER_HOST[$cluster]}"

  python3 - "$src_dir" "$dest_ops" "$dest_du" "$dest_cluster" \
    "$OAI_RAN_OPERATORS_NS" "$OAI_RAN_DU_NS" "$OAI_RAN_OPERATOR_IMAGE" \
    "$du_name" "$du_f1" "$CUCP_F1C_IP" "$CUCP_F1C_GW" "$cucp_name" \
    "$CUCP_CLUSTER" "$OAI_GNB_IMAGE" "$SITE_IFACE" "$SCRIPT_DIR/oai_debug_sidecar.py" \
    "$OAI_DEBUG_SIDECAR_IMAGE" "$ran_nodes" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

import yaml

(src_dir, dest_ops, dest_du, dest_cluster, ops_ns, du_ns, operator_image,
 du_name, du_f1, cucp_f1c, cucp_f1c_gw, cucp_name, cucp_cluster, gnb_image,
 nad_parent, debug_lib, debug_image, ran_nodes) = sys.argv[1:19]
ran_node_list = [n for n in ran_nodes.split(",") if n]
spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_container = oai_debug.debug_sidecar_container(debug_image)

dest_ops = Path(dest_ops)
dest_du = Path(dest_du)
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
purge(dest_du, (
    "namespace-", "nfdeployment-", "nfconfig-", "config-",
    "networkattachmentdefinition-", "configmap-oai-du-",
    "serviceaccount-oai-du-", "deployment-oai-du", "service-oai-du",
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
    "metadata": {"name": du_ns, "labels": privileged_labels},
}, dest_du)

nfconfig = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFConfig",
    "metadata": {"name": f"{du_name}-config", "namespace": du_ns},
    "spec": {
        "configRefs": [
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "RANConfig",
                "metadata": {"name": "ranconfig", "namespace": du_ns},
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
                "metadata": {"name": "plmn", "namespace": du_ns},
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
                "metadata": {"name": "oai-nf-config", "namespace": du_ns},
                "spec": {"image": gnb_image},
            },
        ],
    },
}
write_doc(nfconfig, dest_du, prefix="10-")

cucp_ref = {
    "apiVersion": "ref.nephio.org/v1alpha1",
    "kind": "Config",
    "metadata": {"name": f"{du_name}-cucp-{cucp_cluster}", "namespace": du_ns},
    "spec": {
        "config": {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFDeployment",
            "metadata": {"name": cucp_name, "namespace": "oai-ran-cucp"},
            "spec": {
                "provider": "cucp.openairinterface.org",
                "interfaces": [{
                    "name": "f1c",
                    "ipv4": {"address": f"{cucp_f1c}/24", "gateway": cucp_f1c_gw},
                    "vlanID": 5,
                }],
            },
        },
    },
}
write_doc(cucp_ref, dest_du, prefix="11-")

nfdeploy = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": du_name, "namespace": du_ns},
    "spec": {
        "provider": "du.openairinterface.org",
        "interfaces": [{
            "name": "f1",
            "ipv4": {"address": f"{du_f1}/24", "gateway": cucp_f1c_gw},
            "vlanID": 5,
        }],
        "networkInstances": [{
            "name": "vpc-cudu-f1",
            "interfaces": ["f1"],
        }],
        "parametersRefs": [
            {
                "name": f"{du_name}-config",
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFConfig",
            },
            {
                "name": f"{du_name}-cucp-{cucp_cluster}",
                "apiVersion": "ref.nephio.org/v1alpha1",
                "kind": "Config",
            },
        ],
    },
}
write_doc(nfdeploy, dest_du, prefix="20-")


def make_nad(suffix, address, gateway):
    nad_name = f"{du_name}-{suffix}"
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
        "metadata": {"name": nad_name, "namespace": du_ns},
        "spec": {"config": json.dumps(cfg)},
    }


write_doc(make_nad("f1", du_f1, cucp_f1c_gw), dest_du, prefix="12-")

# gnb.conf from nephio oai DU template (rfsim RU block included).
gnb_conf = f"""Active_gNBs = ( "oai-du");
Asn1_verbosity = "none";
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_DU_ID = 0xe00;
    gNB_name  =  "oai-du";
    tracking_area_code  =  1;
    plmn_list = ({{ mcc = 001; mnc = 01; mnc_length = 2; snssaiList = ({{ sst = 1, sd = 0xffffff }}) }});
    nr_cellid = 12345678;
    min_rxtxtime = 6;
    servingCellConfigCommon = (
    {{
      absoluteFrequencySSB = 640704;
      dl_frequencyBand = 78;
      dl_absoluteFrequencyPointA = 639996;
      dl_offstToCarrier = 0;
      dl_subcarrierSpacing = 1;
      dl_carrierBandwidth = 51;
      initialDLBWPlocationAndBandwidth = 13750;
      initialDLBWPsubcarrierSpacing = 1;
      initialDLBWPcontrolResourceSetZero = 12;
      initialDLBWPsearchSpaceZero = 0;
      ul_frequencyBand = 78;
      ul_offstToCarrier = 0;
      ul_subcarrierSpacing = 1;
      ul_carrierBandwidth = 51;
      pMax = 20;
      initialULBWPlocationAndBandwidth = 13750;
      initialULBWPsubcarrierSpacing = 1;
      prach_ConfigurationIndex = 98;
      prach_msg1_FDM = 0;
      prach_msg1_FrequencyStart = 0;
      zeroCorrelationZoneConfig = 13;
      preambleReceivedTargetPower = -96;
      preambleTransMax = 6;
      powerRampingStep = 1;
      ra_ResponseWindow = 4;
      ssb_perRACH_OccasionAndCB_PreamblesPerSSB_PR = 4;
      ssb_perRACH_OccasionAndCB_PreamblesPerSSB = 14;
      ra_ContentionResolutionTimer = 7;
      rsrp_ThresholdSSB = 19;
      prach_RootSequenceIndex_PR = 2;
      prach_RootSequenceIndex = 1;
      msg1_SubcarrierSpacing = 1,
      restrictedSetConfig = 0,
      msg3_DeltaPreamble = 1;
      p0_NominalWithGrant =-90;
      pucchGroupHopping = 0;
      hoppingId = 40;
      p0_nominal = -90;
      ssb_PositionsInBurst_Bitmap = 1;
      ssb_periodicityServingCell = 2;
      dmrs_TypeA_Position = 0;
      subcarrierSpacing = 1;
      referenceSubcarrierSpacing = 1;
      dl_UL_TransmissionPeriodicity = 6;
      nrofDownlinkSlots = 7;
      nrofDownlinkSymbols = 6;
      nrofUplinkSlots = 2;
      nrofUplinkSymbols = 4;
      ssPBCH_BlockPower = -25;
     }}
  );
    SCTP :
    {{
        SCTP_INSTREAMS  = 2;
        SCTP_OUTSTREAMS = 2;
    }};
  }}
);

MACRLCs = (
  {{
    num_cc = 1;
    tr_s_preference = "local_L1";
    tr_n_preference = "f1";
    local_n_address = "{du_f1}";
    remote_n_address = "{cucp_f1c}";
    local_n_portc = 500;
    local_n_portd = 2152;
    remote_n_portc = 501;
    remote_n_portd = 2152;
    pusch_TargetSNRx10 = 200;
    pucch_TargetSNRx10 = 200;
  }}
);

L1s = (
{{
  num_cc = 1;
  tr_n_preference = "local_mac";
  prach_dtx_threshold = 200;
  pucch0_dtx_threshold = 150;
  ofdm_offset_divisor = 8;
}}
);

RUs = (
    {{
    local_rf = "yes"
    nb_tx = 1
    nb_rx = 1
    att_tx = 0
    att_rx = 0;
    bands = [78];
    max_pdschReferenceSignalPower = -27;
    max_rxgain = 114;
    eNB_instances = [0];
    bf_weights = [0x00007fff, 0x0000, 0x0000, 0x0000];
    clock_src = "internal";
    }}
);

THREAD_STRUCT = (
  {{
    parallel_config = "PARALLEL_SINGLE_THREAD";
    worker_config = "WORKER_ENABLE";
  }}
);
rfsimulator: {{
    serveraddr = "server";
    serverport = "4043";
    options = ();
    modelname = "AWGN";
    IQfile = "/tmp/rfsimulator.iqs"
}}

log_config :
{{
    global_log_level = "info";
    hw_log_level = "info";
    phy_log_level = "info";
    mac_log_level = "info";
    rlc_log_level = "info";
    f1ap_log_level = "info";
}};
"""

write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-du-configmap", "namespace": du_ns},
    "data": {"gnb.conf": gnb_conf},
}, dest_du, prefix="13-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-du-sa", "namespace": du_ns},
}, dest_du, prefix="14-")

networks_json = (
    "[\n {\n"
    f'  "name": {json.dumps(f"{du_name}-f1")},\n'
    '  "interface": "f1",\n'
    f'  "ips": [{json.dumps(f"{du_f1}/24")}],\n'
    f'  "gateways": [{json.dumps(cucp_f1c_gw)}]\n'
    " }\n]"
)

write_doc({
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "oai-du",
        "namespace": du_ns,
        "labels": {"app.kubernetes.io/name": "oai-du"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-du"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-du",
                    "app.kubernetes.io/name": "oai-du",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-du-sa",
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
                    "name": "du",
                    "image": gnb_image,
                    "securityContext": {"privileged": True},
                    "env": [{
                        "name": "USE_ADDITIONAL_OPTIONS",
                        "value": (
                            "--sa --rfsim --log_config.global_log_options level,nocolor,time"
                            " --telnetsrv --telnetsrv.shrmod o1 --telnetsrv.listenaddr 192.168.74.2"
                        ),
                    }],
                    "ports": [
                        {"name": "f1c", "containerPort": 38472, "protocol": "SCTP"},
                        {"name": "f1u", "containerPort": 2152, "protocol": "UDP"},
                    ],
                    "resources": {
                        "requests": {"cpu": "2000m", "memory": "1Gi"},
                        "limits": {"cpu": "2000m", "memory": "2Gi"},
                    },
                    "volumeMounts": [{
                        "name": "configuration",
                        "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                        "subPath": "gnb.conf",
                    }],
                }, debug_container],
                "volumes": [{
                    "name": "configuration",
                    "configMap": {"name": "oai-du-configmap"},
                }],
            },
        },
    },
}, dest_du, prefix="15-")

write_doc({
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {
        "name": "oai-du",
        "namespace": du_ns,
        "labels": {"app.kubernetes.io/name": "oai-du"},
    },
    "spec": {
        "type": "ClusterIP",
        "clusterIP": "None",
        "selector": {"app.kubernetes.io/name": "oai-du"},
        "ports": [
            {"name": "f1c", "port": 38472, "protocol": "SCTP", "targetPort": 38472},
            {"name": "f1u", "port": 2152, "protocol": "UDP", "targetPort": 2152},
        ],
    },
}, dest_du, prefix="16-")

print(f"  namespaces/{ops_ns}: operator")
print(f"  namespaces/{du_ns}: DU NFDeployment + executor + rfsim RU ({du_name})")
print(f"  DU F1 {du_f1} -> {cucp_cluster} CU-CP F1-C {cucp_f1c}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI RAN DU → ${OAI_RAN_OPERATORS_NS}, ${OAI_RAN_DU_NS})"
}

main() {
  local clusters=("$@")
  local src_dir=""

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(edge)
  fi

  for cluster in "${clusters[@]}"; do
    local ok=0
    for allowed in "${DU_CLUSTERS[@]}"; do
      [[ "$cluster" == "$allowed" ]] && ok=1
    done
    if [[ "$ok" -ne 1 ]]; then
      echo "error: OAI RAN DU deploys on edge (ue later), not '${cluster}'" >&2
      exit 1
    fi
    if [[ -z "${DU_F1_IP[$cluster]:-}" ]]; then
      echo "error: no F1 IP defined for DU cluster '${cluster}'" >&2
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
    write_du_gitops "$cluster" "$src_dir"
  done

  echo
  echo "Namespaces: ${OAI_RAN_OPERATORS_NS} (operator), ${OAI_RAN_DU_NS} (DU + rfsim RU)"
  echo "Prerequisites: Multus (./scripts/render_multus_gitops.sh <cluster>)"
  echo "Regional CU-CP must be running (F1-C ${CUCP_F1C_IP} on vpc-cudu-f1)"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write OAI RAN operator + DU (with rfsim RU) to repos/<gitea-repo>/ for Config Sync.
Default cluster: edge.

Namespaces:
  ${OAI_RAN_OPERATORS_NS}  — nephio/oai-ran-controller
  ${OAI_RAN_DU_NS}         — DU NFDeployment + executor (NADs, ConfigMap, Deployment)

Requires Multus and regional CU-CP (F1 toward ${CUCP_F1C_IP}).

Environment:
  OAI_RAN_OPERATOR_IMAGE  Operator image (default: docker.io/nephio/oai-ran-controller:latest)
  OAI_GNB_IMAGE           DU gNB image (default: docker.io/oaisoftwarealliance/oai-gnb:v2.3.0)
  CUCP_CLUSTER            CU-CP site (default: regional)
  CUCP_F1C_IP             Regional CU-CP F1-C IP (default: ${CUCP_F1C_IP})
  REPOS_DIR               Output tree (default: repos/)
EOF
  exit 0
fi

main "$@"
