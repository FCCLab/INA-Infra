#!/usr/bin/env bash
# Render oai-nws-1ue simulation stack for Config Sync:
#   edge: mono gNB (106 PRB, compose-like RAN) on usrp
#   ue:   1x nrUE RFsim client
# Replaces oai-ran-gnb / oai-gnb-ns-1ue. Uses OAI AMF on central macvlan.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_NWS_NS="${OAI_NWS_NS:-oai-nws-1ue}"
OAI_GNB_IMAGE="${OAI_GNB_IMAGE:-10.1.132.30:5000/oai-gnb:nws-v0.2}"
OAI_NR_UE_IMAGE="${OAI_NR_UE_IMAGE:-10.1.132.30:5000/oai-nr-ue:nws-v0.2}"
OAI_DEBUG_SIDECAR_IMAGE="${OAI_DEBUG_SIDECAR_IMAGE:-docker.io/nicolaka/netshoot}"
AMF_N2_IP="${AMF_N2_IP:-$(amf_n2_vip central)}"
GNB_IP="${GNB_IP:-$(oai_macvlan_ip edge 3)}"   # 10.1.139.113
UE_RF_IP="${UE_RF_IP:-$(oai_macvlan_ip ue 0)}" # 10.1.139.160
GNB_NODE="${GNB_NODE:-usrp}"
UE_IMSI="${UE_IMSI:-001010000000001}"
UE_KEY="${UE_KEY:-fec86ba6eb707ed08905757b1bb44b8f}"
UE_OPC="${UE_OPC:-C42449363BBAD02B66D16BC975D77CC1}"
UE_DNN="${UE_DNN:-internet}"
UE_NSSAI_SST="${UE_NSSAI_SST:-1}"
UE_NSSAI_SD="${UE_NSSAI_SD:-0xFFFFFF}"

remove_legacy_namespaces() {
  local edge_repo ue_repo
  edge_repo="${REPOS_DIR}/$(cluster_gitea_repo_name edge)/namespaces"
  ue_repo="${REPOS_DIR}/$(cluster_gitea_repo_name ue)/namespaces"
  rm -rf "${edge_repo}/oai-ran-gnb" "${ue_repo}/oai-gnb-ns-1ue"
  echo "==> Removed legacy namespaces: oai-ran-gnb, oai-gnb-ns-1ue"
}

write_edge_gnb() {
  local dest gnb_ns
  dest="${REPOS_DIR}/$(cluster_gitea_repo_name edge)/namespaces/${OAI_NWS_NS}"
  gnb_ns="$OAI_NWS_NS"
  mkdir -p "$dest"

  python3 - "$dest" "$gnb_ns" "$OAI_GNB_IMAGE" "$USRP_SITE_IFACE" \
    "$GNB_IP" "$OAI_MACVLAN_GW" "$AMF_N2_IP" "$GNB_NODE" \
    "$SCRIPT_DIR/oai_debug_sidecar.py" "$OAI_DEBUG_SIDECAR_IMAGE" <<'PY'
import json
import importlib.util
import sys
from pathlib import Path

import yaml

(dest, gnb_ns, gnb_image, nad_parent, gnb_ip, oai_gw, amf_n2_ip, gnb_node,
 debug_lib, debug_image) = sys.argv[1:11]
dest = Path(dest)

spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_container = oai_debug.debug_sidecar_container(debug_image)

for old in dest.glob("*.yaml"):
    old.unlink()


def write_doc(doc, prefix=""):
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"]
    (dest / f"{prefix}{kind}-{name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )


write_doc({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": gnb_ns}})

nad_name = "gnb-n2n3"
nad_config = {
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
                "addresses": [{"address": f"{gnb_ip}/24", "gateway": oai_gw}],
            },
        },
        {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
    ],
}
write_doc({
    "apiVersion": "k8s.cni.cncf.io/v1",
    "kind": "NetworkAttachmentDefinition",
    "metadata": {"name": nad_name, "namespace": gnb_ns},
    "spec": {"config": json.dumps(nad_config)},
}, prefix="12-")

# 106 PRB RAN from network-slicing/nws/gnb.sa.band78.106prb.rfsim.oai.yaml
# AMF/IPs rewritten for Nephio OAI core + macvlan.
gnb_conf = f"""Active_gNBs = ( "gnb-rfsim");
Asn1_verbosity = "none";
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_name  =  "gnb-rfsim";
    # AMF plmn_support_list tac: 1 (compose Open5GS used 81 — reject with Unknown PLMN)
    tracking_area_code  =  1;
    plmn_list = ({{ mcc = 001; mnc = 01; mnc_length = 2; snssaiList = ({{ sst = 1, sd = 0xffffff }}) }});
    nr_cellid = 12345678;
    min_rxtxtime = 6;
    servingCellConfigCommon = (
    {{
      # Match network-slicing/nws/gnb.sa.band78.106prb.rfsim.oai.yaml
      # Do NOT set ra_ResponseWindow: OAI auto-selects sl20 (mu=1); sl10 (4) is too
      # short for Msg2 under RFsim/TDD (exceeded RA window / RAR failed).
      physCellId = 0;
      absoluteFrequencySSB = 621312;
      dl_frequencyBand = 78;
      dl_absoluteFrequencyPointA = 620040;
      dl_offstToCarrier = 0;
      dl_subcarrierSpacing = 1;
      dl_carrierBandwidth = 106;
      initialDLBWPlocationAndBandwidth = 28875;
      initialDLBWPsubcarrierSpacing = 1;
      initialDLBWPcontrolResourceSetZero = 11;
      initialDLBWPsearchSpaceZero = 0;
      ul_frequencyBand = 78;
      ul_offstToCarrier = 0;
      ul_subcarrierSpacing = 1;
      ul_carrierBandwidth = 106;
      pMax = 20;
      initialULBWPlocationAndBandwidth = 28875;
      initialULBWPsubcarrierSpacing = 1;
      prach_ConfigurationIndex = 98;
      prach_msg1_FDM = 0;
      prach_msg1_FrequencyStart = 0;
      zeroCorrelationZoneConfig = 12;
      preambleReceivedTargetPower = -104;
      preambleTransMax = 6;
      powerRampingStep = 1;
      ssb_perRACH_OccasionAndCB_PreamblesPerSSB_PR = 4;
      ssb_perRACH_OccasionAndCB_PreamblesPerSSB = 15;
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
    pusch_TargetSNRx10 = 200;
    pucch_TargetSNRx10 = 200;
    stats_max_ue = 17;
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
    max_rxgain = 75;
    eNB_instances = [0];
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

security = {{
  ciphering_algorithms = ( "nea0" );
  integrity_algorithms = ( "nia2", "nia0" );
  drb_ciphering = "yes";
  drb_integrity = "no";
}};

log_config :
{{
    global_log_level = "info";
    hw_log_level = "info";
    phy_log_level = "info";
    mac_log_level = "info";
    rlc_log_level = "info";
    f1ap_log_level = "debug";
    rrc_log_level = "info";
    ngap_log_level = "debug";
}};
"""

write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-gnb-configmap", "namespace": gnb_ns},
    "data": {"gnb.conf": gnb_conf},
}, prefix="13-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-gnb-sa", "namespace": gnb_ns},
}, prefix="14-")

networks_json = (
    "[\n {\n"
    f'  "name": {json.dumps(nad_name)},\n'
    '  "interface": "n2",\n'
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
                "nodeSelector": {"kubernetes.io/hostname": gnb_node},
                "containers": [{
                    "name": "gnb",
                    "image": gnb_image,
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": {"privileged": True},
                    "env": [{
                        "name": "USE_ADDITIONAL_OPTIONS",
                        "value": (
                            "-E --rfsim --log_config.global_log_options level,nocolor,time"
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
}, prefix="15-")

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
}, prefix="16-")

print(f"  edge namespaces/{gnb_ns}: gNB 106PRB {gnb_ip} on {gnb_node} -> AMF {amf_n2_ip}")
PY
}

write_ue() {
  local dest ue_ns
  dest="${REPOS_DIR}/$(cluster_gitea_repo_name ue)/namespaces/${OAI_NWS_NS}"
  ue_ns="$OAI_NWS_NS"
  mkdir -p "$dest"

  python3 - "$dest" "$ue_ns" "$OAI_NR_UE_IMAGE" "$SITE_IFACE" \
    "$UE_RF_IP" "$OAI_MACVLAN_GW" "$GNB_IP" \
    "$UE_IMSI" "$UE_KEY" "$UE_OPC" "$UE_DNN" "$UE_NSSAI_SST" "$UE_NSSAI_SD" <<'PY'
import json
import sys
from pathlib import Path

import yaml

(dest, ue_ns, ue_image, nad_parent, ue_rf_ip, oai_gw, gnb_ip,
 imsi, key, opc, dnn, nssai_sst, nssai_sd) = sys.argv[1:14]
dest = Path(dest)

for old in dest.glob("*.yaml"):
    old.unlink()


def write_doc(doc, prefix=""):
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"]
    (dest / f"{prefix}{kind}-{name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )


write_doc({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ue_ns}})

nad_name = "ue-sim-rf"
nad_config = {
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
                "addresses": [{"address": f"{ue_rf_ip}/24", "gateway": oai_gw}],
            },
        },
        {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
    ],
}
write_doc({
    "apiVersion": "k8s.cni.cncf.io/v1",
    "kind": "NetworkAttachmentDefinition",
    "metadata": {"name": nad_name, "namespace": ue_ns},
    "spec": {"config": json.dumps(nad_config)},
}, prefix="10-")

ue_conf = f"""uicc0 = {{
  imsi = "{imsi}";
  key = "{key}";
  opc = "{opc}";
  dnn = "{dnn}";
  nssai_sst = {nssai_sst};
  nssai_sd = {nssai_sd};
}}

position0 = {{
  x = 0.0;
  y = 0.0;
  z = 6377900.0;
}}
"""
write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-ue-configmap", "namespace": ue_ns},
    "data": {"ue.conf": ue_conf},
}, prefix="11-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-ue-sa", "namespace": ue_ns},
}, prefix="12-")

networks_json = (
    "[\n {\n"
    f'  "name": "{nad_name}",\n'
    '  "interface": "rf",\n'
    f'  "ips": [{json.dumps(f"{ue_rf_ip}/24")}],\n'
    f'  "gateways": [{json.dumps(oai_gw)}]\n'
    " }\n]"
)
# Match compose run_ue.sh RF for 106 PRB gNB
additional_options = (
    f"-E --rfsim --log_config.global_log_options level,nocolor,time "
    f"--rfsimulator.serveraddr {gnb_ip} --rfsimulator.serverport 4043 "
    "-C 3319680000 -r 106 --numerology 1 --band 78 "
    f"--uicc0.imsi {imsi}"
)

write_doc({
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "oai-ue",
        "namespace": ue_ns,
        "labels": {"app.kubernetes.io/name": "oai-ue"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-ue"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-ue",
                    "app.kubernetes.io/name": "oai-ue",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-ue-sa",
                "terminationGracePeriodSeconds": 5,
                "containers": [{
                    "name": "ue",
                    "image": ue_image,
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": {"privileged": True},
                    "env": [
                        {"name": "USE_ADDITIONAL_OPTIONS", "value": additional_options},
                        {"name": "TZ", "value": "Europe/Paris"},
                    ],
                    "resources": {
                        "requests": {"cpu": "1000m", "memory": "512Mi"},
                        "limits": {"cpu": "2000m", "memory": "1Gi"},
                    },
                    "volumeMounts": [{
                        "name": "configuration",
                        "mountPath": "/opt/oai-nr-ue/etc/nr-ue.conf",
                        "subPath": "ue.conf",
                    }],
                }],
                "volumes": [{
                    "name": "configuration",
                    "configMap": {"name": "oai-ue-configmap"},
                }],
            },
        },
    },
}, prefix="15-")

print(f"  ue namespaces/{ue_ns}: nrUE {ue_rf_ip} -> gNB rfsim {gnb_ip}:4043 IMSI {imsi}")
PY
}

main() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  remove_legacy_namespaces
  write_edge_gnb
  write_ue

  echo
  echo "Namespace: ${OAI_NWS_NS} (edge gNB + ue nrUE)"
  echo "gNB ${GNB_IP} on ${GNB_NODE} (106 PRB) -> AMF ${AMF_N2_IP}"
  echo "UE  ${UE_RF_IP} RFsim -> ${GNB_IP}:4043"
  echo "Removed: oai-ran-gnb, oai-gnb-ns-1ue"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh edge ue"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0")

Render oai-nws-1ue (106 PRB gNB on edge/usrp + 1 nrUE on ue) and remove
legacy oai-ran-gnb / oai-gnb-ns-1ue manifests from repos/.

Environment:
  OAI_NWS_NS       Namespace (default: oai-nws-1ue)
  GNB_IP / UE_RF_IP  macvlan IPs (default: .113 / .160)
  GNB_NODE         Node for gNB (default: usrp)
EOF
  exit 0
fi

main "$@"
