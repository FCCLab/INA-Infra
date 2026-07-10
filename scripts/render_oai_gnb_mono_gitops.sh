#!/usr/bin/env bash
# Render OAI RAN operator + monolithic gNB NFDeployment (with rfsim) into repos/ for Config Sync GitOps.
# Operator: nephio-project/catalog workloads/oai/oai-ran-operator
# Executor: nephio-project/oai
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_RAN_OPERATORS_NS="${OAI_RAN_OPERATORS_NS:-oai-ran-operators}"
OAI_RAN_GNB_NS="${OAI_RAN_GNB_NS:-oai-ran-gnb}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-docker.io/nephio/oai-ran-controller:latest}"
OAI_GNB_IMAGE="${OAI_GNB_IMAGE:-10.1.132.30:5000/oai-gnb:nws-v0.2}"
RAN_CATALOG_BASE="${RAN_CATALOG_BASE:-https://raw.githubusercontent.com/nephio-project/catalog/main/workloads/oai/oai-ran-operator/operator}"
NEPHIO_CRD_BASE="${NEPHIO_CRD_BASE:-https://raw.githubusercontent.com/nephio-project/api/main/config/crd/bases}"
NEPHIO_CRD_MANIFESTS=(
  ref.nephio.org_configs.yaml
  workload.nephio.org_nfdeployments.yaml
  workload.nephio.org_nfconfigs.yaml
)

DU_CLUSTERS=(edge)
AMF_N2_IP="${AMF_N2_IP:-$(amf_n2_vip central)}"
OAI_MACVLAN_GW="${OAI_MACVLAN_GW:-10.1.139.1}"

declare -A GNB_MONO_IP=(
  [edge]="$(oai_macvlan_ip edge 3)" # 10.1.139.113
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

write_gnb_mono_gitops() {
  local cluster="$1"
  local src_dir="$2"
  local repo_name dest_ops dest_gnb dest_cluster
  local gnb_ip gnb_name

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ops="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_OPERATORS_NS}"
  dest_gnb="${REPOS_DIR}/${repo_name}/namespaces/${OAI_RAN_GNB_NS}"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  mkdir -p "$dest_ops" "$dest_gnb" "$dest_cluster"

  gnb_ip="${GNB_MONO_IP[$cluster]}"
  gnb_name="gnb-${cluster}"

  # Mono gNB is pinned to usrp; NAD macvlan master must match usrp's site NIC.
  python3 - "$src_dir" "$dest_ops" "$dest_gnb" "$dest_cluster" \
    "$OAI_RAN_OPERATORS_NS" "$OAI_RAN_GNB_NS" "$OAI_RAN_OPERATOR_IMAGE" \
    "$gnb_name" "$gnb_ip" "$AMF_N2_IP" "$OAI_MACVLAN_GW" \
    "$OAI_GNB_IMAGE" "$USRP_SITE_IFACE" "$SCRIPT_DIR/oai_debug_sidecar.py" \
    "$OAI_DEBUG_SIDECAR_IMAGE" <<'PY'
import sys
import json
import importlib.util
from pathlib import Path
import yaml

(src_dir, dest_ops, dest_gnb, dest_cluster, ops_ns, gnb_ns, operator_image,
 gnb_name, gnb_ip, amf_n2_ip, oai_gw, gnb_image, nad_parent, debug_lib, debug_image) = sys.argv[1:16]

spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_container = oai_debug.debug_sidecar_container(debug_image)

dest_ops = Path(dest_ops)
dest_gnb = Path(dest_gnb)
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
purge(dest_gnb, (
    "namespace-", "nfdeployment-", "nfconfig-", "config-",
    "networkattachmentdefinition-", "configmap-oai-gnb-",
    "serviceaccount-oai-gnb-", "deployment-oai-gnb", "service-oai-gnb",
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
            containers = doc["spec"]["template"]["spec"]["containers"]
            containers[0]["image"] = operator_image
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
    "metadata": {"name": gnb_ns, "labels": privileged_labels},
}, dest_gnb)

# Monolithic config parameters
nfconfig = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFConfig",
    "metadata": {"name": f"{gnb_name}-config", "namespace": gnb_ns},
    "spec": {
        "configRefs": [
            {
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "RANConfig",
                "metadata": {"name": "ranconfig", "namespace": gnb_ns},
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
                "metadata": {"name": "plmn", "namespace": gnb_ns},
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
                "metadata": {"name": "oai-nf-config", "namespace": gnb_ns},
                "spec": {"image": gnb_image},
            },
        ],
    },
}
write_doc(nfconfig, dest_gnb, prefix="10-")

# Monolithic gNB directly deploys
nfdeploy = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": gnb_name, "namespace": gnb_ns},
    "spec": {
        "provider": "du.openairinterface.org",
        "interfaces": [{
            "name": "f1",
            "ipv4": {"address": f"{gnb_ip}/24", "gateway": oai_gw},
            "vlanID": 5,
        }],
        "networkInstances": [{
            "name": "vpc-cudu-f1",
            "interfaces": ["f1"],
        }],
        "parametersRefs": [
            {
                "name": f"{gnb_name}-config",
                "apiVersion": "workload.nephio.org/v1alpha1",
                "kind": "NFConfig",
            }
        ],
    },
}
write_doc(nfdeploy, dest_gnb, prefix="20-")

def make_nad(suffix, address, gateway):
    nad_name = f"{gnb_name}-{suffix}"
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
        "metadata": {"name": nad_name, "namespace": gnb_ns},
        "spec": {"config": json.dumps(cfg)},
    }

write_doc(make_nad("f1", gnb_ip, oai_gw), dest_gnb, prefix="12-")

# gnb.conf for monolithic mode with RF Simulator
gnb_conf = f"""Active_gNBs = ( "oai-gnb");
Asn1_verbosity = "none";
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_name  =  "oai-gnb";
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

    amf_ip_address      = ( {{ ipv4       = "{amf_n2_ip}"; }} );

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{gnb_ip}";
        GNB_IPV4_ADDRESS_FOR_NGU                 = "{gnb_ip}";
        GNB_PORT_FOR_S1U                         = 2152;
    }};
  }}
);

MACRLCs = (
  {{
    num_cc = 1;
    tr_s_preference = "local_L1";
    tr_n_preference = "local_RRC";
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
    rrc_log_level = "info";
    ngap_log_level = "debug";
}};
"""

write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-gnb-configmap", "namespace": gnb_ns},
    "data": {"gnb.conf": gnb_conf},
}, dest_gnb, prefix="13-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-gnb-sa", "namespace": gnb_ns},
}, dest_gnb, prefix="14-")

networks_json = (
    "[\n {\n"
    f'  "name": {json.dumps(f"{gnb_name}-f1")},\n'
    '  "interface": "f1",\n'
    f'  "ips": [{json.dumps(f"{gnb_ip}/24")}],\n'
    f'  "gateways": [{json.dumps(oai_gw)}]\n'
    " }\n]"
)

write_doc({
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "oai-gnb",
        "namespace": gnb_ns,
        "labels": {"app.kubernetes.io/name": "oai-gnb"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-gnb"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-gnb",
                    "app.kubernetes.io/name": "oai-gnb",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-gnb-sa",
                "terminationGracePeriodSeconds": 5,
                "containers": [{
                    "name": "gnb",
                    "image": gnb_image,
                    "securityContext": {"privileged": True},
                    "env": [{
                        "name": "USE_ADDITIONAL_OPTIONS",
                        "value": (
                            "--rfsim --log_config.global_log_options level,nocolor,time"
                        ),
                    }],
                    "ports": [
                        {"name": "n2", "containerPort": 38412, "protocol": "SCTP"},
                        {"name": "n3", "containerPort": 2152, "protocol": "UDP"},
                        {"name": "rfsim", "containerPort": 4043, "protocol": "TCP"},
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
                    "configMap": {"name": "oai-gnb-configmap"},
                }],
            },
        },
    },
}, dest_gnb, prefix="15-")

write_doc({
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {
        "name": "oai-gnb",
        "namespace": gnb_ns,
        "labels": {"app.kubernetes.io/name": "oai-gnb"},
    },
    "spec": {
        "type": "ClusterIP",
        "clusterIP": "None",
        "selector": {"app.kubernetes.io/name": "oai-gnb"},
        "ports": [
            {"name": "n2", "port": 38412, "protocol": "SCTP", "targetPort": 38412},
            {"name": "n3", "port": 2152, "protocol": "UDP", "targetPort": 2152},
            {"name": "rfsim", "port": 4043, "protocol": "TCP", "targetPort": 4043},
        ],
    },
}, dest_gnb, prefix="16-")

print(f"  namespaces/{ops_ns}: operator")
print(f"  namespaces/{gnb_ns}: Monolithic gNB + rfsim ({gnb_name})")
print(f"  gNB IP {gnb_ip} -> central AMF N2 {amf_n2_ip}")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI RAN Monolithic gNB → ${OAI_RAN_OPERATORS_NS}, ${OAI_RAN_GNB_NS})"
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
      echo "error: OAI RAN Monolithic gNB deploys on edge, not '${cluster}'" >&2
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
    write_gnb_mono_gitops "$cluster" "$src_dir"
  done

  echo
  echo "Namespaces: ${OAI_RAN_OPERATORS_NS} (operator), ${OAI_RAN_GNB_NS} (Monolithic gNB)"
  echo "Prerequisites: Multus (./scripts/render_multus_gitops.sh <cluster>)"
  echo "Central AMF must be running (AMF N2 IP ${AMF_N2_IP})"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
}

main "$@"
