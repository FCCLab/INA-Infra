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
OAI_IMAGE_TAG="${OAI_IMAGE_TAG:-nws-v0.5-amd64}"
OAI_CUCP_IMAGE="${OAI_CUCP_IMAGE:-${OAI_REGISTRY}/oai-cucp:${OAI_IMAGE_TAG}}"
OAI_DU_IMAGE="${OAI_DU_IMAGE:-${OAI_REGISTRY}/oai-du:${OAI_IMAGE_TAG}}"
OAI_CUUP_IMAGE="${OAI_CUUP_IMAGE:-${OAI_REGISTRY}/oai-nr-cuup:${OAI_IMAGE_TAG}}"
OAI_NR_UE_IMAGE="${OAI_NR_UE_IMAGE:-${OAI_REGISTRY}/oai-nr-ue:${OAI_IMAGE_TAG}}"
OAI_FLEXRIC_IMAGE="${OAI_FLEXRIC_IMAGE:-${OAI_REGISTRY}/oai-flexric:${OAI_IMAGE_TAG}}"
OAI_XAPP_IMAGE="${OAI_XAPP_IMAGE:-${OAI_REGISTRY}/nws-xapp:${OAI_IMAGE_TAG}}"
DEBUG_IMAGE="${OAI_DEBUG_SIDECAR_IMAGE:-docker.io/nicolaka/netshoot}"
SLICE_COUNT="${OAI_SLICE_COUNT:-5}"
# Only first N UE deployments get replicas=1 (rest stay 0). Default: all slices.
UE_ACTIVE_COUNT="${OAI_UE_ACTIVE_COUNT:-$SLICE_COUNT}"
RETIRE_OLD="${RETIRE_OLD:-1}"

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
SMF_N4="$(oai_macvlan_ip central 2)"
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
            {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
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

    # op-conf: point NRF/SMF FQDNs at loopback — ClusterIP names do not resolve off-central.
    op_src = src_ops / "configmap-oai-upf-op-conf.yaml"
    if op_src.is_file():
        doc = yaml.safe_load(op_src.read_text())
        raw = doc["data"]["upf.yaml"]
        raw = raw.replace(
            "nrf: 'oai-nrf.oai-cn.svc.cluster.local'",
            "nrf: '127.0.0.1'",
        ).replace(
            "smf: 'oai-smf.oai-cn.svc.cluster.local'",
            "smf: '127.0.0.1'",
        )
        doc["data"]["upf.yaml"] = raw
        dump(doc, dest_ops / "configmap-oai-upf-op-conf.yaml")

    # nf-conf: disable NRF registration (SMF already has static UPF Config refs).
    nf_src = src_ops / "configmap-oai-upf-nf-conf.yaml"
    if nf_src.is_file():
        doc = yaml.safe_load(nf_src.read_text())
        tpl = doc["data"]["upf.yaml"]
        tpl = tpl.replace("register_nf:\n  upf: 'yes'", "register_nf:\n  upf: 'no'")
        doc["data"]["upf.yaml"] = tpl
        dump(doc, dest_ops / "configmap-oai-upf-nf-conf.yaml")

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
        # Keep UPF pods off usrp (no enp7s0 for macvlan N3/N4/N6).
        if site == "edge":
            spec = doc["spec"]["template"]["spec"]
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
            "metadata": {"name": name, "namespace": upf_ns},
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
    # Match NWS stable NSUL: DL=PF (0), UL=NS (1). NSBOTH (dl=1) can starve SRB and trigger RLC max RETX.
    dl_scheduler_type = 0;
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
                            }
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
PY

echo "Done. Push with:"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'oai-slice: co-located UPF+CUUP' central regional edge"
