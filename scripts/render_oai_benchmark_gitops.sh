#!/usr/bin/env bash
# Render oai-benchmark GitOps: non-slice OAI stack for throughput/latency benchmarks.
#   central — dedicated 5GC CP executors in oai-benchmark (no Kopf operators)
#   edge    — oai-ran-controller + NFDeployments (operator create-once CU/CUUP/DU);
#             UPF + nrUE static; FlexRIC near-RT RIC + nws-xapp; DU+UE node via
#             OAI_BENCH_* (default usrp); UPF N6=DHCP
# Deploy: render → push → operator creates RAN → patch_oai_benchmark_ran_vpc.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
BENCH_NS="${OAI_BENCHMARK_NS:-oai-benchmark}"
OAI_CN_TAG="${OAI_CN_TAG:-v2.0.1}"
OAI_CN_IMAGE="${OAI_CN_IMAGE:-docker.io/oaisoftwarealliance}"
OAI_SMF_IMAGE="${OAI_SMF_IMAGE:-10.1.132.30:5000/oaisoftwarealliance/oai-smf:v2.2.1-dnn-fix-4}"
OAI_UPF_IMAGE="${OAI_UPF_IMAGE:-docker.io/oaisoftwarealliance/oai-upf:v2.2.1}"
OAI_REGISTRY="${OAI_REGISTRY:-10.1.132.30:5000}"
OAI_IMAGE_TAG="${OAI_IMAGE_TAG:-nws-v0.8-amd64}"
OAI_SIDECAR_IMAGE_TAG="${OAI_SIDECAR_IMAGE_TAG:-nws-v0.5-amd64}"
OAI_CUCP_IMAGE="${OAI_CUCP_IMAGE:-${OAI_REGISTRY}/oai-cucp:${OAI_IMAGE_TAG}}"
OAI_DU_IMAGE="${OAI_DU_IMAGE:-${OAI_REGISTRY}/oai-du:${OAI_IMAGE_TAG}}"
OAI_CUUP_IMAGE="${OAI_CUUP_IMAGE:-${OAI_REGISTRY}/oai-nr-cuup:${OAI_IMAGE_TAG}}"
OAI_NR_UE_IMAGE="${OAI_NR_UE_IMAGE:-${OAI_REGISTRY}/oai-nr-ue:${OAI_IMAGE_TAG}}"
OAI_FLEXRIC_IMAGE="${OAI_FLEXRIC_IMAGE:-${OAI_REGISTRY}/oai-flexric:${OAI_IMAGE_TAG}}"
OAI_XAPP_IMAGE="${OAI_XAPP_IMAGE:-${OAI_REGISTRY}/nws-xapp:nws-v0.6-amd64}"
OAI_RAN_OPERATOR_IMAGE="${OAI_RAN_OPERATOR_IMAGE:-${OAI_REGISTRY}/oai-ran-controller:cpuagent-v8}"
IPERF3_N6_IMAGE="${IPERF3_N6_IMAGE:-${OAI_REGISTRY}/iperf3-n6:stalefix}"
DEBUG_IMAGE="${OAI_DEBUG_SIDECAR_IMAGE:-docker.io/nicolaka/netshoot}"
N6_DHCP_GW="${OAI_N6_DHCP_GW:-10.1.137.1}"

AMF_N2="$(oai_bench_amf_n2)"
NRF_SBI="$(oai_bench_nrf_sbi)"
CUCP_N2="$(oai_bench_cucp_n2)"
CUCP_F1C="$(oai_bench_cucp_f1c)"
CUCP_E1="$(oai_bench_cucp_e1)"
CUUP_E1="$(oai_bench_cuup_e1)"
CUUP_F1U="$(oai_bench_cuup_f1u)"
CUUP_N3="$(oai_bench_cuup_n3)"
DU_F1="$(oai_bench_du_f1)"
DU_RF="$(oai_bench_du_rf)"
UE_RF="$(oai_bench_ue_rf)"
FLEXRIC_IP="$(oai_bench_flexric)"
XAPP_E2_IP="$(oai_bench_xapp_e2)"
XAPP_SWAGGER_VIP="${OAI_XAPP_SWAGGER_VIP}"
XAPP_API_PORT="${OAI_BENCH_XAPP_API_PORT}"
UPF_N3="$(oai_bench_upf_n3)"
UPF_N4="$(oai_bench_upf_n4)"
UPF_N6_LOGICAL="$(oai_bench_upf_n6_logical)"
SMF_N4="$(oai_bench_smf_n4)"
BENCH_IMSI="${OAI_BENCH_IMSI}"
GW="$OAI_MACVLAN_GW"
NAD_PARENT="$SITE_IFACE"
USRP_IFACE="${USRP_SITE_IFACE:-enp4s0f0}"
UPF_NAME="upf-benchmark"
# Edge workers for rfsim DU + nrUE (default: physical usrp node).
DU_NODE="${OAI_BENCH_DU_NODE:-usrp}"
UE_NODE="${OAI_BENCH_UE_NODE:-${DU_NODE}}"

echo "==> Dedicated benchmark core (${BENCH_NS})"
OAI_CN_NS="$BENCH_NS" OAI_CORE_OFFSET0="${OAI_BENCH_CORE_OFFSET0:-40}" SKIP_UPF=1 \
  "$SCRIPT_DIR/render_oai_core_gitops.sh" central

CN_DIR="${REPOS_DIR}/central-repo/namespaces/${BENCH_NS}"
mkdir -p "$CN_DIR"
python3 - "$CN_DIR" "$BENCH_NS" "$AMF_N2" "$NRF_SBI" "$SMF_N4" <<'PY'
import sys
from pathlib import Path
import yaml

cn_dir, bench_ns, amf_n2, nrf_sbi, smf_n4 = sys.argv[1:6]
cn_dir = Path(cn_dir)
doc = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {
        "name": "ina-core-ips",
        "namespace": bench_ns,
        "labels": {"app.kubernetes.io/part-of": bench_ns},
    },
    "data": {
        "subnet": f"{amf_n2.rsplit('.', 1)[0]}.0/24",
        "amf_n2": amf_n2,
        "nrf_sbi": nrf_sbi,
        "smf_n4": smf_n4,
    },
}
cn_dir.mkdir(parents=True, exist_ok=True)
(cn_dir / "10-core-ips-configmap.yaml").write_text(
    yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
)
print(f"  core-ips: AMF {amf_n2} NRF {nrf_sbi} SMF {smf_n4}")
PY

# Drop legacy multi-namespace benchmark dirs from prior renders.
rm -rf "${REPOS_DIR}/central-repo/namespaces/oai-cn-benchmark" \
       "${REPOS_DIR}/edge-repo/namespaces/oai-upf-benchmark" \
       "${REPOS_DIR}/edge-repo/namespaces/oai-cn-benchmark" \
       "${REPOS_DIR}/regional-repo/namespaces/oai-cn-benchmark"

python3 - "$REPOS_DIR" <<'PY'
import sys
from pathlib import Path
import yaml

repos = Path(sys.argv[1])
cluster = repos / "central-repo" / "cluster"
legacy_ns = {"oai-cn-benchmark", "oai-upf-benchmark"}
for path in cluster.glob("clusterrolebinding-oai-*-operator-rolebinding-cluster.yaml"):
    doc = yaml.safe_load(path.read_text())
    subjects = [
        s for s in (doc.get("subjects") or [])
        if not (s.get("kind") == "ServiceAccount" and s.get("namespace") in legacy_ns)
    ]
    if subjects != doc.get("subjects"):
        doc["subjects"] = subjects
        path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))
        print(f"  CRB cleaned: {path.name}")
PY

python3 - "$REPOS_DIR" "$BENCH_NS" \
  "$OAI_CUCP_IMAGE" "$OAI_DU_IMAGE" "$OAI_CUUP_IMAGE" "$OAI_NR_UE_IMAGE" \
  "$OAI_CN_IMAGE" "$OAI_CN_TAG" "$OAI_SMF_IMAGE" "$OAI_UPF_IMAGE" \
  "$DEBUG_IMAGE" "$SCRIPT_DIR/oai_debug_sidecar.py" \
  "$AMF_N2" "$NRF_SBI" "$CUCP_N2" "$CUCP_F1C" "$CUCP_E1" \
  "$CUUP_E1" "$CUUP_F1U" "$CUUP_N3" "$DU_F1" "$DU_RF" "$UE_RF" \
  "$UPF_N3" "$UPF_N4" "$UPF_N6_LOGICAL" "$SMF_N4" "$GW" "$N6_DHCP_GW" \
  "$NAD_PARENT" "$USRP_IFACE" "$BENCH_IMSI" "$UPF_NAME" \
  "$OAI_RAN_OPERATOR_IMAGE" "$IPERF3_N6_IMAGE" \
  "$DU_NODE" "$UE_NODE" \
  "$FLEXRIC_IP" "$XAPP_E2_IP" "$XAPP_SWAGGER_VIP" "$XAPP_API_PORT" \
  "$OAI_FLEXRIC_IMAGE" "$OAI_XAPP_IMAGE" \
  <<'PY'
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

(
    repos_dir, bench_ns,
    cucp_image, du_image, cuup_image, ue_image,
    cn_image, cn_tag, smf_image, upf_image,
    debug_image, debug_lib,
    amf_n2, nrf_sbi, cucp_n2, cucp_f1c, cucp_e1,
    cuup_e1, cuup_f1u, cuup_n3, du_f1, du_rf, ue_rf,
    upf_n3, upf_n4, upf_n6_logical, smf_n4, gw, n6_dhcp_gw,
    nad_parent, usrp_iface, bench_imsi, upf_name,
    ran_op_image, iperf3_n6_image,
    du_node, ue_node,
    flexric_ip, xapp_e2_ip, xapp_swagger_vip, xapp_api_port_s,
    flexric_image, xapp_image,
) = sys.argv[1:46]
xapp_api_port = int(xapp_api_port_s)

repos = Path(repos_dir)
gw_default = gw
edge_dir = repos / "edge-repo" / "namespaces" / bench_ns
cn_dir = repos / "central-repo" / "namespaces" / bench_ns
edge_cluster = repos / "edge-repo" / "cluster"

spec = importlib.util.spec_from_file_location("oai_debug_sidecar", debug_lib)
oai_debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oai_debug)
debug_ctr = oai_debug.debug_sidecar_container(debug_image)

_WAIT_READY_FUNCS = r"""
wait_tcp() {
  label="$1"; host="$2"; port="$3"
  echo "waiting for ${label} tcp://${host}:${port}"
  until (echo >/dev/tcp/${host}/${port}) >/dev/null 2>&1 \
     || nc -z -w2 "$host" "$port" >/dev/null 2>&1; do
    sleep 2
  done
  echo "  ${label} ready"
}
wait_sctp() {
  label="$1"; host="$2"; port="$3"
  echo "waiting for ${label} sctp://${host}:${port}"
  until ncat --sctp -z -w2 "$host" "$port" >/dev/null 2>&1 \
     || nmap -sY -p "$port" --host-timeout 3s "$host" 2>/dev/null | grep -q "open"; do
    sleep 2
  done
  echo "  ${label} ready"
}
wait_ping() {
  label="$1"; host="$2"
  echo "waiting for ${label} icmp://${host}"
  until ping -c1 -W2 "$host" >/dev/null 2>&1; do
    sleep 2
  done
  echo "  ${label} ready"
}
"""


def wait_ready_init(checks, *, name: str) -> dict:
    body = "\n".join(
        [
            "set -eu",
            _WAIT_READY_FUNCS.strip(),
            f'echo "dependency wait ({name}): service ready checks"',
            *checks,
            'echo "all dependencies ready — starting main container"',
        ]
    )
    return {
        "name": name,
        "image": debug_image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["bash", "-c", body],
        "securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}},
        "resources": {
            "requests": {"cpu": "10m", "memory": "32Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
    }


def bringup_order_sidecar(role: str, steps) -> dict:
    lines = "\n".join(f"echo '  - {s}'" for s in steps)
    body = "\n".join(
        [
            "set -eu",
            f'echo "bringup-order sidecar role={role}"',
            "echo 'depends on (see ina-infra/bringup_order.md — same RAN chain):'",
            lines,
            "echo 'gated by bringup-* initContainers; sleeping'",
            "sleep infinity",
        ]
    )
    return {
        "name": "bringup-order",
        "image": debug_image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["bash", "-c", body],
        "resources": {
            "requests": {"cpu": "5m", "memory": "16Mi"},
            "limits": {"cpu": "50m", "memory": "64Mi"},
        },
    }


def softmodem_readiness() -> dict:
    return {
        "exec": {"command": ["bash", "-c", "pgrep -f softmodem >/dev/null"]},
        "initialDelaySeconds": 15,
        "periodSeconds": 5,
        "timeoutSeconds": 2,
        "failureThreshold": 24,
    }


# CU-CP must not wait on CU-UP (CU-UP waits on CU-CP E1 — would deadlock).
cucp_inits = [
    wait_ready_init([f'wait_sctp "amf/n2" "{amf_n2}" "38412"'], name="bringup-amf"),
    wait_ready_init([f'wait_ping "upf-benchmark/n3" "{upf_n3}"'], name="bringup-upf"),
]
du_inits = [
    wait_ready_init([f'wait_sctp "cu-cp/f1c" "{cucp_f1c}" "38472"'], name="bringup-cucp"),
]
ue_inits = [
    wait_ready_init([f'wait_tcp "du/rfsim" "{du_rf}" "4043"'], name="bringup-du"),
]

GNB_ID = "0xe00"
GNB_CU_ID = "0xe00"
NR_CELLID = 87654321


def dump(doc, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))


def purge_dir(directory: Path):
    if directory.is_dir():
        shutil.rmtree(directory)


def write_ns(path: Path, name: str):
    dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}, path / f"namespace-{name}.yaml")


def nad(name, ns, master, ip, *, gateway=None, routes=None):
    gw = gateway or gw_default
    cfg = {
        "cniVersion": "0.3.1",
        "name": name,
        "plugins": [
            {
                "type": "macvlan",
                "capabilities": {"ips": True},
                "master": master,
                "mode": "bridge",
                "ipam": {
                    "type": "static",
                    "addresses": [{"address": f"{ip}/24", "gateway": gw}],
                },
            },
            {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
        ],
    }
    if routes:
        cfg["plugins"][0]["ipam"]["routes"] = routes
    return {
        "apiVersion": "k8s.cni.cncf.io/v1",
        "kind": "NetworkAttachmentDefinition",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"config": json.dumps(cfg)},
    }


def nad_bare(name, ns, master):
    cfg = {
        "cniVersion": "0.3.1",
        "name": name,
        "plugins": [
            {
                "type": "macvlan",
                "capabilities": {"ips": True},
                "master": master,
                "mode": "bridge",
                "ipam": {},
            },
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
    lines = ["[\n"]
    for item in entries:
        nad_name, iface, ip = item
        lines.append(" {\n")
        lines.append(f'  "name": {json.dumps(nad_name)},\n')
        lines.append(f'  "interface": {json.dumps(iface)},\n')
        lines.append(f'  "ips": [{json.dumps(f"{ip}/24")}],\n')
        lines.append(f'  "gateways": [{json.dumps(gw_default)}]\n')
        lines.append(" }")
        if item != entries[-1]:
            lines.append(",")
        lines.append("\n")
    lines.append("]")
    return "".join(lines)


def edge_node_affinity(hosts=("edge-0", "edge-1")):
    return {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [{
                    "matchExpressions": [{
                        "key": "kubernetes.io/hostname",
                        "operator": "In",
                        "values": list(hosts),
                    }],
                }],
            },
        },
    }


N6_DHCP_INIT = """set -eu
echo 'waiting for Multus n6'
for i in $(seq 1 60); do ip link show n6 >/dev/null 2>&1 && break; sleep 1; done
ip link show n6 >/dev/null 2>&1 || { echo 'n6 iface missing' >&2; exit 1; }
ip link set n6 up || true
ip -4 addr flush dev n6 || true
if command -v dhclient >/dev/null 2>&1; then
  dhclient -v -1 -lf /tmp/dhclient.n6.leases n6
elif command -v udhcpc >/dev/null 2>&1; then
  udhcpc -i n6 -q -n -f
else
  echo 'no dhclient/udhcpc in image' >&2; exit 1
fi
for i in $(seq 1 30); do
  ip -4 -o addr show n6 | grep -q 'inet ' && break
  sleep 1
done
ip -4 -o addr show n6 | grep -q 'inet ' || { echo 'n6 DHCP lease failed' >&2; exit 1; }
gw=$(ip -4 route show default dev n6 2>/dev/null | awk '{print $3; exit}')
gw=${gw:-10.1.137.1}
ip route show default | while read -r line; do
  case "$line" in *'dev eth0'*) ip route del $line 2>/dev/null || true ;; esac
done
ip route replace default via "$gw" dev n6 metric 50
ip route replace 10.244.0.0/16 via 10.244.1.1 dev eth0 2>/dev/null || true
# Keep ClusterIP / Influx reachable via flannel eth0 (N6 is default).
flannel_gw=$(ip -4 route show 10.244.0.0/16 | awk '{print $3; exit}')
flannel_gw=${flannel_gw:-10.244.1.1}
ip route replace 10.96.0.0/12 via "$flannel_gw" dev eth0 2>/dev/null || true
exit 0
"""


def purge_operator_artifacts(directory: Path) -> None:
    if not directory.is_dir():
        return
    for pat in ("70-*", "*nfdeployment*", "*nfconfig*", "25-config-smf-core-upf-*"):
        for path in directory.glob(pat):
            path.unlink()


# PLACEHOLDER_EXECUTORS

def kubectl_get_cm(name: str, src_ns: str = "oai-cn") -> dict | None:
    try:
        raw = subprocess.check_output(
            ["kubectl", "--context", "central@central", "-n", src_ns, "get", "cm", name, "-o", "yaml"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    doc = yaml.safe_load(raw)
    meta = doc.setdefault("metadata", {})
    for key in ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields", "ownerReferences"):
        meta.pop(key, None)
    ann = meta.get("annotations") or {}
    ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if ann:
        meta["annotations"] = ann
    else:
        meta.pop("annotations", None)
    labels = meta.get("labels") or {}
    for key in list(labels):
        if key.startswith(("configmanagement.gke.io/", "configsync.gke.io/")):
            labels.pop(key)
        elif key == "app.kubernetes.io/managed-by" and labels.get(key) == "configmanagement.gke.io":
            labels.pop(key)
    if labels:
        meta["labels"] = labels
    else:
        meta.pop("labels", None)
    return doc


def copy_shared_cm(name: str, dest_ns: str, dest: Path) -> bool:
    doc = kubectl_get_cm(name)
    if not doc:
        print(f"  WARN: no source ConfigMap {name} in oai-cn — skip")
        return False
    doc["metadata"]["namespace"] = dest_ns
    dump(doc, dest)
    return True


def oai_service(svc_name: str, nf_label: str, dest_ns: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": svc_name, "namespace": dest_ns},
        "spec": {
            "type": "ClusterIP",
            "selector": {"workload.nephio.org/oai": nf_label},
            "ports": [{"name": "sbi", "port": 80, "targetPort": 80, "protocol": "TCP"}],
        },
    }


def oai_sa(name: str, dest_ns: str) -> dict:
    return {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": name, "namespace": dest_ns}}


def wait_nrf_init() -> dict:
    return {
        "name": "init",
        "image": "docker.io/alpine/curl:3.14",
        "imagePullPolicy": "IfNotPresent",
        "command": [
            "sh", "-c",
            "until curl --connect-timeout 1 --head -X GET "
            "http://oai-nrf/nnrf-nfm/v1/nf-instances?nf-type=NRF --http2-prior-knowledge; "
            "do echo waiting for nrf; sleep 1; done",
        ],
    }


def cn_executor_deployment(
    name, nf_label, image, binary, config_cm, ports, *, networks=None, init_containers=None,
) -> dict:
    pod_meta = {"labels": {"workload.nephio.org/oai": nf_label}}
    if networks:
        pod_meta["annotations"] = {"k8s.v1.cni.cncf.io/networks": networks}
    spec = {
        "serviceAccountName": name,
        "nodeSelector": {"kubernetes.io/hostname": "central-0"},
        "securityContext": {"runAsUser": 0, "runAsGroup": 0},
        "terminationGracePeriodSeconds": 5,
        "containers": [{
            "name": name,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": [binary, "-c", f"/openair-{nf_label}/etc/{nf_label}.yaml", "-o"],
            "ports": ports,
            "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "100m", "memory": "512Mi"}},
            "volumeMounts": [{"name": "configuration", "mountPath": f"/openair-{nf_label}/etc"}],
        }],
        "volumes": [{"name": "configuration", "configMap": {"name": config_cm}}],
    }
    if init_containers:
        spec["initContainers"] = init_containers
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": bench_ns, "labels": {"workload.nephio.org/oai": nf_label}},
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"workload.nephio.org/oai": nf_label}},
            "template": {"metadata": pod_meta, "spec": spec},
        },
    }


def patch_smf_core_configmap(cn_dir: Path) -> None:
    doc = {
        "log_level": {"general": "info"},
        "register_nf": {"general": True},
        "http_version": 2,
        # Controllable NS slice (xApp cannot SET default 0xffffff over E2).
        "snssais": [{"sst": 1, "sd": "000001"}],
        "nfs": {
            "amf": {"host": "oai-amf", "sbi": {"port": 80, "api_version": "v1"}},
            "smf": {
                "host": "oai-smf",
                "sbi": {"port": 80, "api_version": "v1", "interface_name": "eth0"},
                "n4": {"interface_name": "n4", "port": 8805},
            },
            "udm": {"host": "oai-udm", "sbi": {"port": 80, "api_version": "v1"}},
            "nrf": {"host": "oai-nrf", "sbi": {"port": 80, "api_version": "v1"}},
        },
        "dnns": [{"dnn": "internet", "pdu_session_type": "IPV4", "ipv4_subnet": "10.1.0.0/24"}],
    }
    smf = doc.setdefault("smf", {})
    smf.setdefault("support_features", {}).update({
        "discover_upf": "no",
        "use_local_subscription_info": "yes",
    })
    slice0 = {"sst": 1, "sd": "000001"}
    smf.setdefault("smf_info", {})["sNssaiSmfInfoList"] = [
        {"sNssai": slice0, "dnnSmfInfoList": [{"dnn": "internet"}]},
    ]
    smf["local_subscription_infos"] = [{
        "single_nssai": slice0,
        "dnn": "internet",
        "qos_profile": {"5qi": 5, "session_ambr_ul": "1000Mbps", "session_ambr_dl": "1000Mbps"},
    }]
    smf["upfs"] = [{
        "host": upf_n4,
        "port": 8805,
        "config": {"enable_usage_reporting": "no", "enable_upf_wo_nf_discovery": "yes", "n3_local_ipv4": upf_n3},
        "upf_info": {
            "interfaceUpfInfoList": [
                {"interfaceType": "N3", "networkInstance": "access.oai.org"},
                {"interfaceType": "N6", "networkInstance": "core.oai.org"},
            ],
            "sNssaiUpfInfoList": [
                {"sNssai": slice0, "dnnUpfInfoList": [{"dnn": "internet"}]},
            ],
        },
    }]
    dump({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "smf-core", "namespace": bench_ns, "annotations": {"oai-benchmark/rendered": "executor"}},
        "data": {"smf.yaml": yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)},
    }, cn_dir / "26-configmap-smf-core.yaml")


def render_core_executors() -> None:
    purge_operator_artifacts(cn_dir)
    prefix = "30-executor-"
    for cm in ("nrf-core", "amf-core", "udm-core", "udr-core", "ausf-core"):
        copy_shared_cm(cm, bench_ns, cn_dir / f"{prefix}configmap-{cm}.yaml")
    patch_smf_core_configmap(cn_dir)
    for svc_name, label, sa in (
        ("oai-nrf", "nrf", "nrf-core"),
        ("oai-amf", "amf", "amf-core"),
        ("oai-smf", "smf", "smf-core"),
        ("oai-udm", "udm", "udm-core"),
        ("oai-udr", "udr", "udr-core"),
        ("oai-ausf", "ausf", "ausf-core"),
    ):
        dump(oai_service(svc_name, label, bench_ns), cn_dir / f"{prefix}service-{svc_name}.yaml")
        dump(oai_sa(sa, bench_ns), cn_dir / f"{prefix}serviceaccount-{sa}.yaml")
    amf_net = json.dumps([{"name": "amf-core-n2", "interface": "n2", "ips": [f"{amf_n2}/24"], "gateway": [gw]}])
    smf_net = json.dumps([{"name": "smf-core-n4", "interface": "n4", "ips": [f"{smf_n4}/24"], "gateway": [gw]}])
    for name, label, image, binary, ports, networks, inits in (
        ("nrf-core", "nrf", f"{cn_image}/oai-nrf:{cn_tag}", "/openair-nrf/bin/oai_nrf", [], None, None),
        ("amf-core", "amf", f"{cn_image}/oai-amf:{cn_tag}", "/openair-amf/bin/oai_amf", [
            {"containerPort": 38412, "name": "n2", "protocol": "SCTP"},
            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
        ], amf_net, [wait_nrf_init()]),
        ("smf-core", "smf", smf_image, "/openair-smf/bin/oai_smf", [
            {"containerPort": 8805, "name": "n4", "protocol": "UDP"},
            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
        ], smf_net, [wait_nrf_init(), wait_ready_init([f'wait_ping "upf-benchmark/n4" "{upf_n4}"'], name="bringup-upf")]),
        ("udm-core", "udm", f"{cn_image}/oai-udm:{cn_tag}", "/openair-udm/bin/oai_udm", [
            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
        ], None, [wait_nrf_init()]),
        ("udr-core", "udr", f"{cn_image}/oai-udr:{cn_tag}", "/openair-udr/bin/oai_udr", [
            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
        ], None, [wait_nrf_init()]),
        ("ausf-core", "ausf", f"{cn_image}/oai-ausf:{cn_tag}", "/openair-ausf/bin/oai_ausf", [
            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
        ], None, [wait_nrf_init()]),
    ):
        dump(cn_executor_deployment(name, label, image, binary, name, ports, networks=networks, init_containers=inits),
             cn_dir / f"{prefix}deployment-{name}.yaml")
    print(f"  core executors → {bench_ns} (no operators)")


def upf_config_yaml() -> str:
    return f"""log_level:
  upf: debug
register_nf:
  upf: 'yes'
http_version: 2
http_request_timeout: 3000
snssais:
  - sst: 1
    sd: "000001"
nfs:
  upf:
    host: oai-upf
    sbi:
      port: 80
      api_version: v1
      interface_name: n4
    n3:
      interface_name: n3
      port: 2152
    n4:
      interface_name: n4
      port: 8805
    n6:
      interface_name: n6
  smf:
    host: {smf_n4}
    n4:
      interface_name: n4
      port: 8805
  nrf:
    host: {nrf_sbi}
    sbi:
      port: 80
upf:
  support_features:
    enable_bpf_datapath: no
    enable_snat: yes
  remote_n6_gw: {n6_dhcp_gw}
  upf_info:
    sNssaiUpfInfoList:
      - sNssai:
          sst: 1
          sd: "000001"
        dnnUpfInfoList:
          - dnn: internet
dnns:
  - dnn: internet
    pdu_session_type: IPV4
    ipv4_subnet: 10.1.0.0/24
"""


def render_upf_executor() -> None:
    legacy = repos / "edge-repo" / "namespaces" / "oai-upf"
    if legacy.is_dir():
        for old in legacy.glob(f"*{upf_name}*"):
            old.unlink()
    for suffix, ip in (("n3", upf_n3), ("n4", upf_n4)):
        dump(nad(f"{upf_name}-{suffix}", bench_ns, nad_parent, ip),
             edge_dir / f"40-networkattachmentdefinition-{upf_name}-{suffix}.yaml")
    dump(nad_bare(f"{upf_name}-n6", bench_ns, nad_parent),
         edge_dir / f"40-networkattachmentdefinition-{upf_name}-n6.yaml")
    dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": upf_name, "namespace": bench_ns},
          "data": {"upf.yaml": upf_config_yaml()}}, edge_dir / f"40-configmap-{upf_name}.yaml")
    dump(oai_sa(upf_name, bench_ns), edge_dir / f"40-serviceaccount-{upf_name}.yaml")
    net_ann = json.dumps([
        {"name": f"{upf_name}-n3", "interface": "n3", "ips": [f"{upf_n3}/24"], "gateway": [gw]},
        {"name": f"{upf_name}-n4", "interface": "n4", "ips": [f"{upf_n4}/24"], "gateway": [gw]},
        {"name": f"{upf_name}-n6", "interface": "n6"},
    ])
    dump({
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": upf_name, "namespace": bench_ns,
            "labels": {"workload.nephio.org/oai": "upf", "app.kubernetes.io/part-of": bench_ns},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"workload.nephio.org/oai": "upf", "app.kubernetes.io/name": upf_name}},
            "template": {
                "metadata": {
                    "labels": {"workload.nephio.org/oai": "upf", "app.kubernetes.io/name": upf_name},
                    "annotations": {"k8s.v1.cni.cncf.io/networks": net_ann},
                },
                "spec": {
                    "serviceAccountName": upf_name,
                    "affinity": edge_node_affinity(),
                    "securityContext": {"runAsUser": 0, "runAsGroup": 0},
                    "initContainers": [{
                        "name": "n6-dhcp",
                        "image": debug_image,
                        "imagePullPolicy": "IfNotPresent",
                        "securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}},
                        "command": ["sh", "-c", N6_DHCP_INIT],
                    }],
                    "containers": [{
                        "name": upf_name,
                        "image": upf_image,
                        "imagePullPolicy": "IfNotPresent",
                        "securityContext": {"privileged": True},
                        "command": ["/openair-upf/bin/oai_upf", "-c", "/openair-upf/etc/upf.yaml", "-o"],
                        "ports": [
                            {"containerPort": 2152, "name": "n3n9", "protocol": "UDP"},
                            {"containerPort": 8805, "name": "n4", "protocol": "UDP"},
                            {"containerPort": 80, "name": "sbi", "protocol": "TCP"},
                        ],
                        "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "100m", "memory": "512Mi"}},
                        "volumeMounts": [{"name": "configuration", "mountPath": "/openair-upf/etc"}],
                    }, {
                        # Share UPF netns → bind iperf3 to Multus N3 address.
                        "name": "iperf3-n6",
                        "image": iperf3_n6_image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python3", "server.py"],
                        "env": [
                            {"name": "BIND_IFACE", "value": "n3"},
                            {"name": "N6_IFACE", "value": "n3"},
                            {"name": "PORT_START", "value": "5201"},
                            {"name": "PORT_COUNT", "value": "126"},
                            {"name": "IDLE_TIMEOUT", "value": "30"},
                            {"name": "REPORT_INTERVAL", "value": "1"},
                            {"name": "TESTBED", "value": bench_ns},
                            {"name": "INFLUX_URL", "value": "http://influxdb.influxdb.svc.cluster.local:8086"},
                            {"name": "INFLUX_TOKEN", "value": "ina-infra-influxdb-token"},
                            {"name": "INFLUX_ORG", "value": "ina-infra"},
                            {"name": "INFLUX_BUCKET", "value": "default"},
                        ],
                        "resources": {
                            "requests": {"cpu": "50m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "256Mi"},
                        },
                    }, debug_ctr],
                    "volumes": [{"name": "configuration", "configMap": {"name": upf_name}}],
                },
            },
        },
    }, edge_dir / f"40-deployment-{upf_name}.yaml")
    print(f"  UPF executor {upf_name} @ {bench_ns} (N6 DHCP + iperf3-n6 sidecar {iperf3_n6_image})")


render_core_executors()

# ---------------------------------------------------------------------------
# Edge RAN via oai-ran-controller (NFDeployment) + static UE/UPF executors
# ---------------------------------------------------------------------------
purge_dir(edge_dir)
edge_dir.mkdir(parents=True)
write_ns(edge_dir, bench_ns)

# Operator RBAC (cluster-scoped) + Deployment in oai-benchmark
dump(
    {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {"name": "oai-ran-operator-cluster-role"},
        "rules": [
            {"apiGroups": [""], "resources": ["configmaps", "services", "serviceaccounts"],
             "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"]},
            {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch"]},
            {"apiGroups": [""], "resources": ["pods"],
             "verbs": ["get", "list", "watch", "patch", "update"]},
            {"apiGroups": ["apps"], "resources": ["deployments"],
             "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"]},
            {"apiGroups": ["apps"], "resources": ["deployments/status"], "verbs": ["get"]},
            {"apiGroups": ["k8s.cni.cncf.io"], "resources": ["network-attachment-definitions"],
             "verbs": ["get", "list", "watch", "create"]},
            {"apiGroups": ["workload.nephio.org"],
             "resources": ["nfdeployments", "nfdeployments/status", "nfdeployments/finalizers"],
             "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"]},
            {"apiGroups": ["workload.nephio.org"], "resources": ["nfconfigs"],
             "verbs": ["get", "list", "watch"]},
            {"apiGroups": ["ref.nephio.org"], "resources": ["configs"],
             "verbs": ["get", "list", "watch"]},
        ],
    },
    edge_cluster / "clusterrole-oai-ran-operator-cluster-role.yaml",
)

crb_path = edge_cluster / "clusterrolebinding-oai-ran-operator-rolebinding-cluster.yaml"
subject = {"kind": "ServiceAccount", "name": "oai-ran-operator", "namespace": bench_ns}
if crb_path.is_file():
    crb = yaml.safe_load(crb_path.read_text())
    subjects = crb.setdefault("subjects", [])
    if subject not in subjects:
        subjects.append(subject)
    dump(crb, crb_path)
else:
    dump(
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "oai-ran-operator-rolebinding-cluster"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "oai-ran-operator-cluster-role",
            },
            "subjects": [subject],
        },
        crb_path,
    )

dump(
    {"apiVersion": "v1", "kind": "ServiceAccount",
     "metadata": {"name": "oai-ran-operator", "namespace": bench_ns}},
    edge_dir / "05-serviceaccount-oai-ran-operator.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "oai-ran-operator", "namespace": bench_ns},
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
                    "serviceAccountName": "oai-ran-operator",
                    "affinity": edge_node_affinity(),
                    "containers": [{
                        "name": "operator",
                        "image": ran_op_image,
                        "imagePullPolicy": "IfNotPresent",
                        "env": [
                            {"name": "INA_INFRA_API_URL",
                             "value": os.environ.get("INA_INFRA_API_URL", "http://10.1.132.200:8082")},
                            {"name": "INA_OPERATOR_CLUSTER", "value": "edge"},
                            {"name": "INA_OPERATOR_NAMESPACE", "value": bench_ns},
                            {"name": "INA_OPERATOR_ID",
                             "value": os.environ.get("INA_OPERATOR_ID", f"edge-{bench_ns}")},
                            {"name": "INA_OPERATOR_POLL_SEC",
                             "value": os.environ.get("INA_OPERATOR_POLL_SEC", "5")},
                            # Create-once RAN Deployments read these for nodeSelector
                            # (avoids scheduling onto edge-2 / wrong Multus parent).
                            {"name": "OAI_RAN_CU_MULTUS_MASTER", "value": nad_parent},
                            {"name": "OAI_RAN_DU_MULTUS_MASTER", "value": usrp_iface},
                            {"name": "OAI_RAN_DU_NODE", "value": du_node},
                        ],
                        "resources": {
                            "limits": {"cpu": "500m", "memory": "128Mi"},
                            "requests": {"cpu": "10m", "memory": "64Mi"},
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }],
                },
            },
        },
    },
    edge_dir / "05-deployment-oai-ran-operator.yaml",
)

for nad_name, ip in (
    ("cucp-bench-n2", cucp_n2),
    ("cucp-bench-f1c", cucp_f1c),
    ("cucp-bench-e1", cucp_e1),
):
    dump(nad(nad_name, bench_ns, nad_parent, ip),
         edge_dir / f"12-networkattachmentdefinition-{nad_name}.yaml")

for nad_name, ip in (
    ("cuup-bench-e1", cuup_e1),
    ("cuup-bench-f1u", cuup_f1u),
    ("cuup-bench-n3", cuup_n3),
):
    dump(nad(nad_name, bench_ns, nad_parent, ip),
         edge_dir / f"30-networkattachmentdefinition-{nad_name}.yaml")

dump(nad("du-bench-f1", bench_ns, usrp_iface, du_f1),
     edge_dir / "12-networkattachmentdefinition-du-bench-f1.yaml")
dump(nad("du-bench-rf", bench_ns, usrp_iface, du_rf),
     edge_dir / "12-networkattachmentdefinition-du-bench-rf.yaml")
dump(nad("ue-bench-sim-rf", bench_ns, usrp_iface, ue_rf),
     edge_dir / "50-networkattachmentdefinition-ue-bench-sim-rf.yaml")

# Lab RF profile (same as oai-slice-deployment / nws 133 PRB band78 rfsim).
du_conf = f"""Active_gNBs = ( "oai-du");
Asn1_verbosity = "none";
gNBs =
(
 {{
    gNB_ID = {GNB_ID};
    gNB_DU_ID = {GNB_ID};
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
    tracking_area_code  =  81;
    uess_agg_levels = [8, 8, 8, 5, 2];
    coreset_duration = 2;
    plmn_list = ({{ mcc = 001; mnc = 01; mnc_length = 2; snssaiList = ({{ sst = 1, sd = 0xFFFFFF }}, {{ sst = 1, sd = 0x000001 }}) }});
    nr_cellid = {NR_CELLID};
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
        SCTP_OUTSTREAMS  = 2;
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
  {{ slice_id = 1; sst = 1; sd = 0x000001; dedicated_prb_ratio = 0.0; min_prb_ratio = 0.0; max_prb_ratio = 100.0; }}
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
        "metadata": {"name": "oai-du-configmap", "namespace": bench_ns},
        "data": {"gnb.conf": du_conf},
    },
    edge_dir / "13-configmap-oai-du-configmap.yaml",
)




def iface(name, ip, vlan=None):
    doc = {"name": name, "ipv4": {"address": f"{ip}/24", "gateway": gw}}
    if vlan is not None:
        doc["vlanID"] = vlan
    return doc


def nfconfig(name, image):
    return {
        "apiVersion": "workload.nephio.org/v1alpha1",
        "kind": "NFConfig",
        "metadata": {"name": name, "namespace": bench_ns},
        "spec": {
            "configRefs": [
                {
                    "apiVersion": "workload.nephio.org/v1alpha1",
                    "kind": "RANConfig",
                    "metadata": {"name": f"{name}-ran", "namespace": bench_ns},
                    "spec": {
                        "cellIdentity": str(NR_CELLID),
                        "physicalCellID": 0,
                        "downlinkFrequencyBand": 78,
                        "downlinkSubCarrierSpacing": 1,
                        "downlinkCarrierBandwidth": 133,
                        "uplinkFrequencyBand": 78,
                        "uplinkSubCarrierSpacing": 1,
                        "uplinkCarrierBandwidth": 133,
                    },
                },
                {
                    "apiVersion": "workload.nephio.org/v1alpha1",
                    "kind": "PLMN",
                    "metadata": {"name": f"{name}-plmn", "namespace": bench_ns},
                    "spec": {
                        "PLMNInfo": [{
                            "plmnID": {"mcc": "001", "mnc": "01"},
                            "tac": 81,
                            "nssai": [
                                {"sst": 1, "sd": "FFFFFF"},
                                {"sst": 1, "sd": "000001"},
                            ],
                        }],
                    },
                },
                {
                    "apiVersion": "workload.nephio.org/v1alpha1",
                    "kind": "OAIConfig",
                    "metadata": {"name": f"{name}-oai", "namespace": bench_ns},
                    "spec": {"image": image},
                },
            ],
        },
    }


def ref_config(name, embedded_nf):
    return {
        "apiVersion": "ref.nephio.org/v1alpha1",
        "kind": "Config",
        "metadata": {"name": name, "namespace": bench_ns},
        "spec": {"config": embedded_nf},
    }


def nfdeployment(name, provider, interfaces, network_instances, param_refs, annotations=None):
    meta = {"name": name, "namespace": bench_ns}
    if annotations:
        meta["annotations"] = annotations
    return {
        "apiVersion": "workload.nephio.org/v1alpha1",
        "kind": "NFDeployment",
        "metadata": meta,
        "spec": {
            "provider": provider,
            "interfaces": interfaces,
            "networkInstances": network_instances,
            "parametersRefs": param_refs,
        },
    }


# Hints for humans / post-create patch (create-once does not consume these).
bench_vpc_annotation = {"ina-infra.nephio.lab/multus-master": nad_parent}
du_vpc_annotation = {
    "ina-infra.nephio.lab/multus-master": usrp_iface,
    "ina-infra.nephio.lab/node": du_node,
}


amf_embedded = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": "amf-core", "namespace": bench_ns},
    "spec": {
        "provider": "amf.openairinterface.org",
        "interfaces": [iface("n2", amf_n2, 4)],
    },
}
cucp_embedded = {
    "apiVersion": "workload.nephio.org/v1alpha1",
    "kind": "NFDeployment",
    "metadata": {"name": "cucp-bench", "namespace": bench_ns},
    "spec": {
        "provider": "cucp.openairinterface.org",
        "interfaces": [
            iface("n2", cucp_n2, 4),
            iface("f1c", cucp_f1c, 5),
            iface("e1", cucp_e1, 6),
        ],
    },
}

dump(nfconfig("cucp-bench-config", cucp_image), edge_dir / "10-nfconfig-cucp-bench-config.yaml")
dump(nfconfig("cuup-bench-config", cuup_image), edge_dir / "10-nfconfig-cuup-bench-config.yaml")
dump(nfconfig("du-bench-config", du_image), edge_dir / "10-nfconfig-du-bench-config.yaml")

dump(ref_config("cucp-bench-amf", amf_embedded), edge_dir / "11-config-cucp-bench-amf.yaml")
dump(ref_config("cuup-bench-cucp", cucp_embedded), edge_dir / "11-config-cuup-bench-cucp.yaml")
dump(ref_config("du-bench-cucp", cucp_embedded), edge_dir / "11-config-du-bench-cucp.yaml")

dump(
    nfdeployment(
        "cucp-bench",
        "cucp.openairinterface.org",
        [iface("n2", cucp_n2, 4), iface("f1c", cucp_f1c, 5), iface("e1", cucp_e1, 6)],
        [
            {"name": "vpc-ran", "interfaces": ["n2"]},
            {"name": "vpc-cudu-f1", "interfaces": ["f1c"]},
            {"name": "vpc-cu-e1", "interfaces": ["e1"]},
        ],
        [
            {"name": "cucp-bench-config", "apiVersion": "workload.nephio.org/v1alpha1", "kind": "NFConfig"},
            {"name": "cucp-bench-amf", "apiVersion": "ref.nephio.org/v1alpha1", "kind": "Config"},
        ],
        annotations=bench_vpc_annotation,
    ),
    edge_dir / "20-nfdeployment-cucp-bench.yaml",
)
dump(
    nfdeployment(
        "cuup-bench",
        "cuup.openairinterface.org",
        [iface("e1", cuup_e1, 6), iface("f1u", cuup_f1u, 5), iface("n3", cuup_n3, 4)],
        [
            {"name": "vpc-cu-e1", "interfaces": ["e1"]},
            {"name": "vpc-cudu-f1", "interfaces": ["f1u"]},
            {"name": "vpc-ran", "interfaces": ["n3"]},
        ],
        [
            {"name": "cuup-bench-config", "apiVersion": "workload.nephio.org/v1alpha1", "kind": "NFConfig"},
            {"name": "cuup-bench-cucp", "apiVersion": "ref.nephio.org/v1alpha1", "kind": "Config"},
        ],
        annotations=bench_vpc_annotation,
    ),
    edge_dir / "20-nfdeployment-cuup-bench.yaml",
)
dump(
    nfdeployment(
        "du-bench",
        "du.openairinterface.org",
        [iface("f1", du_f1, 5), iface("rf", du_rf)],
        [{"name": "vpc-cudu-f1", "interfaces": ["f1"]}],
        [
            {"name": "du-bench-config", "apiVersion": "workload.nephio.org/v1alpha1", "kind": "NFConfig"},
            {"name": "du-bench-cucp", "apiVersion": "ref.nephio.org/v1alpha1", "kind": "Config"},
        ],
        annotations=du_vpc_annotation,
    ),
    edge_dir / "20-nfdeployment-du-bench.yaml",
)

print(f"  RAN operator {ran_op_image} + NFDeployments cucp/cuup/du-bench (create-once executors)")
print(f"  Post-create pin: OAI_BENCH_DU_NODE={du_node} ./scripts/patch_oai_benchmark_ran_vpc.sh")

# UE stays a static executor (nrUE is not managed by oai-ran-controller)
ue_conf = f"""uicc0 = {{
  imsi = "{bench_imsi}";
  key = "fec86ba6eb707ed08905757b1bb44b8f";
  opc = "C42449363BBAD02B66D16BC975D77CC1";
  dnn = "internet";
  nssai_sst = 1;
  nssai_sd = 0x000001;
}}

thread-pool = "-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1";

rfsimulator = {{
  serveraddr = "{du_rf}";
}}

log_config = {{
  global_log_options = "level,nocolor,time";
}}
"""

dump(
    {"apiVersion": "v1", "kind": "ConfigMap",
     "metadata": {"name": "oai-ue-configmap", "namespace": bench_ns},
     "data": {"ue.conf": ue_conf}},
    edge_dir / "51-configmap-oai-ue-configmap.yaml",
)
dump(
    {"apiVersion": "v1", "kind": "ServiceAccount",
     "metadata": {"name": "oai-ue-sa", "namespace": bench_ns}},
    edge_dir / "52-serviceaccount-oai-ue-sa.yaml",
)
# Static UPF N3 target for UE iperf3-client (avoid waiting on live sync).
dump(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "iperf3-n6-endpoint",
            "namespace": bench_ns,
            "labels": {"app.kubernetes.io/part-of": bench_ns},
        },
        "data": {
            "server_ip": upf_n3,
            "n6_server_ip": upf_n3,
        },
    },
    edge_dir / "53-configmap-iperf3-n6-endpoint.yaml",
)
dump(
    {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "oai-ue",
            "namespace": bench_ns,
            "labels": {"app.kubernetes.io/name": "oai-ue"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-ue"}},
            "template": {
                "metadata": {
                    "labels": {"app": "oai-ue", "app.kubernetes.io/name": "oai-ue"},
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [("ue-bench-sim-rf", "rf", ue_rf)]
                        )
                    },
                },
                "spec": {
                    "serviceAccountName": "oai-ue-sa",
                    "terminationGracePeriodSeconds": 5,
                    "nodeSelector": {"kubernetes.io/hostname": ue_node},
                    "initContainers": ue_inits,
                    "containers": [
                        bringup_order_sidecar("ue", ["DU rfsim TCP :4043"]),
                        {
                            "name": "ue",
                            "image": ue_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {"privileged": True},
                            "env": [
                                {
                                    "name": "USE_ADDITIONAL_OPTIONS",
                                    "value": (
                                        # Match lab DU: 133 PRB / ARFCN 620640 (oai-slice-deployment).
                                        f"-r 133 --numerology 1 -C 3325620000 --ssb 144 "
                                        f"--rfsim --log_config.global_log_options level,nocolor,time "
                                        f"--rfsimulator.serveraddr {du_rf}"
                                    ),
                                },
                                {"name": "TZ", "value": "Asia/Singapore"},
                            ],
                            "volumeMounts": [{
                                "name": "configuration",
                                "mountPath": "/opt/oai-nr-ue/etc/nr-ue.conf",
                                "subPath": "ue.conf",
                            }],
                        },
                        {
                            # Auto-starts after PDU (oaitun_ue1). IPERF_SERVER / ConfigMap
                            # seed UPF N3; refresh live with ./scripts/sync_iperf3_n6_server_ip.sh.
                            "name": "iperf3-client",
                            "image": iperf3_n6_image,
                            "imagePullPolicy": "Always",
                            "command": ["/bin/sh", "client_entrypoint.sh"],
                            "env": [
                                {"name": "PORT_START", "value": "5210"},
                                {"name": "PROCESSES", "value": "5"},
                                {"name": "BANDWIDTH", "value": "50M"},
                                {"name": "REPORT_INTERVAL", "value": "1"},
                                {"name": "LOG_INTERVAL", "value": "5"},
                                {"name": "BIND_DEV", "value": "oaitun_ue1"},
                                {"name": "TESTBED", "value": bench_ns},
                                {"name": "IPERF_SERVER", "value": upf_n3},
                                {"name": "INFLUX_URL", "value": "http://influxdb.influxdb.svc.cluster.local:8086"},
                                {"name": "INFLUX_TOKEN", "value": "ina-infra-influxdb-token"},
                                {"name": "INFLUX_ORG", "value": "ina-infra"},
                                {"name": "INFLUX_BUCKET", "value": "default"},
                            ],
                            "volumeMounts": [{
                                "name": "iperf3-n6-endpoint",
                                "mountPath": "/config",
                                "readOnly": True,
                            }],
                            "resources": {
                                "requests": {"cpu": "20m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                        },
                        debug_ctr,
                    ],
                    "volumes": [{
                        "name": "configuration",
                        "configMap": {"name": "oai-ue-configmap"},
                    }, {
                        "name": "iperf3-n6-endpoint",
                        "configMap": {
                            "name": "iperf3-n6-endpoint",
                            "optional": True,
                            "items": [
                                {"key": "server_ip", "path": "server_ip"},
                                {"key": "n6_server_ip", "path": "n6_server_ip"},
                            ],
                        },
                    }],
                },
            },
        },
    },
    edge_dir / "55-deployment-oai-ue.yaml",
)


# FlexRIC near-RT RIC + nws-xapp (E2 on macvlan; Swagger via edge mgmt VIP :18081)
flexric_conf = f"""[NEAR-RIC]
NEAR_RIC_IP = {flexric_ip}

[XAPP]
DB_DIR = /tmp/
DB_NAME = xapp_db
"""
dump(
    nad("flexric-bench-e2", bench_ns, nad_parent, flexric_ip),
    edge_dir / "40-networkattachmentdefinition-flexric-bench-e2.yaml",
)
dump(
    nad("xapp-bench-e2", bench_ns, nad_parent, xapp_e2_ip),
    edge_dir / "40-networkattachmentdefinition-xapp-bench-e2.yaml",
)
dump(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "oai-flexric-configmap", "namespace": bench_ns},
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
            "namespace": bench_ns,
            "labels": {"app.kubernetes.io/name": "oai-flexric"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-flexric"}},
            "template": {
                "metadata": {
                    "labels": {
                        "app": "oai-flexric",
                        "app.kubernetes.io/name": "oai-flexric",
                    },
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [("flexric-bench-e2", "e2", flexric_ip)]
                        )
                    },
                },
                "spec": {
                    "terminationGracePeriodSeconds": 5,
                    # Opposite node from xApp (macvlan cannot hairpin).
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
            "namespace": bench_ns,
            "labels": {"app.kubernetes.io/name": "nws-xapp"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": "nws-xapp"}},
            "template": {
                "metadata": {
                    "labels": {
                        "app": "nws-xapp",
                        "app.kubernetes.io/name": "nws-xapp",
                    },
                    "annotations": {
                        "k8s.v1.cni.cncf.io/networks": networks_annot(
                            [("xapp-bench-e2", "e2", xapp_e2_ip)]
                        )
                    },
                },
                "spec": {
                    "terminationGracePeriodSeconds": 5,
                    "nodeSelector": {"kubernetes.io/hostname": "edge-0"},
                    "containers": [
                        {
                            "name": "xapp",
                            "image": xapp_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {
                                "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}
                            },
                            "command": [
                                "bash",
                                "-lc",
                                (
                                    "set -e; "
                                    'echo "waiting 30s for DU E2 attach to ${NEAR_RIC_IP}"; '
                                    "sleep 30; "
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
            "namespace": bench_ns,
            "labels": {"app.kubernetes.io/name": "nws-xapp"},
        },
        "spec": {
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


render_upf_executor()

# Bench UE uses SST=1 / SD=0x000001 (xApp-controllable). Patch MySQL
# SessionManagementSubscriptionData so UDM serves that NSSAI (decimal sd=1).
import re
mysql_cm = cn_dir / "05-configmap-mysql-initialization.yaml"
if mysql_cm.is_file():
    doc = yaml.safe_load(mysql_cm.read_text())
    key = next(iter(doc["data"]))
    sql = doc["data"][key]
    sd_dec = "1"  # 0x000001
    pattern = (
        rf"\('{re.escape(bench_imsi)}',\s*'00101',\s*'"
        r'\{\\"sst\\": 1, \\"sd\\": \\"[0-9]+\\"\}'
        r"'"
    )
    repl = (
        f"('{bench_imsi}', '00101', "
        f"'{{\\\"sst\\\": 1, \\\"sd\\\": \\\"{sd_dec}\\\"}}'"
    )
    sql2, nsub = re.subn(pattern, repl, sql, count=1)
    if not nsub:
        pattern2 = (
            rf"\('{re.escape(bench_imsi)}', '00101', "
            r"'{\"sst\": 1, \"sd\": \"[0-9]+\"}'"
        )
        sql2, nsub = re.subn(
            pattern2,
            f"('{bench_imsi}', '00101', '{{\"sst\": 1, \"sd\": \"{sd_dec}\"}}'",
            sql,
            count=1,
        )
    if nsub:
        doc["data"][key] = sql2
        dump(doc, mysql_cm)
        print(f"  MySQL: {bench_imsi} SessionManagement NSSAI → sst=1 sd={sd_dec} (0x000001)")
    else:
        print(f"  WARN: MySQL SessionManagement row for {bench_imsi} not patched")
else:
    print(f"  WARN: missing {mysql_cm.name}; UE NSSAI may stay on default FFFFFF")

print(f"Rendered {bench_ns} @ edge:")
print(f"  RAN: oai-ran-controller ({ran_op_image}) → NFDeployments cucp/cuup/du-bench")
print(f"  Nodes: DU={du_node} UE={ue_node} (rfsim Multus parent {usrp_iface})")
print(f"  CU-CP N2/F1/E1 {cucp_n2}/{cucp_f1c}/{cucp_e1} → AMF {amf_n2}")
print(f"  CU-UP E1/F1U/N3 {cuup_e1}/{cuup_f1u}/{cuup_n3} → UPF N3 {upf_n3}")
print(f"  DU F1/rfsim {du_f1}/{du_rf}, UE RF {ue_rf} (IMSI {bench_imsi}, NSSAI 1/0x000001, DNN internet)")
print(f"  near-RT RIC FlexRIC {flexric_ip} + xApp E2 {xapp_e2_ip}")
print(f"  xApp Swagger http://{xapp_swagger_vip}:{xapp_api_port}/docs")
print(f"  UPF {upf_name} N3/N4 {upf_n3}/{upf_n4} N6=DHCP (logical {upf_n6_logical} for SMF)")
print(f"  iperf3-n6: {iperf3_n6_image} (UPF sidecar :5201-5326 → InfluxDB; UE client helper)")
print(f"  Core {bench_ns}: AMF {amf_n2} SMF {smf_n4}")
PY

echo
echo "Push:  ./bringup/03_push_to_git_repos/push_git_repos.sh central edge"
echo "Verify: ./scripts/check-configsync.sh central edge"
echo "       kubectl --context edge@edge -n ${BENCH_NS} get nfdeployment,deploy"
echo "DU/UE nodes: ${DU_NODE} / ${UE_NODE}"
echo "near-RT RIC: FlexRIC ${FLEXRIC_IP}  xApp http://${XAPP_SWAGGER_VIP}:${XAPP_API_PORT}/docs"
echo "After operator create-once, pin VPC Multus parents + DU node:"
echo "       OAI_BENCH_DU_NODE=${DU_NODE} ./scripts/patch_oai_benchmark_ran_vpc.sh"
echo "       (needs node labels from ./scripts/label_ina_multus_masters.sh edge)"
echo "UE iperf (UPF N3) seeded in GitOps; optional refresh:"
echo "       ./scripts/sync_iperf3_n6_server_ip.sh"
