#!/usr/bin/env bash
# Render oai-slice-deployment GitOps:
#   Co-located UPF + CU-UP per slice:
#     slice1 → central, slice2 → regional, slices 3–5 → edge
#   central  — UPF1 + CU-UP1 + SMF/AMF NSSAI + MySQL SD patch + UPF operator (existing)
#   regional — UPF2 + CU-UP2 + UPF-only operator
#   edge     — UPF3–5 + CU-UP3–5 + CU-CP/DU/UEs/FlexRIC + UPF-only operator
# Retires oai-ran-nephio-example-split-deploy and oai-nws-1ue from repos.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
SLICE_NS="${OAI_SLICE_NS:-oai-slice-deployment}"
UPF_NS="${OAI_UPF_NS:-oai-upf}"
CN_NS="${OAI_CN_NS:-oai-cn}"
OPS_NS="${OAI_CN_OPERATORS_NS:-oai-cn-operators}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-docker.io/nephio/oai-ran-controller:latest}"
OAI_REGISTRY="${OAI_REGISTRY:-10.1.132.30:5000}"
OAI_IMAGE_TAG="${OAI_IMAGE_TAG:-nws-v0.8-amd64}"
# Sidecars / xApp may lag the softmodem tag.
OAI_SIDECAR_IMAGE_TAG="${OAI_SIDECAR_IMAGE_TAG:-nws-v0.5-amd64}"
OAI_CUCP_IMAGE="${OAI_CUCP_IMAGE:-${OAI_REGISTRY}/oai-cucp:${OAI_IMAGE_TAG}}"
OAI_DU_IMAGE="${OAI_DU_IMAGE:-${OAI_REGISTRY}/oai-du:${OAI_IMAGE_TAG}}"
OAI_CUUP_IMAGE="${OAI_CUUP_IMAGE:-${OAI_REGISTRY}/oai-nr-cuup:${OAI_IMAGE_TAG}}"
OAI_NR_UE_IMAGE="${OAI_NR_UE_IMAGE:-${OAI_REGISTRY}/oai-nr-ue:${OAI_IMAGE_TAG}}"
OAI_FLEXRIC_IMAGE="${OAI_FLEXRIC_IMAGE:-${OAI_REGISTRY}/oai-flexric:${OAI_IMAGE_TAG}}"
OAI_XAPP_IMAGE="${OAI_XAPP_IMAGE:-${OAI_REGISTRY}/nws-xapp:${OAI_SIDECAR_IMAGE_TAG}}"
DEBUG_IMAGE="${OAI_DEBUG_SIDECAR_IMAGE:-docker.io/nicolaka/netshoot}"
SLICE_COUNT="${OAI_SLICE_COUNT:-5}"
# Only first N UE deployments get replicas=1 (rest stay 0). Default: all slices.
UE_ACTIVE_COUNT="${OAI_UE_ACTIVE_COUNT:-$SLICE_COUNT}"
RETIRE_OLD="${RETIRE_OLD:-1}"

# --- Slice A CCTV (analyzer on central, publisher sidecar in edge UE-1) -------
# Analyzer is reached over the 5G air interface: UE-1 -> DU -> UPF-1 -> N6 ->
# analyzer externalIP on the central mgmt LAN (UPF N6 routes MGMT_CIDR back).
SLICEA_PUBLISHER_IMAGE="${SLICEA_PUBLISHER_IMAGE:-${OAI_REGISTRY}/slicea-publisher:${OAI_SIDECAR_IMAGE_TAG}}"
SLICEA_ANALYZER_IMAGE="${SLICEA_ANALYZER_IMAGE:-${OAI_REGISTRY}/slicea-analyzer:${OAI_SIDECAR_IMAGE_TAG}}"
SLICEA_ANALYZER_EXTIP="${SLICEA_ANALYZER_EXTIP:-${CLUSTER_MGMT_IP[central]}}"
SLICEA_RTSP_PORT="${SLICEA_RTSP_PORT:-8554}"
SLICEA_STREAM_PATH="${SLICEA_STREAM_PATH:-slicea}"
SLICEA_RTSP_PROTOCOL="${SLICEA_RTSP_PROTOCOL:-tcp}"
SLICEA_PUB_METRICS_PORT="${SLICEA_PUB_METRICS_PORT:-9101}"
SLICEA_ANALYZER_METRICS_PORT="${SLICEA_ANALYZER_METRICS_PORT:-9102}"
SLICEA_PDU_IFACE="${SLICEA_PDU_IFACE:-oaitun_ue1}"
SLICEA_YOLO_ENABLED="${SLICEA_YOLO_ENABLED:-true}"
SLICEA_YOLO_DEVICE="${SLICEA_YOLO_DEVICE:-cpu}"
SLICEA_YOLO_MODEL="${SLICEA_YOLO_MODEL:-yolov8n.pt}"
SLICEA_FRAME_SKIP="${SLICEA_FRAME_SKIP:-1}"
SLICEA_UE_SLICE="${SLICEA_UE_SLICE:-1}"
# Optional: pin the analyzer to a specific central node (e.g. central-0) — the
# central workers are disk-constrained, so keep it off the full one. Empty = any.
SLICEA_ANALYZER_NODE="${SLICEA_ANALYZER_NODE:-central-0}"
export SLICEA_PUBLISHER_IMAGE SLICEA_ANALYZER_IMAGE SLICEA_ANALYZER_EXTIP \
  SLICEA_RTSP_PORT SLICEA_STREAM_PATH SLICEA_RTSP_PROTOCOL SLICEA_PUB_METRICS_PORT \
  SLICEA_ANALYZER_METRICS_PORT SLICEA_PDU_IFACE SLICEA_YOLO_ENABLED SLICEA_YOLO_DEVICE \
  SLICEA_YOLO_MODEL SLICEA_FRAME_SKIP SLICEA_UE_SLICE SLICEA_ANALYZER_NODE

# --- Slice D IoT (broker+controller on central, client sidecar in edge UE-4) ---
# Broker is reached over the 5G air interface: UE-4 -> DU -> UPF-4 -> N6 ->
# broker externalIP on the central mgmt LAN (UPF N6 routes MGMT_CIDR back).
SLICED_CLIENT_IMAGE="${SLICED_CLIENT_IMAGE:-${OAI_REGISTRY}/sliced-client:${OAI_SIDECAR_IMAGE_TAG}}"
SLICED_EDGE_IMAGE="${SLICED_EDGE_IMAGE:-${OAI_REGISTRY}/sliced-edge:${OAI_SIDECAR_IMAGE_TAG}}"
SLICED_BROKER_EXTIP="${SLICED_BROKER_EXTIP:-${CLUSTER_MGMT_IP[central]}}"
SLICED_BROKER_PORT="${SLICED_BROKER_PORT:-1883}"
SLICED_CLIENT_METRICS_PORT="${SLICED_CLIENT_METRICS_PORT:-9104}"
SLICED_EDGE_METRICS_PORT="${SLICED_EDGE_METRICS_PORT:-9105}"
SLICED_PDU_IFACE="${SLICED_PDU_IFACE:-oaitun_ue1}"
SLICED_UE_SLICE="${SLICED_UE_SLICE:-4}"
SLICED_NUM_DEVICES="${SLICED_NUM_DEVICES:-5}"
SLICED_FAST_PERIOD_S="${SLICED_FAST_PERIOD_S:-60}"
SLICED_MED_PERIOD_S="${SLICED_MED_PERIOD_S:-1800}"
SLICED_SLOW_PERIOD_S="${SLICED_SLOW_PERIOD_S:-3600}"
SLICED_DL_FAST_PERIOD_S="${SLICED_DL_FAST_PERIOD_S:-300}"
SLICED_DL_SLOW_PERIOD_S="${SLICED_DL_SLOW_PERIOD_S:-3600}"
export SLICED_CLIENT_IMAGE SLICED_EDGE_IMAGE SLICED_BROKER_EXTIP SLICED_BROKER_PORT \
  SLICED_CLIENT_METRICS_PORT SLICED_EDGE_METRICS_PORT SLICED_PDU_IFACE SLICED_UE_SLICE \
  SLICED_NUM_DEVICES SLICED_FAST_PERIOD_S SLICED_MED_PERIOD_S SLICED_SLOW_PERIOD_S \
  SLICED_DL_FAST_PERIOD_S SLICED_DL_SLOW_PERIOD_S

# Build IP tables for Python
UPF_N3=() UPF_N4=() UPF_N6=()
CUUP_E1=() CUUP_F1U=() CUUP_N3=()
UE_RF=() IMSIS=() SDS=() SITES=()
for i in $(seq 1 "$SLICE_COUNT"); do
  UPF_N3+=("$(upf_slice_n3 "$i")")
  UPF_N4+=("$(upf_slice_n4 "$i")")
  UPF_N6+=("$(upf_slice_n6 "$i")")
  CUUP_E1+=("$(cuup_slice_e1 "$i")")
  CUUP_F1U+=("$(cuup_slice_f1u "$i")")
  CUUP_N3+=("$(cuup_slice_n3 "$i")")
  UE_RF+=("$(oai_slice_ue_rf "$i")")
  IMSIS+=("$(oai_slice_imsi "$i")")
  SDS+=("$(oai_slice_sd_hex "$i")")
  SITES+=("$(oai_slice_site "$i")")
done

AMF_N2="$(amf_n2_vip central)"
CUCP_N2="$(oai_slice_cucp_n2)"
CUCP_F1C="$(oai_slice_cucp_f1c)"
CUCP_E1="$(oai_slice_cucp_e1)"
DU_F1="$(oai_slice_du_f1)"
DU_RF="$(oai_slice_du_rf)"
FLEXRIC_IP="$(oai_slice_flexric)"
XAPP_E2_IP="$(oai_slice_xapp_e2)"
XAPP_SWAGGER_VIP="${OAI_XAPP_SWAGGER_VIP}"
XAPP_API_PORT="${OAI_XAPP_API_PORT}"
SMF_N4="$(oai_smf_n4_ip)"
NRF_LB="$(oai_nrf_lb_ip)"
export OAI_NRF_LB_IP="$NRF_LB"
GW="$OAI_MACVLAN_GW"
N6_GW_CENTRAL="$(oai_n6_gw_ip central)"
N6_GW_REGIONAL="$(oai_n6_gw_ip regional)"
N6_GW_EDGE="$(oai_n6_gw_ip edge)"
MGMT_CIDR_ARG="${MGMT_CIDR}"
NAD_PARENT="$SITE_IFACE"
USRP_IFACE="${USRP_SITE_IFACE:-enp4s0f0}"

join_csv() { local IFS=,; echo "$*"; }

python3 - "$REPOS_DIR" "$SLICE_NS" "$UPF_NS" "$CN_NS" "$OPS_NS" \
  "$OAI_RAN_OPERATOR_IMAGE" "$OAI_CUCP_IMAGE" "$OAI_DU_IMAGE" "$OAI_CUUP_IMAGE" "$OAI_NR_UE_IMAGE" \
  "$DEBUG_IMAGE" "$SCRIPT_DIR/oai_debug_sidecar.py" \
  "$AMF_N2" "$CUCP_N2" "$CUCP_F1C" "$CUCP_E1" "$DU_F1" "$DU_RF" "$SMF_N4" "$GW" \
  "$NAD_PARENT" "$USRP_IFACE" "$SLICE_COUNT" "$UE_ACTIVE_COUNT" "$RETIRE_OLD" \
  "$(join_csv "${UPF_N3[@]}")" "$(join_csv "${UPF_N4[@]}")" "$(join_csv "${UPF_N6[@]}")" \
  "$(join_csv "${CUUP_E1[@]}")" "$(join_csv "${CUUP_F1U[@]}")" "$(join_csv "${CUUP_N3[@]}")" \
  "$(join_csv "${UE_RF[@]}")" "$(join_csv "${IMSIS[@]}")" "$(join_csv "${SDS[@]}")" \
  "$(join_csv "${SITES[@]}")" \
  "$FLEXRIC_IP" "$XAPP_E2_IP" "$XAPP_SWAGGER_VIP" "$XAPP_API_PORT" \
  "$OAI_FLEXRIC_IMAGE" "$OAI_XAPP_IMAGE" \
  "$N6_GW_CENTRAL" "$N6_GW_REGIONAL" "$N6_GW_EDGE" "$MGMT_CIDR_ARG" \
  <<'PY'
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

(
    repos_dir, slice_ns, upf_ns, cn_ns, ops_ns,
    ran_op_image, cucp_image, du_image, cuup_image, ue_image,
    debug_image, debug_lib,
    amf_n2, cucp_n2, cucp_f1c, cucp_e1, du_f1, du_rf, smf_n4, gw,
    nad_parent, usrp_iface, slice_count_s, ue_active_s, retire_old,
    upf_n3_csv, upf_n4_csv, upf_n6_csv,
    cuup_e1_csv, cuup_f1u_csv, cuup_n3_csv,
    ue_rf_csv, imsi_csv, sd_csv,
    sites_csv,
    flexric_ip, xapp_e2_ip, xapp_swagger_vip, xapp_api_port_s,
    flexric_image, xapp_image,
    n6_gw_central, n6_gw_regional, n6_gw_edge, mgmt_cidr,
) = sys.argv[1:46]

slice_count = int(slice_count_s)
ue_active = max(0, min(slice_count, int(ue_active_s)))
xapp_api_port = int(xapp_api_port_s)
upf_n3 = upf_n3_csv.split(",")
upf_n4 = upf_n4_csv.split(",")
upf_n6 = upf_n6_csv.split(",")
cuup_e1 = cuup_e1_csv.split(",")
cuup_f1u = cuup_f1u_csv.split(",")
cuup_n3 = cuup_n3_csv.split(",")
ue_rf = ue_rf_csv.split(",")
imsis = imsi_csv.split(",")
sds = sd_csv.split(",")
sites = sites_csv.split(",")
SITE_N6_GW = {
    "central": n6_gw_central,
    "regional": n6_gw_regional,
    "edge": n6_gw_edge,
}
# Unified UPF peer endpoints for every site (central / regional / edge).
# SMF N4 macvlan + NRF MetalLB — never ClusterIP DNS (does not resolve off-central).
nrf_lb = os.environ.get("OAI_NRF_LB_IP") or os.environ.get("OAI_NRF_LB", "10.1.138.100")
UPF_PEER_SMF = smf_n4
UPF_PEER_NRF = nrf_lb
# SMF uses static upfs (discover_upf: no); same register_nf for all slices.
UPF_REGISTER_NF = "no"


def patch_upf_op_conf_peers(path: Path, *, smf: str = UPF_PEER_SMF, nrf: str = UPF_PEER_NRF) -> None:
    """Set op-conf fqdn.smf / fqdn.nrf to shared IPs."""
    if not path.is_file():
        return
    doc = yaml.safe_load(path.read_text())
    raw = doc["data"]["upf.yaml"]
    raw = re.sub(r"smf: '[^']*'", f"smf: '{smf}'", raw)
    raw = re.sub(r"nrf: '[^']*'", f"nrf: '{nrf}'", raw)
    doc["data"]["upf.yaml"] = raw
    dump(doc, path)


def patch_upf_nf_conf_register(path: Path, *, register: str = UPF_REGISTER_NF) -> None:
    """Unify register_nf.upf in the operator nf-conf jinja template."""
    if not path.is_file():
        return
    doc = yaml.safe_load(path.read_text())
    tpl = doc["data"]["upf.yaml"]
    tpl = re.sub(
        r"register_nf:\n  upf: '[^']*'",
        f"register_nf:\n  upf: '{register}'",
        tpl,
    )
    doc["data"]["upf.yaml"] = tpl
    dump(doc, path)


def patch_upf_controller_utils_exclude_usrp(path: Path) -> None:
    """Keep operator-created UPF pods off usrp (no enp7s0 for N3/N4/N6 macvlan)."""
    if not path.is_file():
        return
    doc = yaml.safe_load(path.read_text())
    utils = doc.get("data", {}).get("utils.py", "")
    if not utils or ('"operator": "NotIn"' in utils and '"usrp"' in utils):
        return
    old = '                      "spec": {\n                        "securityContext": {'
    new = (
        '                      "spec": {\n'
        '                        "affinity": {\n'
        '                          "nodeAffinity": {\n'
        '                            "requiredDuringSchedulingIgnoredDuringExecution": {\n'
        '                              "nodeSelectorTerms": [\n'
        '                                {\n'
        '                                  "matchExpressions": [\n'
        '                                    {\n'
        '                                      "key": "kubernetes.io/hostname",\n'
        '                                      "operator": "NotIn",\n'
        '                                      "values": ["usrp"]\n'
        '                                    }\n'
        '                                  ]\n'
        '                                }\n'
        '                              ]\n'
        '                            }\n'
        '                          }\n'
        '                        },\n'
        '                        "securityContext": {'
    )
    if old not in utils:
        return
    doc["data"]["utils.py"] = utils.replace(old, new, 1)
    dump(doc, path)


def apply_unified_upf_operator_peers(site: str) -> None:
    """Same SMF/NRF IPs + register_nf on every cluster's UPF operator configs."""
    ops = repos / REPO_NAME[site] / "namespaces" / ops_ns
    patch_upf_op_conf_peers(ops / "configmap-oai-upf-op-conf.yaml")
    patch_upf_nf_conf_register(ops / "configmap-oai-upf-nf-conf.yaml")
    patch_upf_controller_utils_exclude_usrp(
        ops / "configmap-oai-upf-controller-utils.yaml"
    )


# Slice A CCTV config (from exported SLICEA_* env vars).
SLICEA = {
    "pub_image": os.environ["SLICEA_PUBLISHER_IMAGE"],
    "analyzer_image": os.environ["SLICEA_ANALYZER_IMAGE"],
    "ext_ip": os.environ["SLICEA_ANALYZER_EXTIP"],
    "rtsp_port": int(os.environ.get("SLICEA_RTSP_PORT", "8554")),
    "stream_path": os.environ.get("SLICEA_STREAM_PATH", "slicea"),
    "protocol": os.environ.get("SLICEA_RTSP_PROTOCOL", "tcp"),
    "pub_metrics_port": int(os.environ.get("SLICEA_PUB_METRICS_PORT", "9101")),
    "analyzer_metrics_port": int(os.environ.get("SLICEA_ANALYZER_METRICS_PORT", "9102")),
    "pdu_iface": os.environ.get("SLICEA_PDU_IFACE", "oaitun_ue1"),
    "yolo_enabled": os.environ.get("SLICEA_YOLO_ENABLED", "true"),
    "yolo_device": os.environ.get("SLICEA_YOLO_DEVICE", "cpu"),
    "yolo_model": os.environ.get("SLICEA_YOLO_MODEL", "yolov8n.pt"),
    "frame_skip": os.environ.get("SLICEA_FRAME_SKIP", "1"),
    "ue_slice": int(os.environ.get("SLICEA_UE_SLICE", "1")),
    "analyzer_node": os.environ.get("SLICEA_ANALYZER_NODE", ""),
}

# Slice D IoT config (from exported SLICED_* env vars).
SLICED = {
    "client_image": os.environ["SLICED_CLIENT_IMAGE"],
    "edge_image": os.environ["SLICED_EDGE_IMAGE"],
    "ext_ip": os.environ["SLICED_BROKER_EXTIP"],
    "broker_port": int(os.environ.get("SLICED_BROKER_PORT", "1883")),
    "client_metrics_port": int(os.environ.get("SLICED_CLIENT_METRICS_PORT", "9104")),
    "edge_metrics_port": int(os.environ.get("SLICED_EDGE_METRICS_PORT", "9105")),
    "pdu_iface": os.environ.get("SLICED_PDU_IFACE", "oaitun_ue1"),
    "ue_slice": int(os.environ.get("SLICED_UE_SLICE", "4")),
    "num_devices": os.environ.get("SLICED_NUM_DEVICES", "5"),
    "fast_period_s": os.environ.get("SLICED_FAST_PERIOD_S", "60"),
    "med_period_s": os.environ.get("SLICED_MED_PERIOD_S", "1800"),
    "slow_period_s": os.environ.get("SLICED_SLOW_PERIOD_S", "3600"),
    "dl_fast_period_s": os.environ.get("SLICED_DL_FAST_PERIOD_S", "300"),
    "dl_slow_period_s": os.environ.get("SLICED_DL_SLOW_PERIOD_S", "3600"),
}

repos = Path(repos_dir)
REPO_NAME = {"central": "central-repo", "regional": "regional-repo", "edge": "edge-repo"}
SITE_NODES = {
    "central": ["central-0", "central-1"],
    "regional": ["regional-0", "regional-1"],
    "edge": ["edge-0", "edge-1"],
}
spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_ctr = oai_debug.debug_sidecar_container(debug_image)


def dump(doc, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))


def nad(name, ns, master, ip, *, gateway=None, routes=None):
    g = gw if gateway is None else gateway
    ipam = {
        "type": "static",
        "addresses": [{"address": f"{ip}/24", "gateway": g}],
    }
    if routes:
        ipam["routes"] = routes
    cfg = {
        "cniVersion": "0.3.1",
        "name": name,
        "plugins": [
            {
                "type": "macvlan",
                "capabilities": {"ips": True},
                "master": master,
                "mode": "bridge",
                "ipam": ipam,
            },
            # arp_ignore=1 / arp_announce=2: each macvlan only ARPs for its
            # own IP. Without this, UPF n3/n4/n6 on the same 10.1.139.0/24
            # cause ARP flux (SMF learned UPF1 .21 → n6 MAC → no PFCP on n4).
            {
                "type": "tuning",
                "capabilities": {"mac": True},
                "ipam": {},
                "sysctl": {
                    "net.ipv4.conf.IFNAME.arp_ignore": "1",
                    "net.ipv4.conf.IFNAME.arp_announce": "2",
                },
            },
        ],
    }
    return {
        "apiVersion": "k8s.cni.cncf.io/v1",
        "kind": "NetworkAttachmentDefinition",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"config": json.dumps(cfg)},
    }


def networks_annot(entries):
    # entries: list of (nad_name, ifname, ip)
    parts = []
    for name, iface, ip in entries:
        parts.append(
            {
                "name": name,
                "interface": iface,
                "ips": [f"{ip}/24"],
                "gateways": [gw],
            }
        )
    return json.dumps(parts)


def slicea_publisher_container():
    """Slice A publisher sidecar for the UE pod (shares oaitun_ue1)."""
    return {
        "name": "slicea-publisher",
        "image": SLICEA["pub_image"],
        "imagePullPolicy": "IfNotPresent",
        # NET_ADMIN to pin the analyzer route through the PDU tunnel (over-the-air).
        "securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}},
        "env": [
            {"name": "VIDEO_SOURCE", "value": "/data/example.mp4"},
            {"name": "RTSP_TARGET_HOST", "value": SLICEA["ext_ip"]},
            {"name": "RTSP_PORT", "value": str(SLICEA["rtsp_port"])},
            {"name": "STREAM_PATH", "value": SLICEA["stream_path"]},
            {"name": "RTSP_PROTOCOL", "value": SLICEA["protocol"]},
            {"name": "METRICS_PORT", "value": str(SLICEA["pub_metrics_port"])},
            {"name": "METRICS_ADDR", "value": "0.0.0.0"},
            {"name": "LOG_INTERVAL_S", "value": "1"},
            # Pin the analyzer via the PDU tunnel so the stream goes over the air.
            {"name": "PDU_IFACE", "value": SLICEA["pdu_iface"]},
            {"name": "PDU_ROUTE_HOSTS", "value": SLICEA["ext_ip"]},
        ],
        "ports": [{"name": "metrics", "containerPort": SLICEA["pub_metrics_port"]}],
        "resources": {
            "requests": {"cpu": "200m", "memory": "256Mi"},
            "limits": {"cpu": "2", "memory": "1Gi"},
        },
    }


def emit_slicea_analyzer(out_dir: Path):
    """Slice A analyzer (RTSP RECORD server) on central, reachable over N6."""
    labels = {"app.kubernetes.io/name": "slicea-analyzer"}
    pod_spec = {
        "containers": [
            {
                "name": "analyzer",
                "image": SLICEA["analyzer_image"],
                "imagePullPolicy": "IfNotPresent",
                "env": [
                    {"name": "BIND_ADDRESS", "value": "0.0.0.0"},
                    {"name": "RTSP_PORT", "value": str(SLICEA["rtsp_port"])},
                    {"name": "STREAM_PATH", "value": SLICEA["stream_path"]},
                    {"name": "RTSP_LATENCY_MS", "value": "0"},
                    {"name": "YOLO_ENABLED", "value": SLICEA["yolo_enabled"]},
                    {"name": "YOLO_MODEL", "value": SLICEA["yolo_model"]},
                    {"name": "YOLO_DEVICE", "value": SLICEA["yolo_device"]},
                    {"name": "FRAME_SKIP", "value": SLICEA["frame_skip"]},
                    {"name": "METRICS_PORT", "value": str(SLICEA["analyzer_metrics_port"])},
                    {"name": "METRICS_ADDR", "value": "0.0.0.0"},
                    {"name": "LOG_INTERVAL_S", "value": "1"},
                ],
                "ports": [
                    {"name": "rtsp", "containerPort": SLICEA["rtsp_port"]},
                    {"name": "metrics", "containerPort": SLICEA["analyzer_metrics_port"]},
                ],
                "resources": {
                    "requests": {"cpu": "1", "memory": "2Gi"},
                    "limits": {"cpu": "6", "memory": "6Gi"},
                },
            }
        ],
    }
    # Central workers are disk-constrained; optionally pin to a healthy node.
    if SLICEA["analyzer_node"]:
        pod_spec["nodeSelector"] = {"kubernetes.io/hostname": SLICEA["analyzer_node"]}
    dump(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "slicea-analyzer", "namespace": slice_ns, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        },
        out_dir / "60-deployment-slicea-analyzer.yaml",
    )
    dump(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "slicea-analyzer", "namespace": slice_ns, "labels": labels},
            "spec": {
                # externalIP on the central mgmt LAN so the UE reaches it over N6.
                "type": "ClusterIP",
                "externalIPs": [SLICEA["ext_ip"]],
                "selector": labels,
                "ports": [
                    {
                        "name": "rtsp",
                        "port": SLICEA["rtsp_port"],
                        "targetPort": SLICEA["rtsp_port"],
                        "protocol": "TCP",
                    }
                ],
            },
        },
        out_dir / "61-service-slicea-analyzer.yaml",
    )


def sliced_client_container():
    """Slice D IoT client sidecar for the UE pod (shares oaitun_ue1)."""
    return {
        "name": "sliced-client",
        "image": SLICED["client_image"],
        "imagePullPolicy": "IfNotPresent",
        # NET_ADMIN to pin the broker route through the PDU tunnel (over-the-air).
        "securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}},
        "env": [
            {"name": "BROKER_HOST", "value": SLICED["ext_ip"]},
            {"name": "BROKER_PORT", "value": str(SLICED["broker_port"])},
            # Empty bind: after PDU route pin, MQTT egresses via oaitun_ue1.
            {"name": "OTA_BIND_IP", "value": ""},
            {"name": "METRICS_BIND_IP", "value": "0.0.0.0"},
            {"name": "METRICS_PORT", "value": str(SLICED["client_metrics_port"])},
            {"name": "NUM_DEVICES", "value": SLICED["num_devices"]},
            {"name": "FAST_PERIOD_S", "value": SLICED["fast_period_s"]},
            {"name": "MED_PERIOD_S", "value": SLICED["med_period_s"]},
            {"name": "SLOW_PERIOD_S", "value": SLICED["slow_period_s"]},
            {"name": "MQTT_QOS", "value": "0"},
            {"name": "LOG_INTERVAL_S", "value": "30"},
            {"name": "LOG_LEVEL", "value": "INFO"},
            # Pin the broker via the PDU tunnel so MQTT goes over the air.
            {"name": "PDU_IFACE", "value": SLICED["pdu_iface"]},
            {"name": "PDU_ROUTE_HOSTS", "value": SLICED["ext_ip"]},
        ],
        "ports": [{"name": "metrics", "containerPort": SLICED["client_metrics_port"]}],
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "512Mi"},
        },
    }


def emit_sliced_edge(out_dir: Path):
    """Slice D IoT edge (mosquitto + controller) on central, reachable over N6."""
    labels = {"app.kubernetes.io/name": "sliced-edge"}
    dump(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "sliced-edge", "namespace": slice_ns, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        # amd64 image; do not schedule on arm64 GPU workers (e.g. gh82)
                        "nodeSelector": {"kubernetes.io/arch": "amd64"},
                        "containers": [
                            {
                                "name": "edge",
                                "image": SLICED["edge_image"],
                                "imagePullPolicy": "IfNotPresent",
                                "env": [
                                    {"name": "OTA_BIND_IP", "value": "0.0.0.0"},
                                    {"name": "METRICS_BIND_IP", "value": "0.0.0.0"},
                                    {
                                        "name": "METRICS_PORT",
                                        "value": str(SLICED["edge_metrics_port"]),
                                    },
                                    {
                                        "name": "DL_FAST_PERIOD_S",
                                        "value": SLICED["dl_fast_period_s"],
                                    },
                                    {
                                        "name": "DL_SLOW_PERIOD_S",
                                        "value": SLICED["dl_slow_period_s"],
                                    },
                                    {"name": "DL_PAYLOAD_BYTES", "value": "256"},
                                    {"name": "DEVICE_TTL_S", "value": "7200"},
                                    {"name": "MQTT_QOS", "value": "0"},
                                    {"name": "LOG_INTERVAL_S", "value": "30"},
                                    {"name": "LOG_LEVEL", "value": "INFO"},
                                ],
                                "ports": [
                                    {
                                        "name": "mqtt",
                                        "containerPort": SLICED["broker_port"],
                                    },
                                    {
                                        "name": "metrics",
                                        "containerPort": SLICED["edge_metrics_port"],
                                    },
                                ],
                                "resources": {
                                    "requests": {"cpu": "200m", "memory": "256Mi"},
                                    "limits": {"cpu": "2", "memory": "1Gi"},
                                },
                                "readinessProbe": {
                                    "tcpSocket": {"port": SLICED["broker_port"]},
                                    "initialDelaySeconds": 5,
                                    "periodSeconds": 10,
                                },
                            }
                        ],
                    },
                },
            },
        },
        out_dir / "62-deployment-sliced-edge.yaml",
    )
    dump(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "sliced-edge", "namespace": slice_ns, "labels": labels},
            "spec": {
                # externalIP on the central mgmt LAN so the UE reaches MQTT over N6.
                "type": "ClusterIP",
                "externalIPs": [SLICED["ext_ip"]],
                "selector": labels,
                "ports": [
                    {
                        "name": "mqtt",
                        "port": SLICED["broker_port"],
                        "targetPort": SLICED["broker_port"],
                        "protocol": "TCP",
                    },
                    {
                        "name": "metrics",
                        "port": SLICED["edge_metrics_port"],
                        "targetPort": SLICED["edge_metrics_port"],
                        "protocol": "TCP",
                    },
                ],
            },
        },
        out_dir / "63-service-sliced-edge.yaml",
    )


def purge_dir(directory: Path):
    if directory.is_dir():
        shutil.rmtree(directory)


def write_ns(path: Path, name: str):
    dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}, path / f"namespace-{name}.yaml")


# ---------------------------------------------------------------------------
# Retire old RAN namespaces from GitOps
# ---------------------------------------------------------------------------
if retire_old == "1":
    for rel in (
        "regional-repo/namespaces/oai-ran-nephio-example-split-deploy",
        "edge-repo/namespaces/oai-ran-nephio-example-split-deploy",
        "ue-repo/namespaces/oai-ran-nephio-example-split-deploy",
        "edge-repo/namespaces/oai-nws-1ue",
        "ue-repo/namespaces/oai-nws-1ue",
    ):
        purge_dir(repos / rel)
    # Fix regional/edge ClusterRoleBinding subject ns if present
    for cluster, repo in (("regional", "regional-repo"), ("edge", "edge-repo")):
        crb = repos / repo / "cluster" / "clusterrolebinding-oai-ran-operator-rolebinding-cluster.yaml"
        if crb.is_file():
            doc = yaml.safe_load(crb.read_text())
            for sub in doc.get("subjects", []):
                if sub.get("name") == "oai-ran-operator":
                    sub["namespace"] = slice_ns
            dump(doc, crb)


# ---------------------------------------------------------------------------
# Per-slice UPFs (NFDeployment) on the slice's site cluster
# ---------------------------------------------------------------------------
def ensure_upf_operator(site: str) -> None:
    """Copy UPF-only CN operator from central → site (no NRF; SMF uses static upfs)."""
    if site == "central":
        return
    src_ops = repos / "central-repo" / "namespaces" / ops_ns
    src_cluster = repos / "central-repo" / "cluster"
    dest_ops = repos / REPO_NAME[site] / "namespaces" / ops_ns
    dest_cluster = repos / REPO_NAME[site] / "cluster"
    dest_ops.mkdir(parents=True, exist_ok=True)
    dest_cluster.mkdir(parents=True, exist_ok=True)
    write_ns(dest_ops, ops_ns)

    # serviceaccount + controller utils (unchanged)
    for name in (
        "serviceaccount-oai-upf-operator.yaml",
        "configmap-oai-upf-controller-utils.yaml",
    ):
        src = src_ops / name
        if src.is_file():
            shutil.copy2(src, dest_ops / name)

    # op-conf / nf-conf peers unified later via apply_unified_upf_operator_peers().
    op_src = src_ops / "configmap-oai-upf-op-conf.yaml"
    if op_src.is_file():
        shutil.copy2(op_src, dest_ops / "configmap-oai-upf-op-conf.yaml")

    nf_src = src_ops / "configmap-oai-upf-nf-conf.yaml"
    if nf_src.is_file():
        shutil.copy2(nf_src, dest_ops / "configmap-oai-upf-nf-conf.yaml")

    dep_src = src_ops / "deployment-oai-upf-controller.yaml"
    if dep_src.is_file():
        doc = yaml.safe_load(dep_src.read_text())
        for env in (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("env", [])
        ):
            if env.get("name") == "TESTING":
                env["value"] = "yes"  # skip NRF initContainer off-central
        # Controller images are amd64-only; keep off arm64 GPU workers (gh81/gh82).
        # On edge also pin off usrp (no enp7s0 for macvlan N3/N4/N6).
        spec = doc["spec"]["template"]["spec"]
        node_selector = spec.setdefault("nodeSelector", {})
        node_selector["kubernetes.io/arch"] = "amd64"
        if site == "edge":
            spec["affinity"] = {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": ["edge-0", "edge-1"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        dump(doc, dest_ops / "deployment-oai-upf-controller.yaml")
    for name in (
        "clusterrole-oai-upf-operator-cluster-role.yaml",
        "clusterrolebinding-oai-upf-operator-rolebinding-cluster.yaml",
    ):
        src = src_cluster / name
        if src.is_file():
            shutil.copy2(src, dest_cluster / name)


for site in ("central", "regional", "edge"):
    upf_dir = repos / REPO_NAME[site] / "namespaces" / upf_ns
    if site != "central":
        # central keeps upf-core + ns from core render; others get a fresh oai-upf ns
        upf_dir.mkdir(parents=True, exist_ok=True)
        write_ns(upf_dir, upf_ns)
        ensure_upf_operator(site)
    else:
        upf_dir.mkdir(parents=True, exist_ok=True)
    # Same SMF N4 + NRF LB + register_nf on every site (scale-friendly).
    apply_unified_upf_operator_peers(site)
    for old in upf_dir.glob("*upf-slice-*"):
        old.unlink()

for i in range(slice_count):
    n = i + 1
    site = sites[i]
    name = f"upf-slice-{n}"
    sd = sds[i]
    n3, n4, n6 = upf_n3[i], upf_n4[i], upf_n6[i]
    n6_gw = SITE_N6_GW.get(site, gw)
    upf_dir = repos / REPO_NAME[site] / "namespaces" / upf_ns
    dump(nad(f"{name}-n3", upf_ns, nad_parent, n3), upf_dir / f"40-networkattachmentdefinition-{name}-n3.yaml")
    dump(nad(f"{name}-n4", upf_ns, nad_parent, n4), upf_dir / f"40-networkattachmentdefinition-{name}-n4.yaml")
    dump(
        nad(
            f"{name}-n6",
            upf_ns,
            nad_parent,
            n6,
            gateway=n6_gw,
            routes=[{"dst": mgmt_cidr, "gw": n6_gw}],
        ),
        upf_dir / f"40-networkattachmentdefinition-{name}-n6.yaml",
    )
    dump(
        {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFConfig",
            "metadata": {"name": f"{name}-config", "namespace": upf_ns},
            "spec": {
                "configRefs": [
                    {
                        "apiVersion": "cellular.nephio.org/v1alpha1",
                        "kind": "PLMN",
                        "metadata": {"name": "oai-plmn"},
                        "spec": {
                            "plmnInfo": [
                                {
                                    "plmnID": {"mcc": "001", "mnc": "01"},
                                    "tac": 81,
                                    "nssai": [
                                        {
                                            "sst": 1,
                                            "sd": sd,
                                            "dnnInfo": [
                                                {
                                                    "name": f"oai{n}",
                                                    "sessionType": "ipv4",
                                                    "dns": "1.1.1.1",
                                                    "subnet": f"10.1.{n}.0/24",
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ]
            },
        },
        upf_dir / f"40-nfconfig-{name}-config.yaml",
    )
    dump(
        {
            "apiVersion": "workload.nephio.org/v1alpha1",
            "kind": "NFDeployment",
            "metadata": {
                "name": name,
                "namespace": upf_ns,
                # Bump so UPF operator re-renders ConfigMap after op-conf fqdn IP change.
                "annotations": {"force-reconcile": "smf-nrf-ip-20260727"},
            },
            "spec": {
                "provider": "upf.openairinterface.org",
                "capacity": {
                    "maxDownlinkThroughput": "5G",
                    "maxUplinkThroughput": "5G",
                },
                "parametersRefs": [
                    {
                        "name": f"{name}-config",
                        "apiVersion": "workload.nephio.org/v1alpha1",
                        "kind": "NFConfig",
                    }
                ],
                "interfaces": [
                    {"name": "n3", "ipv4": {"address": f"{n3}/24", "gateway": gw}, "vlanID": 4},
                    {"name": "n4", "ipv4": {"address": f"{n4}/24", "gateway": gw}, "vlanID": 2},
                    {"name": "n6", "ipv4": {"address": f"{n6}/24", "gateway": n6_gw}, "vlanID": 3},
                ],
                "networkInstances": [
                    {"name": "vpc-internal", "interfaces": ["n4"]},
                    {
                        "name": "vpc-internet",
                        "dataNetworks": [
                            {"name": f"oai{n}", "pool": [{"prefix": f"10.1.{n}.0/24"}]}
                        ],
                        "interfaces": ["n6"],
                    },
                    {"name": "vpc-ran", "interfaces": ["n3"]},
                ],
            },
        },
        upf_dir / f"40-nfdeployment-{name}.yaml",
    )


# ---------------------------------------------------------------------------
# Central: SMF Config refs + NFConfig NSSAI + SMF operator template n3 map
# ---------------------------------------------------------------------------
# (UPF CRs live on site clusters; SMF still gets static interface Configs here.)
cn_dir = repos / "central-repo" / "namespaces" / cn_ns
for old in cn_dir.glob("25-config-smf-core-upf-slice-*"):
    old.unlink()

smf_refs = [
    {
        "name": "smf-core-config",
        "apiVersion": "workload.nephio.org/v1alpha1",
        "kind": "NFConfig",
    }
]
nssai_list = []
# Match network-slicing/nws/5gc/oai/conf/config.yaml: embb (no SD) + sd 0x01..0x05
nssai_list.append(
    {
        "sst": 1,
        "sd": "FFFFFF",
        "dnnInfo": [
            {
                "name": "oai",
                "sessionType": "ipv4",
                "dns": "1.1.1.1",
                "subnet": "10.0.0.0/24",
            }
        ],
    }
)
for i in range(slice_count):
    n = i + 1
    name = f"upf-slice-{n}"
    n3, n4, n6 = upf_n3[i], upf_n4[i], upf_n6[i]
    n6_gw = SITE_N6_GW.get(sites[i], gw)
    sd = sds[i]
    nssai_list.append(
        {
            "sst": 1,
            "sd": sd,
            "dnnInfo": [
                {
                    "name": f"oai{n}",
                    "sessionType": "ipv4",
                    "dns": "1.1.1.1",
                    "subnet": f"10.1.{n}.0/24",
                }
            ],
        }
    )
    cfg_name = f"smf-core-{name}"
    dump(
        {
            "apiVersion": "ref.nephio.org/v1alpha1",
            "kind": "Config",
            "metadata": {"name": cfg_name, "namespace": cn_ns},
            "spec": {
                "config": {
                    "apiVersion": "workload.nephio.org/v1alpha1",
                    "kind": "NFDeployment",
                    "metadata": {"name": name, "namespace": upf_ns},
                    "spec": {
                        "provider": "upf.openairinterface.org",
                        "capacity": {
                            "maxDownlinkThroughput": "5G",
                            "maxUplinkThroughput": "5G",
                        },
                        "interfaces": [
                            {"name": "n3", "ipv4": {"address": f"{n3}/24", "gateway": gw}, "vlanID": 4},
                            {"name": "n4", "ipv4": {"address": f"{n4}/24", "gateway": gw}, "vlanID": 2},
                            {"name": "n6", "ipv4": {"address": f"{n6}/24", "gateway": n6_gw}, "vlanID": 3},
                        ],
                        "networkInstances": [
                            {"name": "vpc-internal", "interfaces": ["n4"]},
                            {
                                "name": "vpc-internet",
                                "dataNetworks": [
                                    {"name": f"oai{n}", "pool": [{"prefix": f"10.1.{n}.0/24"}]}
                                ],
                                "interfaces": ["n6"],
                            },
                            {"name": "vpc-ran", "interfaces": ["n3"]},
                        ],
                    },
                }
            },
        },
        cn_dir / f"25-config-smf-core-{name}.yaml",
    )
    smf_refs.append(
        {
            "name": cfg_name,
            "apiVersion": "ref.nephio.org/v1alpha1",
            "kind": "Config",
        }
    )

dump(
    {
        "apiVersion": "workload.nephio.org/v1alpha1",
        "kind": "NFConfig",
        "metadata": {"name": "smf-core-config", "namespace": cn_ns},
        "spec": {
            "configRefs": [
                {
                    "apiVersion": "cellular.nephio.org/v1alpha1",
                    "kind": "PLMN",
                    "metadata": {"name": "oai-plmn"},
                    "spec": {
                        "plmnInfo": [
                            {
                                "plmnID": {"mcc": "001", "mnc": "01"},
                                "tac": 81,
                                "nssai": nssai_list,
                            }
                        ]
                    },
                }
            ]
        },
    },
    cn_dir / "25-nfconfig-smf-core-config.yaml",
)

# AMF must advertise the same S-NSSAIs or NG Setup fails with "Unknown PLMN"
amf_cfg_path = cn_dir / "24-nfconfig-amf-core-config.yaml"
amf_cfg = yaml.safe_load(amf_cfg_path.read_text()) if amf_cfg_path.is_file() else {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFConfig",
    "metadata": {"name": "amf-core-config", "namespace": cn_ns},
    "spec": {"configRefs": []},
}
new_refs = []
plmn_written = False
for ref in amf_cfg.get("spec", {}).get("configRefs", []):
    if ref.get("kind") == "PLMN":
        ref = {
            "apiVersion": "cellular.nephio.org/v1alpha1",
            "kind": "PLMN",
            "metadata": {"name": "oai-plmn"},
            "spec": {
                "plmnInfo": [
                    {
                        "plmnID": {"mcc": "001", "mnc": "01"},
                        "tac": 81,
                        "nssai": nssai_list,
                    }
                ]
            },
        }
        plmn_written = True
    new_refs.append(ref)
if not plmn_written:
    new_refs.insert(
        0,
        {
            "apiVersion": "cellular.nephio.org/v1alpha1",
            "kind": "PLMN",
            "metadata": {"name": "oai-plmn"},
            "spec": {
                "plmnInfo": [
                    {
                        "plmnID": {"mcc": "001", "mnc": "01"},
                        "tac": 81,
                        "nssai": nssai_list,
                    }
                ]
            },
        },
    )
amf_cfg["spec"]["configRefs"] = new_refs
dump(amf_cfg, amf_cfg_path)

smf_nf = yaml.safe_load((cn_dir / "25-nfdeployment-smf-core.yaml").read_text())
smf_nf["spec"]["parametersRefs"] = smf_refs
dump(smf_nf, cn_dir / "25-nfdeployment-smf-core.yaml")

# Patch SMF operator template: per-UPF n3_local_ipv4 via jinja loop index
ops_smf = repos / "central-repo" / "namespaces" / ops_ns / "configmap-oai-smf-nf-conf.yaml"
if ops_smf.is_file():
    doc = yaml.safe_load(ops_smf.read_text())
    tpl = doc["data"]["smf.yaml"]
    n3_list = ", ".join(f"'{x}'" for x in upf_n3)
    sd_list = ", ".join(f"'{sd}'" for sd in sds)
    # Per-UPF N3 + sNssai so SMF can select UPF by slice (not only last UPF).
    new_block = (
        "upfs:\n"
        f"    {{% set n3_addrs = [{n3_list}] %}}\n"
        f"    {{% set slice_sds = [{sd_list}] %}}\n"
        "    {%- for i in conf['upfs'] %}\n"
        "    - host: {{ i }}\n"
        "      config:\n"
        "        enable_usage_reporting: no\n"
        "        n3_local_ipv4: {{ n3_addrs[loop.index0] if loop.index0 < (n3_addrs|length) else n3_addrs[0] }}\n"
        "      upf_info:\n"
        "        sNssaiUpfInfoList:\n"
        "          - sNssai:\n"
        "              sst: 1\n"
        "              sd: \"{{ slice_sds[loop.index0] if loop.index0 < (slice_sds|length) else slice_sds[0] }}\"\n"
        "            dnnUpfInfoList:\n"
        "              - dnn: oai{{ loop.index }}\n"
        "    {%- endfor %}"
    )
    tpl2, nsub = re.subn(
        r"upfs:\n    \{%.*?\{%- endfor %\}",
        new_block,
        tpl,
        count=1,
        flags=re.S,
    )
    if nsub:
        tpl = tpl2
    else:
        print("WARNING: failed to patch SMF upfs template block")
    # Static upfs list (co-located multi-cluster); NRF discovery cannot see off-central UPFs.
    tpl = tpl.replace("discover_upf: yes", "discover_upf: no")
    doc["data"]["smf.yaml"] = tpl
    dump(doc, ops_smf)

# Patch MySQL SessionManagementSubscription SD for IMSIs 101-105
mysql_cm = cn_dir / "05-configmap-mysql-initialization.yaml"
if mysql_cm.is_file():
    doc = yaml.safe_load(mysql_cm.read_text())
    key = next(iter(doc["data"]))
    sql = doc["data"][key]
    for i, imsi in enumerate(imsis):
        sd_dec = str(int(sds[i], 16))
        # Replace sd for this ueid in SessionManagementSubscription rows
        pattern = rf"\('{imsi}',\s*'00101',\s*'\{{\\\"sst\\\": 1, \\\"sd\\\": \\\"[0-9]+\\\"\}}'"
        repl = f"('{imsi}', '00101', '{{\\\"sst\\\": 1, \\\"sd\\\": \\\"{sd_dec}\\\"}}'"
        sql2, nsub = re.subn(pattern, repl, sql, count=1)
        if nsub:
            sql = sql2
        else:
            # try unescaped style inside the blob
            pattern2 = rf"\('{imsi}', '00101', '{{\"sst\": 1, \"sd\": \"[0-9]+\"}}'"
            sql = re.sub(
                pattern2,
                f"('{imsi}', '00101', '{{\"sst\": 1, \"sd\": \"{sd_dec}\"}}'",
                sql,
                count=1,
            )
    doc["data"][key] = sql
    dump(doc, mysql_cm)


# ---------------------------------------------------------------------------
# Regional: RAN operator + CU-UP for slices sited here (see emit_cuup below)
# Central:  slice ns for CU-UP1
# ---------------------------------------------------------------------------
reg_dir = repos / "regional-repo" / "namespaces" / slice_ns
purge_dir(reg_dir)
reg_dir.mkdir(parents=True)
write_ns(reg_dir, slice_ns)

central_slice_dir = repos / "central-repo" / "namespaces" / slice_ns
purge_dir(central_slice_dir)
central_slice_dir.mkdir(parents=True)
write_ns(central_slice_dir, slice_ns)

dump(
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "oai-ran-operator", "namespace": slice_ns}},
    reg_dir / "serviceaccount-oai-ran-operator.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "oai-ran-operator", "namespace": slice_ns},
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "oai-ran-operator",
                    "app.kubernetes.io/component": "controller",
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "oai-ran-operator",
                        "app.kubernetes.io/component": "controller",
                    }
                },
                "spec": {
                    "nodeSelector": {"kubernetes.io/arch": "amd64"},
                    "serviceAccountName": "oai-ran-operator",
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": ["regional-0", "regional-1"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "containers": [
                        {
                            "name": "operator",
                            "image": ran_op_image,
                            "resources": {
                                "limits": {"cpu": "500m", "memory": "128Mi"},
                                "requests": {"cpu": "10m", "memory": "64Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    },
    reg_dir / "deployment-oai-ran-operator.yaml",
)


def emit_cuup(i: int, out_dir: Path, node_names: list) -> None:
    n = i + 1
    sd = sds[i]
    e1, f1u, n3 = cuup_e1[i], cuup_f1u[i], cuup_n3[i]
    upf_peer = upf_n3[i]
    cu_id = f"0xe0{n}"
    for iface, ip in (("e1", e1), ("f1u", f1u), ("n3", n3)):
        dump(
            nad(f"cuup-slice{n}-{iface}", slice_ns, nad_parent, ip),
            out_dir / f"30-networkattachmentdefinition-cuup-slice{n}-{iface}.yaml",
        )
    cuup_conf = f"""Active_gNBs = ( "oai-cu-up-{n}");
Asn1_verbosity = "none";
sa = 1;
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_CU_UP_ID = {cu_id};
    gNB_name  =  "oai-cu-up-{n}";
    tracking_area_code  =  0x0051;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({{ sst = 1, sd = 0x{sd} }})
                }});

    tr_s_preference = "f1";
    local_s_address = "{f1u}";
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
        ipv4_cuup = "{e1}";
      }}
    )

    NETWORK_INTERFACES :
    {{
        GNB_IPV4_ADDRESS_FOR_NG_AMF              = "{n3}";
        GNB_IPV4_ADDRESS_FOR_NGU                 = "{n3}";
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
    dump(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"oai-cu-up-{n}-configmap", "namespace": slice_ns},
            "data": {"gnb.conf": cuup_conf},
        },
        out_dir / f"31-configmap-oai-cu-up-{n}-configmap.yaml",
    )
    dump(
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": f"oai-cu-up-{n}-sa", "namespace": slice_ns},
        },
        out_dir / f"32-serviceaccount-oai-cu-up-{n}-sa.yaml",
    )
    dump(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"oai-cu-up-{n}",
                "namespace": slice_ns,
                "labels": {"app.kubernetes.io/name": f"oai-cu-up-{n}", "slice": str(n)},
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app.kubernetes.io/name": f"oai-cu-up-{n}"}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app": f"oai-cu-up-{n}",
                            "app.kubernetes.io/name": f"oai-cu-up-{n}",
                            "slice": str(n),
                        },
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": networks_annot(
                                [
                                    (f"cuup-slice{n}-e1", "e1", e1),
                                    (f"cuup-slice{n}-f1u", "f1u", f1u),
                                    (f"cuup-slice{n}-n3", "n3", n3),
                                ]
                            ),
                            "oai.nephio.org/upf-n3": upf_peer,
                            "oai.nephio.org/site": sites[i],
                        },
                    },
                    "spec": {
                        "serviceAccountName": f"oai-cu-up-{n}-sa",
                        "terminationGracePeriodSeconds": 5,
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [
                                        {
                                            "matchExpressions": [
                                                {
                                                    "key": "kubernetes.io/hostname",
                                                    "operator": "In",
                                                    "values": list(node_names),
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        },
                        "containers": [
                            {
                                "name": "cuup",
                                "image": cuup_image,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"privileged": True},
                                "env": [
                                    {"name": "TZ", "value": "Europe/Paris"},
                                    {
                                        "name": "USE_ADDITIONAL_OPTIONS",
                                        "value": "--log_config.global_log_options level,nocolor,time",
                                    },
                                    {"name": "USE_VOLUMED_CONF", "value": "yes"},
                                ],
                                "ports": [
                                    {"name": "n3", "containerPort": 2152, "protocol": "UDP"},
                                    {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "configuration",
                                        "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                        "subPath": "gnb.conf",
                                    }
                                ],
                            },
                            debug_ctr,
                        ],
                        "volumes": [
                            {
                                "name": "configuration",
                                "configMap": {"name": f"oai-cu-up-{n}-configmap"},
                            }
                        ],
                    },
                },
            },
        },
        out_dir / f"33-deployment-oai-cu-up-{n}.yaml",
    )


# ---------------------------------------------------------------------------
# Edge: CU-CP + DU + UEs + FlexRIC (+ CU-UPs for slices 3–5 via emit_cuup)
# ---------------------------------------------------------------------------
edge_dir = repos / "edge-repo" / "namespaces" / slice_ns
purge_dir(edge_dir)
edge_dir.mkdir(parents=True)
write_ns(edge_dir, slice_ns)

# Emit CU-UPs onto the co-located site (central / regional / edge).
SLICE_DIR = {
    "central": central_slice_dir,
    "regional": reg_dir,
    "edge": edge_dir,
}
for i in range(slice_count):
    site = sites[i]
    emit_cuup(i, SLICE_DIR[site], SITE_NODES[site])

dump(
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "oai-ran-operator", "namespace": slice_ns}},
    edge_dir / "serviceaccount-oai-ran-operator.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "oai-ran-operator", "namespace": slice_ns},
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "oai-ran-operator",
                    "app.kubernetes.io/component": "controller",
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "oai-ran-operator",
                        "app.kubernetes.io/component": "controller",
                    }
                },
                "spec": {
                    "nodeSelector": {"kubernetes.io/arch": "amd64"},
                    "serviceAccountName": "oai-ran-operator",
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": ["edge-0", "edge-1"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "containers": [
                        {
                            "name": "operator",
                            "image": ran_op_image,
                            "resources": {
                                "limits": {"cpu": "500m", "memory": "128Mi"},
                                "requests": {"cpu": "10m", "memory": "64Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    },
    edge_dir / "deployment-oai-ran-operator.yaml",
)

# CU-CP NADs
for nad_name, ip in (
    ("cucp-edge-n2", cucp_n2),
    ("cucp-edge-f1c", cucp_f1c),
    ("cucp-edge-e1", cucp_e1),
):
    dump(nad(nad_name, slice_ns, nad_parent, ip), edge_dir / f"12-networkattachmentdefinition-{nad_name}.yaml")

snssai_cucp = "{ sst = 1, sd = 0xFFFFFF }, " + ", ".join(
        f"{{ sst = 1, sd = 0x{sd} }}" for sd in sds
    )
cucp_conf = f"""Active_gNBs = ( "oai-cu-cp");
Asn1_verbosity = "none";
sa = 1;

gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_name  =  "oai-cu-cp";
    tracking_area_code  =  0x0051;
    plmn_list = ({{ mcc = 001;
                   mnc = 01;
                   mnc_length =2;
                   snssaiList = ({snssai_cucp})
                }});

    nr_cellid = 12345678;
    tr_s_preference = "f1";
    local_s_address = "{cucp_f1c}";
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
        ipv4_cucp = "{cucp_e1}";
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
f1ap_log_level                        ="info";
ngap_log_level                        ="debug";
}};
"""
dump(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "oai-cu-cp-configmap", "namespace": slice_ns},
        "data": {"gnb.conf": cucp_conf},
    },
    edge_dir / "13-configmap-oai-cu-cp-configmap.yaml",
)
dump(
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "oai-cu-cp-sa", "namespace": slice_ns}},
    edge_dir / "14-serviceaccount-oai-cu-cp-sa.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "oai-cu-cp",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "oai-cu-cp"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-cu-cp"}},
            "template": {
                "metadata": {
                    "labels": {"app": "oai-cu-cp", "app.kubernetes.io/name": "oai-cu-cp"},
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [
                                ("cucp-edge-e1", "e1", cucp_e1),
                                ("cucp-edge-f1c", "f1c", cucp_f1c),
                                ("cucp-edge-n2", "n2", cucp_n2),
                            ]
                        )
                    },
                },
                "spec": {
                    "serviceAccountName": "oai-cu-cp-sa",
                    "terminationGracePeriodSeconds": 5,
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": ["edge-0", "edge-1"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "containers": [
                        {
                            "name": "cucp",
                            "image": cucp_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {"privileged": True},
                            "env": [
                                {"name": "TZ", "value": "Europe/Paris"},
                                {
                                    "name": "USE_ADDITIONAL_OPTIONS",
                                    "value": "--log_config.global_log_options level,nocolor,time",
                                },
                                {"name": "USE_VOLUMED_CONF", "value": "yes"},
                            ],
                            "ports": [
                                {"name": "n2", "containerPort": 36412, "protocol": "SCTP"},
                                {"name": "e1", "containerPort": 38462, "protocol": "SCTP"},
                                {"name": "f1c", "containerPort": 38472, "protocol": "UDP"},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "configuration",
                                    "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                    "subPath": "gnb.conf",
                                }
                            ],
                        },
                        debug_ctr,
                    ],
                    "volumes": [
                        {"name": "configuration", "configMap": {"name": "oai-cu-cp-configmap"}}
                    ],
                },
            },
        },
    },
    edge_dir / "15-deployment-oai-cu-cp.yaml",
)

dump(nad("du-edge-f1", slice_ns, usrp_iface, du_f1), edge_dir / "12-networkattachmentdefinition-du-edge-f1.yaml")
dump(nad("du-edge-rf", slice_ns, usrp_iface, du_rf), edge_dir / "12-networkattachmentdefinition-du-edge-rf.yaml")

snssai_du = "{ sst = 1, sd = 0xFFFFFF }, " + ", ".join(
        f"{{ sst = 1, sd = 0x{sd} }}" for sd in sds
    )
# Align with nws/configs/gnb/gnb.du.sa.band78.133prb.rfsim.open5gs.5slices.nsboth.yaml
du_conf = f"""Active_gNBs = ( "oai-du");
Asn1_verbosity = "none";
gNBs =
(
 {{
    gNB_ID = 0xe00;
    gNB_DU_ID = 0xe00;
    gNB_name  =  "oai-du";
    disable_harq = 0;
    TIMERS = {{
      sr_ProhibitTimer = 0;
      sr_TransMax = 64;
      sr_ProhibitTimer_v1700 = 0;
      t300 = 400;
      t301 = 400;
      t310 = 2000;
      n310 = 20;
      t311 = 3000;
      n311 = 1;
      t319 = 400;
    }};
    tracking_area_code  =  0x0051;
    uess_agg_levels = [8, 8, 8, 5, 2];
    coreset_duration = 2;
    plmn_list = ({{ mcc = 001; mnc = 01; mnc_length = 2; snssaiList = ({snssai_du}) }});
    nr_cellid = 12345678;
    min_rxtxtime = 6;
    servingCellConfigCommon = (
    {{
      physCellId = 0;
      absoluteFrequencySSB = 620640;
      dl_frequencyBand = 78;
      dl_absoluteFrequencyPointA = 620112;
      dl_offstToCarrier = 0;
      dl_subcarrierSpacing = 1;
      dl_carrierBandwidth = 133;
      initialDLBWPlocationAndBandwidth = 36300;
      initialDLBWPsubcarrierSpacing = 1;
      initialDLBWPcontrolResourceSetZero = 10;
      initialDLBWPsearchSpaceZero = 0;
      ul_frequencyBand = 78;
      ul_offstToCarrier = 0;
      ul_subcarrierSpacing = 1;
      ul_carrierBandwidth = 133;
      pMax = 20;
      initialULBWPlocationAndBandwidth = 36300;
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
      nrofUplinkSymbols = 6;
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
    pusch_TargetSNRx10 = 150;
    pucch_TargetSNRx10 = 150;
    stats_max_ue = 17;
    # NSBOTH: same NS scheduler for UL and DL (match nws 5slices.nsboth).
    dl_scheduler_type = 1;
    ul_scheduler_type = 1;
    pusch_FailureThres = 1000;
    pucch_FailureThres = 1000;
    dl_harq_round_max = 8;
    ul_harq_round_max = 8;
  }}
);

Slices = (
  {{ slice_id = 0; sst = 1; sd = 0xffffff; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 100.0; }},
  {{ slice_id = 1; sst = 1; sd = 0x000001; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 100.0; }},
  {{ slice_id = 2; sst = 1; sd = 0x000002; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 100.0; }},
  {{ slice_id = 3; sst = 1; sd = 0x000003; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 50.0; }},
  {{ slice_id = 4; sst = 1; sd = 0x000004; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 50.0; }},
  {{ slice_id = 5; sst = 1; sd = 0x000005; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 50.0; }}
);

L1s = (
{{
  num_cc = 1;
  tr_n_preference = "local_mac";
  prach_dtx_threshold = 200;
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
    bf_weights = [0x00007fff, 0x0000, 0x0000, 0x0000];
    clock_src = "internal";
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

e2_agent = {{
  near_ric_ip_addr = "{flexric_ip}";
  sm_dir = "/usr/local/lib/flexric/";
}};
"""
dump(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "oai-du-configmap", "namespace": slice_ns},
        "data": {"gnb.conf": du_conf},
    },
    edge_dir / "13-configmap-oai-du-configmap.yaml",
)
dump(
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "oai-du-sa", "namespace": slice_ns}},
    edge_dir / "14-serviceaccount-oai-du-sa.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "oai-du",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "oai-du"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-du"}},
            "template": {
                "metadata": {
                    "labels": {"app": "oai-du", "app.kubernetes.io/name": "oai-du"},
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [
                                ("du-edge-f1", "f1", du_f1),
                                ("du-edge-rf", "rf", du_rf),
                            ]
                        )
                    },
                },
                "spec": {
                    "serviceAccountName": "oai-du-sa",
                    "terminationGracePeriodSeconds": 5,
                    "nodeSelector": {"kubernetes.io/hostname": "usrp"},
                    "containers": [
                        {
                            "name": "du",
                            "image": du_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {"privileged": True},
                            "env": [
                                {
                                    "name": "USE_ADDITIONAL_OPTIONS",
                                    "value": (
                                        "--rfsim --log_config.global_log_options level,nocolor,time"
                                    ),
                                }
                            ],
                            "ports": [
                                {"name": "f1c", "containerPort": 38472, "protocol": "SCTP"},
                                {"name": "f1u", "containerPort": 2152, "protocol": "UDP"},
                                {"name": "rfsim", "containerPort": 4043, "protocol": "TCP"},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "configuration",
                                    "mountPath": "/opt/oai-gnb/etc/gnb.conf",
                                    "subPath": "gnb.conf",
                                }
                            ],
                        },
                        debug_ctr,
                    ],
                    "volumes": [
                        {"name": "configuration", "configMap": {"name": "oai-du-configmap"}}
                    ],
                },
            },
        },
    },
    edge_dir / "15-deployment-oai-du.yaml",
)
dump(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": "oai-du",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "oai-du"},
        },
        "spec": {
            "type": "ClusterIP",
            "clusterIP": None,
            "selector": {"app.kubernetes.io/name": "oai-du"},
            "ports": [
                {"name": "f1c", "port": 38472, "protocol": "SCTP", "targetPort": 38472},
                {"name": "f1u", "port": 2152, "protocol": "UDP", "targetPort": 2152},
            ],
        },
    },
    edge_dir / "16-service-oai-du.yaml",
)

# FlexRIC nearRT-RIC + slice xApp (E2 on macvlan; Swagger via MetalLB)
flexric_conf = f"""[NEAR-RIC]
NEAR_RIC_IP = {flexric_ip}

[XAPP]
DB_DIR = /tmp/
DB_NAME = xapp_db
"""
dump(
    nad("flexric-edge-e2", slice_ns, nad_parent, flexric_ip),
    edge_dir / "40-networkattachmentdefinition-flexric-edge-e2.yaml",
)
dump(
    nad("xapp-edge-e2", slice_ns, nad_parent, xapp_e2_ip),
    edge_dir / "40-networkattachmentdefinition-xapp-edge-e2.yaml",
)
dump(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "oai-flexric-configmap", "namespace": slice_ns},
        "data": {"flexric.conf": flexric_conf},
    },
    edge_dir / "41-configmap-oai-flexric-configmap.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "oai-flexric",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "oai-flexric"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-flexric"}},
            "template": {
                "metadata": {
                    "labels": {"app": "oai-flexric", "app.kubernetes.io/name": "oai-flexric"},
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [("flexric-edge-e2", "e2", flexric_ip)]
                        )
                    },
                },
                "spec": {
                    "terminationGracePeriodSeconds": 5,
                    # Keep off usrp; pin to edge-1 so xApp on edge-0 can reach RIC
                    # (same-node macvlan cannot hairpin).
                    "nodeSelector": {"kubernetes.io/hostname": "edge-1"},
                    "containers": [
                        {
                            "name": "flexric",
                            "image": flexric_image,
                            "imagePullPolicy": "IfNotPresent",
                            "workingDir": "/tmp",
                            "securityContext": {
                                "privileged": True,
                                "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]},
                            },
                            "command": ["stdbuf", "-o0", "nearRT-RIC"],
                            "env": [
                                {"name": "E2AP_VERSION", "value": "E2AP_V3"},
                                {"name": "KPM_VERSION", "value": "KPM_V3_00"},
                                {"name": "ASAN_OPTIONS", "value": "detect_leaks=0"},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "configuration",
                                    "mountPath": "/usr/local/etc/flexric/flexric.conf",
                                    "subPath": "flexric.conf",
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "configuration",
                            "configMap": {"name": "oai-flexric-configmap"},
                        }
                    ],
                },
            },
        },
    },
    edge_dir / "42-deployment-oai-flexric.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "nws-xapp",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "nws-xapp"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "nws-xapp"}},
            "template": {
                "metadata": {
                    "labels": {"app": "nws-xapp", "app.kubernetes.io/name": "nws-xapp"},
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [("xapp-edge-e2", "e2", xapp_e2_ip)]
                        )
                    },
                },
                "spec": {
                    "terminationGracePeriodSeconds": 5,
                    # Opposite node from FlexRIC (macvlan hairpin); also hosts mgmt IP .230.
                    "nodeSelector": {"kubernetes.io/hostname": "edge-0"},
                    "containers": [
                        {
                            "name": "xapp",
                            "image": xapp_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {
                                "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}
                            },
                            # FlexRIC asserts if E42 SETUP arrives before any E2 node.
                            # E2 is SCTP (not TCP) — do not probe /dev/tcp on :36422.
                            # Sleep briefly so DU can attach after RIC restart.
                            "command": [
                                "bash",
                                "-lc",
                                (
                                    "set -e; "
                                    'echo "waiting 30s for DU E2 attach to ${NEAR_RIC_IP}"; sleep 30; '
                                    "exec python3 -u /xapp/xapp_slice.py "
                                    "--conf /etc/flexric/flexric.conf "
                                    "--out /tmp/rt_slice_stats.json "
                                    "--ns-out /tmp/rt_ns_slice_policy.json --print"
                                ),
                            ],
                            "env": [
                                {
                                    "name": "PYTHONPATH",
                                    "value": "/usr/local/flexric/xApp/python3",
                                },
                                {
                                    "name": "LD_LIBRARY_PATH",
                                    "value": "/usr/local/lib:/flexric/build/src/xApp",
                                },
                                {
                                    "name": "FLEXRIC_CONF",
                                    "value": "/etc/flexric/flexric.conf",
                                },
                                {"name": "NWS_XAPP_IN_DOCKER", "value": "1"},
                                {"name": "NEAR_RIC_IP", "value": flexric_ip},
                                {"name": "NWS_XAPP_API_HOST", "value": "0.0.0.0"},
                                {
                                    "name": "NWS_XAPP_API_PORT",
                                    "value": str(xapp_api_port),
                                },
                                {
                                    "name": "NWS_XAPP_LAB_IP",
                                    "value": xapp_swagger_vip,
                                },
                            ],
                            "ports": [
                                {
                                    "name": "http",
                                    "containerPort": xapp_api_port,
                                    "protocol": "TCP",
                                }
                            ],
                            "volumeMounts": [
                                {
                                    "name": "configuration",
                                    "mountPath": "/etc/flexric/flexric.conf",
                                    "subPath": "flexric.conf",
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "configuration",
                            "configMap": {"name": "oai-flexric-configmap"},
                        }
                    ],
                },
            },
        },
    },
    edge_dir / "43-deployment-nws-xapp.yaml",
)
dump(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": "nws-xapp",
            "namespace": slice_ns,
            "labels": {"app.kubernetes.io/name": "nws-xapp"},
        },
        "spec": {
            # Publish on edge control-plane mgmt IP (10.1.132.x) for operator LAN.
            "type": "ClusterIP",
            "externalIPs": [xapp_swagger_vip],
            "selector": {"app.kubernetes.io/name": "nws-xapp"},
            "ports": [
                {
                    "name": "http",
                    "port": xapp_api_port,
                    "targetPort": xapp_api_port,
                    "protocol": "TCP",
                }
            ],
        },
    },
    edge_dir / "44-service-nws-xapp.yaml",
)

# 5 UEs on usrp
for i in range(slice_count):
    n = i + 1
    sd = sds[i]
    rf_ip = ue_rf[i]
    imsi = imsis[i]
    dump(
        nad(f"ue{n}-sim-rf", slice_ns, usrp_iface, rf_ip),
        edge_dir / f"50-networkattachmentdefinition-ue{n}-sim-rf.yaml",
    )
    ue_conf = f"""uicc0 = {{
  imsi = "{imsi}";
  key = "fec86ba6eb707ed08905757b1bb44b8f";
  opc = "C42449363BBAD02B66D16BC975D77CC1";
  dnn = "oai{n}";
  nssai_sst = 1;
  nssai_sd = 0x{sd.upper()};
}}

thread-pool = "-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1";

rfsimulator = {{
  serveraddr = "{du_rf}";
}}

log_config = {{
  global_log_options = "level,nocolor,time";
}}

channelmod = {{
  max_chan = 10;
  modellist = "modellist_rfsimu_1";
  modellist_rfsimu_1 = (
    {{
      model_name = "rfsimu_channel_enB0";
      type = "AWGN";
      ploss_dB = 20;
      noise_power_dB = -4;
      forgetfact = 0;
      offset = 0;
      ds_tdl = 0;
    }},
    {{
      model_name = "rfsimu_channel_ue0";
      type = "AWGN";
      ploss_dB = 20;
      noise_power_dB = -2;
      forgetfact = 0;
      offset = 0;
      ds_tdl = 0;
    }}
  );
}}
"""
    dump(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"oai-ue-{n}-configmap", "namespace": slice_ns},
            "data": {"ue.conf": ue_conf},
        },
        edge_dir / f"51-configmap-oai-ue-{n}-configmap.yaml",
    )
    dump(
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": f"oai-ue-{n}-sa", "namespace": slice_ns},
        },
        edge_dir / f"52-serviceaccount-oai-ue-{n}-sa.yaml",
    )
    dump(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"oai-ue-{n}",
                "namespace": slice_ns,
                "labels": {"app.kubernetes.io/name": f"oai-ue-{n}", "slice": str(n)},
            },
            "spec": {
                "replicas": 1 if n <= ue_active else 0,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app.kubernetes.io/name": f"oai-ue-{n}"}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app": f"oai-ue-{n}",
                            "app.kubernetes.io/name": f"oai-ue-{n}",
                            "slice": str(n),
                        },
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": networks_annot(
                                [(f"ue{n}-sim-rf", "rf", rf_ip)]
                            )
                        },
                    },
                    "spec": {
                        "serviceAccountName": f"oai-ue-{n}-sa",
                        "terminationGracePeriodSeconds": 5,
                        "nodeSelector": {"kubernetes.io/hostname": "usrp"},
                        "containers": [
                            {
                                "name": "ue",
                                "image": ue_image,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"privileged": True},
                                "env": [
                                    {
                                        "name": "USE_ADDITIONAL_OPTIONS",
                                        "value": (
                                            # Match nws split 133 PRB UE args
                                            f"-r 133 --numerology 1 -C 3325620000 --ssb 144 "
                                            f"--rfsim --log_config.global_log_options level,nocolor,time "
                                            f"--rfsimulator.serveraddr {du_rf}"
                                        ),
                                    },
                                    {"name": "TZ", "value": "Europe/Paris"},
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "configuration",
                                        "mountPath": "/opt/oai-nr-ue/etc/nr-ue.conf",
                                        "subPath": "ue.conf",
                                    }
                                ],
                            },
                            # Shares oaitun_ue1 with ue — ping/traffic over the PDU tunnel
                            debug_ctr,
                            # Slice A CCTV publisher sidecar (only the chosen slice's UE):
                            # pushes over the air to the central analyzer.
                            *([slicea_publisher_container()] if n == SLICEA["ue_slice"] else []),
                            # Slice D IoT client sidecar (only the chosen slice's UE):
                            # MQTT over the air to the central sliced-edge broker.
                            *([sliced_client_container()] if n == SLICED["ue_slice"] else []),
                        ],
                        "volumes": [
                            {
                                "name": "configuration",
                                "configMap": {"name": f"oai-ue-{n}-configmap"},
                            }
                        ],
                    },
                },
            },
        },
        edge_dir / f"55-deployment-oai-ue-{n}.yaml",
    )

# --- Slice A CCTV analyzer (server) on central ---
emit_slicea_analyzer(central_slice_dir)
# --- Slice D IoT edge (broker + controller) on central ---
emit_sliced_edge(central_slice_dir)

print(f"Rendered {slice_ns}:")
for i in range(slice_count):
    print(
        f"  slice{i+1} @{sites[i]}: "
        f"UPF {upf_n3[i]}/{upf_n4[i]}/{upf_n6[i]}  "
        f"CU-UP {cuup_e1[i]}/{cuup_f1u[i]}/{cuup_n3[i]}"
    )
print(f"  DU F1 {du_f1} + rfsim {du_rf} + UEs {', '.join(ue_rf)} on usrp ({usrp_iface})")
print(f"  UE replicas: first {ue_active}/{slice_count} active (OAI_UE_ACTIVE_COUNT)")
print(f"  CU-CP (edge) N2/F1/E1 {cucp_n2}/{cucp_f1c}/{cucp_e1} → AMF {amf_n2}")
print(f"  FlexRIC {flexric_ip} + xApp E2 {xapp_e2_ip}")
print(f"  xApp Swagger http://{xapp_swagger_vip}:{xapp_api_port}/docs")
print(
    f"  Slice A CCTV: analyzer @central "
    f"rtsp://{SLICEA['ext_ip']}:{SLICEA['rtsp_port']}/{SLICEA['stream_path']} "
    f"({SLICEA['protocol']}, YOLO={SLICEA['yolo_device']}); "
    f"publisher sidecar in oai-ue-{SLICEA['ue_slice']} over {SLICEA['pdu_iface']}"
)
print(
    f"  Slice D IoT: broker @central "
    f"mqtt://{SLICED['ext_ip']}:{SLICED['broker_port']} "
    f"(devices={SLICED['num_devices']}); "
    f"client sidecar in oai-ue-{SLICED['ue_slice']} over {SLICED['pdu_iface']}"
)
PY

echo "Done. Push with:"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'oai-slice: co-located UPF+CUUP' central regional edge"
