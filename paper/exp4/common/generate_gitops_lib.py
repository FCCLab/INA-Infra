#!/usr/bin/env python3
"""Shared Exp4 GitOps renderer. Placement comes from the scheme module.

Call ``render(scheme, out_dir)`` from paper/exp4/s0 or s1 (etc).
"""

from __future__ import annotations

import json
import re
import shutil
import types
from pathlib import Path

import yaml

# gNB Slices default: dedicated / min / max PRB % for every NSSAI (incl. default SD).
GNB_SLICE_DEDICATED = 0.0
GNB_SLICE_MIN = 0.0
GNB_SLICE_MAX = 100.0

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
OUT = HERE / "gitops_manifests"
EXP1_TEMPLATES = REPO_ROOT / "paper" / "exp1" / "templates"
APPS_ROOT = REPO_ROOT / "applications"

# Bound from scheme by render()
CLUSTERS = ()
FABRIC = {}
NAMESPACE = ""
PART_OF = "exp4"
PL_ENABLED = False
PM_ENABLED = False
PS_ENABLED = False
REPO_FOR = {}
SCHEME_ID = ""
SCHEME_NAME = ""
SLICES = {}
TIER_ID = {}
TIER_NAME = {}
XAPP_REPLICAS = 0


def site_label(cluster: str) -> str:
    return TIER_NAME[TIER_ID[cluster]]


def _bind(scheme: types.ModuleType, out_dir: Path) -> None:
    global HERE, OUT, REPO_ROOT, EXP1_TEMPLATES, APPS_ROOT
    global CLUSTERS, FABRIC, NAMESPACE, PART_OF
    global PL_ENABLED, PM_ENABLED, PS_ENABLED, REPO_FOR
    global SCHEME_ID, SCHEME_NAME, SLICES, TIER_ID, TIER_NAME, XAPP_REPLICAS
    HERE = out_dir.parent
    OUT = out_dir
    REPO_ROOT = HERE.parents[2]
    EXP1_TEMPLATES = REPO_ROOT / "paper" / "exp1" / "templates"
    APPS_ROOT = REPO_ROOT / "applications"
    CLUSTERS = scheme.CLUSTERS
    FABRIC = scheme.FABRIC
    NAMESPACE = scheme.NAMESPACE
    PART_OF = scheme.PART_OF
    PL_ENABLED = scheme.PL_ENABLED
    PM_ENABLED = scheme.PM_ENABLED
    PS_ENABLED = scheme.PS_ENABLED
    REPO_FOR = scheme.REPO_FOR
    SCHEME_ID = scheme.SCHEME_ID
    SCHEME_NAME = scheme.SCHEME_NAME
    SLICES = scheme.SLICES
    TIER_ID = scheme.TIER_ID
    TIER_NAME = scheme.TIER_NAME
    XAPP_REPLICAS = scheme.XAPP_REPLICAS

SKIP_NAME_SUBSTR = (
    "physical-ai",
    "physical_ai",
    "60-app-2-",
)


def ns_dir(repo: str, ns: str | None = None) -> Path:
    return OUT / repo / "namespaces" / (ns or NAMESPACE)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"  wrote {path.relative_to(HERE)}")


def transform_ns(text: str) -> str:
    text = re.sub(r"\bexp1-a\b", NAMESPACE, text)
    text = text.replace("--namespace=ina-infra", f"--namespace={NAMESPACE}")
    text = re.sub(r"app\.kubernetes\.io/part-of:\s*exp1\b", f"app.kubernetes.io/part-of: {PART_OF}", text)
    text = re.sub(r"app\.kubernetes\.io/part-of:\s*exp1-a\b", f"app.kubernetes.io/part-of: {PART_OF}", text)
    text = re.sub(r"ina\.lab/scheme:\s*\S+", f"ina.lab/scheme: {SCHEME_ID}", text)
    return text


def rewrite_ips_sites(text: str, sid: int) -> str:
    s = SLICES[sid]
    for key, val in (
        ("site_cu", site_label(s["cu"])),
        ("site_upf", site_label(s["upf"])),
        ("site_app", site_label(s["app"])),
        ("cluster_cu", s["cu"]),
        ("cluster_upf", s["upf"]),
    ):
        text = re.sub(rf"(?m)^{key}:\s*.*$", f"{key}: {val}", text)
    return text


def rewrite_slice_token(text: str, src: int, dst: int) -> str:
    """Rewrite slice/app/cu-up numbering from src to dst (filenames handled separately)."""
    pairs = [
        (rf"slice-{src}\b", f"slice-{dst}"),
        (rf"slice{src}\b", f"slice{dst}"),
        (rf"app-slice{src}\b", f"app-slice{dst}"),
        (rf"60-app-{src}-", f"60-app-{dst}-"),
        (rf"cu-up-{src}\b", f"cu-up-{dst}"),
        (rf"cuup-slice{src}\b", f"cuup-slice{dst}"),
        (rf"upf-slice-{src}\b", f"upf-slice-{dst}"),
        (rf"oai{src}\b", f"oai{dst}"),
        (rf"10\.140\.{src}\.", f"10.140.{dst}."),
        (rf"ina\.lab/slice:\s*['\"]?{src}['\"]?", f"ina.lab/slice: '{dst}'"),
        (rf"ina-infra\.nephio\.lab/slice:\s*['\"]?{src}['\"]?", f'ina-infra.nephio.lab/slice: "{dst}"'),
        (rf"slice:\s*['\"]?{src}['\"]?", f'slice: "{dst}"'),
        # Hex CU-UP id / S-NSSAI are not covered by oaiN / 10.140.N rewrites.
        (rf"gNB_CU_UP_ID\s*=\s*0xe0{src}\b", f"gNB_CU_UP_ID = 0xe0{dst}"),
        (rf"sd\s*=\s*0x{src:06x}\b", f"sd = 0x{dst:06x}"),
        (rf"sd:\s*'0*{src}'", f"sd: '{dst:06d}'"),
        (rf'sd:\s*"0*{src}"', f'sd: "{dst:06d}"'),
    ]
    for pat, repl in pairs:
        text = re.sub(pat, repl, text)
    src_fab, dst_fab = FABRIC[src], FABRIC[dst]
    for key in ("upf_n3", "upf_n4", "cuup_e1", "cuup_f1u", "cuup_n3", "ue_rf"):
        text = text.replace(src_fab[key], dst_fab[key])
    return text


def rewrite_app_addr(text: str, src_ip: str, dst_ip: str, src_mac: str, dst_mac: str) -> str:
    text = text.replace(src_ip, dst_ip)
    text = text.replace(src_mac, dst_mac)
    return text


def classify_file(name: str) -> tuple[str, int | None]:
    """Return (kind, slice_id). kind in shared|app|upf|cu|smf|ips."""
    if re.search(r"60-app-", name):
        m = re.search(r"60-app-(\d+)", name)
        return "app", int(m.group(1)) if m else None
    if re.search(r"25-config-smf-upf-slice-(\d+)", name):
        return "smf", int(re.search(r"slice-(\d+)", name).group(1))
    if re.search(r"30-slice-(\d+)-ips", name):
        return "ips", int(re.search(r"slice-(\d+)", name).group(1))
    if re.search(r"(?:nad-upf-slice-|nfconfig-upf-slice-|nfdeployment-upf-slice-)", name):
        m = re.search(r"slice-(\d+)", name)
        return "upf", int(m.group(1)) if m else None
    if re.search(r"(?:nad-cuup-slice|cu-up-\d+)", name):
        m = re.search(r"(?:slice(\d+)|cu-up-(\d+))", name)
        sid = int(m.group(1) or m.group(2))
        return "cu", sid
    return "shared", None


def dest_repo_for(kind: str, sid: int | None, src_repo: str) -> str | None:
    if kind == "shared":
        return src_repo
    if kind == "smf":
        return "central-repo"
    if sid is None or sid not in SLICES:
        return src_repo
    if kind == "app":
        return REPO_FOR[SLICES[sid]["app"]]
    if kind == "upf":
        return REPO_FOR[SLICES[sid]["upf"]]
    if kind == "cu":
        return REPO_FOR[SLICES[sid]["cu"]]
    if kind == "ips":
        return REPO_FOR[SLICES[sid]["cu"]]
    return src_repo


def write_namespace(repo: str) -> None:
    write_text(
        ns_dir(repo) / "00-namespace.yaml",
        f"""apiVersion: v1
kind: Namespace
metadata:
  name: {NAMESPACE}
  labels:
    app.kubernetes.io/name: {NAMESPACE}
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/scheme: {SCHEME_ID}
    ina.lab/pl: "{str(PL_ENABLED).lower()}"
    ina.lab/pm: "{str(PM_ENABLED).lower()}"
    ina.lab/ps: "{str(PS_ENABLED).lower()}"
""",
    )


def write_placement(repo: str) -> None:
    deploy_map = {}
    slices = []
    for sid, s in SLICES.items():
        place = {
            "cu": site_label(s["cu"]),
            "upf": site_label(s["upf"]),
            "app": site_label(s["app"]),
            "cu_id": TIER_ID[s["cu"]],
            "upf_id": TIER_ID[s["upf"]],
            "app_id": TIER_ID[s["app"]],
        }
        deploy_map[str(sid)] = place
        slices.append(
            {
                "id": sid,
                "slice_type": s["name"],
                "label": s["label"],
                "direction": "DL",
                "t_bar": s["t_bar"],
                "d_bar": s["d_bar"],
                "h_s": s["h_s"],
                "eta_t0": s["eta_t0"],
                "strict_sla": s["strict_sla"],
                "placement": place,
                "resources": {
                    "a_c_cu": s["t_bar"] / 1.02,
                    "a_r_cu": 10.0,
                    "a_c_upf": s["t_bar"] / 0.81,
                    "a_r_upf": 10.0,
                    "a_c_app": s["cpu_app"],
                    "a_r_app": 256.0,
                    "a_g_app": s["gpu_app"],
                    "b_min": s["b_min"],
                    "b_ded": s["b_min"] if s["h_s"] else None,
                },
            }
        )
    doc = {
        "cluster": "multi-cluster",
        "namespace": NAMESPACE,
        "profile": {
            "name": NAMESPACE,
            "subnet": "10.1.140.0/24",
            "max_slices": 16,
            "dnn_prefix": "10.140",
            "du_node": "usrp",
            "ue_node": "usrp",
        },
        "scheme_id": SCHEME_ID,
        "scheme_name": SCHEME_NAME,
        "knobs": {"pl": PL_ENABLED, "pm": PM_ENABLED, "ps": PS_ENABLED},
        "deploy_map": deploy_map,
        "slices": slices,
    }
    indented = "\n".join("    " + line for line in json.dumps(doc, indent=2).splitlines())
    write_text(
        ns_dir(repo) / "20-placement-configmap.yaml",
        f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: ina-pl-placement
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: ina-pl-placement
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/scheme: {SCHEME_ID}
data:
  placement.json: |
{indented}
""",
    )


def write_scheme_cm(repo: str) -> None:
    write_text(
        ns_dir(repo) / "21-scheme-configmap.yaml",
        f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: ina-exp4-scheme
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: ina-exp4-scheme
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/scheme: {SCHEME_ID}
data:
  scheme: {SCHEME_ID}
  pl: "{str(PL_ENABLED).lower()}"
  pm: "{str(PM_ENABLED).lower()}"
  ps: "{str(PS_ENABLED).lower()}"
  prb_policy: {"equal" if not PS_ENABLED else "live-eta"}
  compute_policy: {"frozen-peak" if not PM_ENABLED else "pm-resize"}
""",
    )


def copy_shared_and_nfs() -> None:
    """Copy exp1-a templates, relocating UPF/CU-UP to this scheme's sites."""
    for repo in ("central-repo", "regional-repo", "edge-repo"):
        src_root = EXP1_TEMPLATES / repo / "namespaces" / "exp1-a"
        if not src_root.exists():
            raise FileNotFoundError(src_root)
        for item in sorted(src_root.glob("*.yaml")):
            name = item.name
            if name == "00-namespace.yaml" or "placement-configmap" in name:
                continue
            if any(s in name for s in SKIP_NAME_SUBSTR):
                continue
            kind, sid = classify_file(name)
            if kind == "app":
                continue
            dest_repo = dest_repo_for(kind, sid, repo)
            text = transform_ns(item.read_text())
            if sid in SLICES:
                text = rewrite_ips_sites(text, sid)
            if name.startswith("48-deployment-nws-xapp"):
                text = re.sub(r"(?m)^  replicas:\s*\d+", f"  replicas: {XAPP_REPLICAS}", text)
            write_text(ns_dir(dest_repo) / name, text)


def _copy_app_files(src_glob: str, src_repo: str, dest_sid: int, src_sid: int, src_ip: str, src_mac: str) -> None:
    dest = SLICES[dest_sid]
    dest_repo = REPO_FOR[dest["app"]]
    src_root = EXP1_TEMPLATES / src_repo / "namespaces" / "exp1-a"
    for item in sorted(src_root.glob(src_glob)):
        text = transform_ns(item.read_text())
        if src_sid != dest_sid:
            text = rewrite_slice_token(text, src_sid, dest_sid)
            fname = rewrite_slice_token(item.name, src_sid, dest_sid)
        else:
            fname = item.name
        text = rewrite_app_addr(text, src_ip, dest["app_ip"], src_mac, dest["app_mac"])
        write_text(ns_dir(dest_repo) / fname, text)


def copy_existing_apps() -> None:
    # Slice 2: CCTV YOLO (was app-1 on regional @ .211) → edge @ .212
    _copy_app_files("60-app-1-*", "regional-repo", dest_sid=2, src_sid=1, src_ip="10.1.137.211", src_mac="02:0a:89:a0:00:01")
    # Slice 3: OTT (was central @ .213) → regional @ .213
    _copy_app_files("60-app-3-*", "central-repo", dest_sid=3, src_sid=3, src_ip="10.1.137.213", src_mac="02:0a:89:a0:00:03")
    # Slice 5: IoT MQTT (was app-4 @ .214) → central @ .215
    _copy_app_files("60-app-4-*", "central-repo", dest_sid=5, src_sid=4, src_ip="10.1.137.214", src_mac="02:0a:89:a0:00:04")
    sync_exp4_app_code()


def _literal_cm_data(files: dict[str, Path]) -> str:
    blocks: list[str] = []
    for key, path in files.items():
        text = path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        indented = "\n".join(("    " + line) if line else "" for line in text.splitlines())
        blocks.append(f"  {key}: |\n{indented}")
    return "\n".join(blocks)


def write_app_code_configmap(
    sid: int,
    cm_name: str,
    app_name: str,
    app_type: str,
    filename: str,
    files: dict[str, Path],
) -> None:
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print(f"  warn: skip {cm_name}; missing {missing}")
        return
    repo = REPO_FOR[SLICES[sid]["app"]]
    write_text(
        ns_dir(repo) / filename,
        f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {cm_name}
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: {app_name}
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/slice: '{sid}'
    ina.lab/app-type: {app_type}
    ina.lab/role: server
    ina.lab/scheme: {SCHEME_ID}
data:
{_literal_cm_data(files)}
""",
    )


def sync_exp4_app_code() -> None:
    """Overwrite remapped ConfigMaps from applications/exp4_* (dedicated trees)."""
    common_influx = APPS_ROOT / "exp4" / "common" / "influx_publish.py"
    cctv = APPS_ROOT / "exp4" / "exp4_s2_cctv" / "server"
    write_app_code_configmap(
        2,
        "application-cctv-code",
        "application-cctv",
        "cctv",
        "60-app-2-application-cctv-code-configmap.yaml",
        {name: (common_influx if name == "influx_publish.py" else cctv / name) for name in (
            "cctv.py",
            "yolo_worker.py",
            "state.py",
            "api.py",
            "mtx_publish.py",
            "mediamtx.yml",
            "entrypoint.sh",
            "__init__.py",
            "influx_publish.py",
        )},
    )
    ott = APPS_ROOT / "exp4" / "exp4_s3_ott" / "server"
    write_app_code_configmap(
        3,
        "application-ott-code",
        "application-ott",
        "ott",
        "60-app-3-application-ott-code-configmap.yaml",
        {name: (common_influx if name == "influx_publish.py" else ott / name) for name in (
            "main.py",
            "ott.py",
            "api.py",
            "state.py",
            "youtube_resolver.py",
            "mediamtx.yml",
            "entrypoint.sh",
            "__init__.py",
            "influx_publish.py",
        )},
    )


def _uses_gpu(sid: int) -> bool:
    return float(SLICES[sid].get("gpu_app") or 0) > 0


def _multus_master(sid: int) -> str:
    """N6 parent NIC. GPU apps pin to edge gpu-a40 (ens12f0); CPU apps use VM enp7s0."""
    return "ens12f0" if _uses_gpu(sid) else "enp7s0"


def _multus_nad(sid: int) -> str:
    s = SLICES[sid]
    master = _multus_master(sid)
    return f"""apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: app-slice{sid}-multus
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: app-slice{sid}-multus
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/slice: '{sid}'
    ina-infra.nephio.lab/role: app
spec:
  config: '{{"cniVersion": "0.3.1", "name": "app-slice{sid}-multus", "plugins": [{{"type": "macvlan", "capabilities": {{"ips": true, "mac": true}}, "master": "{master}", "mode": "bridge", "ipam": {{"type": "static", "addresses": [{{"address": "{s['app_ip']}/24", "gateway": "10.1.137.1"}}]}}}}, {{"type": "tuning", "capabilities": {{"mac": true}}, "ipam": {{}}, "sysctl": {{"net.ipv4.conf.IFNAME.arp_ignore": "1", "net.ipv4.conf.IFNAME.arp_announce": "2"}}}}]}}'
"""


def _route_init(ip: str) -> str:
    return (
        'set -euo pipefail\\n'
        f'IFACE="net1"\\nIP="{ip}"\\nGW="10.1.137.1"\\n'
        'for _ in $(seq 1 40); do\\n  ip link show "$IFACE" >/dev/null 2>&1 && break\\n  sleep 1\\ndone\\n'
        'ip link show "$IFACE"\\n'
        'ip route replace 10.1.137.0/24 dev "$IFACE" || true\\n'
        'ip route replace 10.1.137.0/24 dev "$IFACE" table 137 || true\\n'
        'ip route replace default via "$GW" dev "$IFACE" table 137 || true\\n'
        'ip rule del from "$IP"/32 table 137 2>/dev/null || true\\n'
        'ip rule add from "$IP"/32 table 137 priority 100\\n'
    )


def _server_influx_env(sid: int, app_type: str, cluster: str, app_name: str) -> str:
    """Influx + probe env for the application server container."""
    return f"""        - name: INFLUXDB_URL
          value: http://influxdb.influxdb.svc:8086
        - name: INFLUXDB_TOKEN
          value: ina-infra-influxdb-token
        - name: INFLUXDB_ORG
          value: ina-infra
        - name: INFLUXDB_BUCKET
          value: default
        - name: INFLUXDB_MEASUREMENT
          value: application_metrics
        - name: SLICE_ID
          value: "{sid}"
        - name: EXP4_APP_TYPE
          value: {app_type}
        - name: APP_TYPE
          value: {app_type}
        - name: APP_NAME
          value: {app_name}
        - name: SCHEME_ID
          value: {SCHEME_ID}
        - name: E2E_PROBE_HOST
          value: 10.140.{sid}.2
        - name: TO_CLIENT_IFACE
          value: "net1"
        - name: CONSOLE_IFACE
          value: "net1"
        - name: CONSOLE_IP
          value: {SLICES[sid]['app_ip']}
        - name: MULTUS_GW
          value: "10.1.137.1"
        - name: TARGET_CLUSTER
          value: {cluster}
        - name: EXP4_METRICS_ORIGIN
          value: server
        - name: PUBLIC_BASE_URL
          value: http://{SLICES[sid]['app_ip']}
        - name: MULTUS_IP
          value: {SLICES[sid]['app_ip']}
"""


def patch_copied_app_metrics() -> None:
    """Fix SLICE_ID and add container-metrics env + publisher mount on remapped apps."""
    extras = (
        (2, "exp4-s2", "application-cctv", "/app/edge/__init__.py", "/app/edge/influx_publish.py"),
        (3, "exp4-s3", "application-ott", "/app/server/__init__.py", "/app/server/influx_publish.py"),
        (5, "exp4-s5", "application-iot", None, None),
    )
    insert = (
        "        - name: EXP4_APP_TYPE\n"
        "          value: {app_type}\n"
        "        - name: SCHEME_ID\n"
        f"          value: {SCHEME_ID}\n"
        "        - name: E2E_PROBE_HOST\n"
        "          value: 10.140.{sid}.2\n"
        "        - name: TO_CLIENT_IFACE\n"
        '          value: "net1"\n'
    )
    for sid, app_type, _name, after_mount, new_mount in extras:
        repo = REPO_FOR[SLICES[sid]["app"]]
        for path in sorted(ns_dir(repo).glob(f"60-app-{sid}-*-deployment.yaml")):
            text = path.read_text()
            text = re.sub(
                r"(?m)^(\s+- name: SLICE_ID\n\s+value: )['\"]?\d+['\"]?",
                rf"\1'{sid}'",
                text,
            )
            if "EXP4_APP_TYPE" not in text:
                text = re.sub(
                    r"(?m)^(\s+- name: APP_TYPE\n\s+value: \S+\n)",
                    r"\1" + insert.format(app_type=app_type, sid=sid),
                    text,
                    count=1,
                )
            if after_mount and new_mount and new_mount not in text:
                old = (
                    f"        - name: code-volume\n"
                    f"          mountPath: {after_mount}\n"
                    f"          subPath: {Path(after_mount).name}\n"
                )
                new = old + (
                    f"        - name: code-volume\n"
                    f"          mountPath: {new_mount}\n"
                    f"          subPath: influx_publish.py\n"
                )
                text = text.replace(old, new, 1)
            write_text(path, text)


def write_iperf_app() -> None:
    """Slice 1: frozen-peak iperf3 + SFTP (openssh) DL server on central."""
    s = SLICES[1]
    repo = REPO_FOR[s["app"]]
    write_text(ns_dir(repo) / "60-app-1-app-slice1-multus-networkattachmentdefinition.yaml", _multus_nad(1))
    write_text(
        ns_dir(repo) / "60-app-1-application-iperf-sftp.yaml",
        f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: application-iperf-sftp
  namespace: {NAMESPACE}
---
apiVersion: v1
kind: Service
metadata:
  name: application-iperf-sftp
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: application-iperf-sftp
    ina.lab/slice: '1'
spec:
  selector:
    app.kubernetes.io/name: application-iperf-sftp
  ports:
  - name: iperf
    port: 5201
    targetPort: 5201
  - name: sftp
    port: 22
    targetPort: 22
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: application-iperf-sftp
  namespace: {NAMESPACE}
  labels: &id001
    app.kubernetes.io/name: application-iperf-sftp
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/slice: '1'
    ina.lab/app-type: iperf-sftp
    ina.lab/role: server
    ina.lab/scheme: {SCHEME_ID}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels: *id001
  template:
    metadata:
      labels: *id001
      annotations:
        k8s.v1.cni.cncf.io/networks: '[{{"name": "app-slice1-multus", "interface": "net1", "ips": ["{s['app_ip']}/24"], "gateways": ["10.1.137.1"], "mac": "{s['app_mac']}"}}]'
    spec:
      serviceAccountName: application-iperf-sftp
      nodeSelector:
        kubernetes.io/arch: amd64
        ina-infra.nephio.lab/multus-master: enp7s0
      initContainers:
      - name: multus-default-route
        image: docker.io/nicolaka/netshoot
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        command: ["bash", "-c", "{_route_init(s['app_ip'])}"]
        resources:
          requests: {{cpu: 10m, memory: 16Mi}}
          limits: {{cpu: 100m, memory: 64Mi}}
      - name: make-sftp-payload
        image: docker.io/nicolaka/netshoot
        imagePullPolicy: IfNotPresent
        command: ["bash", "-c", "dd if=/dev/urandom of=/home/ina/download/exp4-5mb.bin bs=1M count=5 status=none && chmod 644 /home/ina/download/exp4-5mb.bin"]
        volumeMounts:
        - name: payload
          mountPath: /home/ina/download
        resources:
          requests: {{cpu: 10m, memory: 16Mi}}
          limits: {{cpu: 100m, memory: 64Mi}}
      containers:
      - name: iperf
        image: docker.io/networkstatic/iperf3:latest
        imagePullPolicy: IfNotPresent
        env:
{_server_influx_env(1, "exp4-s1", s["app"], "application-iperf-sftp")}        args: ["-s", "-p", "5201"]
        ports:
        - containerPort: 5201
        resources:
          requests:
            cpu: "{s['cpu_app']}"
            memory: {s['mem_app']}
          limits:
            cpu: "{s['cpu_app']}"
            memory: {s['mem_app']}
      - name: sftp
        image: docker.io/atmoz/sftp:alpine
        imagePullPolicy: IfNotPresent
        args: ["ina:ina:1001::download"]
        ports:
        - containerPort: 22
        volumeMounts:
        - name: payload
          mountPath: /home/ina/download
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 250m
            memory: 256Mi
      volumes:
      - name: payload
        emptyDir: {{}}
""",
    )


def write_cpu_offload_app() -> None:
    """Slice 4: same SFTP+iperf path as slice 1, plus encrypt (baked Exp4 image)."""
    s = SLICES[4]
    repo = REPO_FOR[s["app"]]
    write_text(ns_dir(repo) / "60-app-4-app-slice4-multus-networkattachmentdefinition.yaml", _multus_nad(4))
    write_text(
        ns_dir(repo) / "60-app-4-application-cpu-offload.yaml",
        f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: application-cpu-offload
  namespace: {NAMESPACE}
---
apiVersion: v1
kind: Service
metadata:
  name: application-cpu-offload
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: application-cpu-offload
    ina.lab/slice: '4'
spec:
  selector:
    app.kubernetes.io/name: application-cpu-offload
  ports:
  - name: http
    port: 80
    targetPort: 80
  - name: sftp
    port: 22
    targetPort: 22
  - name: iperf
    port: 5201
    targetPort: 5201
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: application-cpu-offload
  namespace: {NAMESPACE}
  labels: &id001
    app.kubernetes.io/name: application-cpu-offload
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/slice: '4'
    ina.lab/app-type: cpu-offload
    ina.lab/role: server
    ina.lab/scheme: {SCHEME_ID}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels: *id001
  template:
    metadata:
      labels: *id001
      annotations:
        k8s.v1.cni.cncf.io/networks: '[{{"name": "app-slice4-multus", "interface": "net1", "ips": ["{s['app_ip']}/24"], "gateways": ["10.1.137.1"], "mac": "{s['app_mac']}"}}]'
    spec:
      serviceAccountName: application-cpu-offload
      nodeSelector:
        kubernetes.io/arch: amd64
        ina-infra.nephio.lab/multus-master: enp7s0
      initContainers:
      - name: multus-default-route
        image: docker.io/nicolaka/netshoot
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        command: ["bash", "-c", "{_route_init(s['app_ip'])}"]
        resources:
          requests: {{cpu: 10m, memory: 16Mi}}
          limits: {{cpu: 100m, memory: 64Mi}}
      containers:
      - name: pipeline
        image: 10.1.132.30:5000/exp4-s4-cpu-offload-server:nws-v0.30-amd64
        imagePullPolicy: Always
        env:
{_server_influx_env(4, "exp4-s4", s["app"], "application-cpu-offload")}        - name: IPERF_AUTOSTART
          value: "1"
        - name: SFTP_AUTOSTART
          value: "1"
        ports:
        - containerPort: 80
        - containerPort: 22
        - containerPort: 5201
        - containerPort: 8080
        resources:
          requests:
            cpu: "{s['cpu_app']}"
            memory: {s['mem_app']}
          limits:
            cpu: "{s['cpu_app']}"
            memory: {s['mem_app']}
""",
    )


NSSAI_SLICE5 = """
        - sst: 1
          sd: '000005'
          dnnInfo:
          - name: oai5
            sessionType: ipv4
            dns: 1.1.1.1
            subnet: 10.140.5.0/24
"""


def _gnb_slice_sd(sid: int) -> str:
    return "0xffffff" if sid == 0 else f"0x{sid:06x}"


def _nssai_sd(sid: int) -> str:
    return "0xFFFFFF" if sid == 0 else f"0x{sid:06x}"


def _build_gnb_slices_block() -> str:
    sids = [0, *sorted(SLICES)]
    rows = []
    for i, sid in enumerate(sids):
        comma = "," if i < len(sids) - 1 else ""
        rows.append(
            "  { slice_id = "
            f"{sid}; sst = 1; sd = {_gnb_slice_sd(sid)}; "
            f"dedicated_prb_ratio = {GNB_SLICE_DEDICATED:.1f}; "
            f"min_prb_ratio = {GNB_SLICE_MIN:.1f}; "
            f"max_prb_ratio = {GNB_SLICE_MAX:.1f}; }}{comma}"
        )
    return "Slices = (\n" + "\n".join(rows) + "\n)"


def _build_snssai_list() -> str:
    items = [f"{{ sst = 1, sd = {_nssai_sd(sid)} }}" for sid in (0, *sorted(SLICES))]
    return "snssaiList = (" + ", ".join(items) + ")"


def _patch_gnb_conf(conf: str, *, slices: bool) -> str:
    if slices:
        conf, n = re.subn(r"Slices\s*=\s*\(.*?\n\)", _build_gnb_slices_block(), conf, count=1, flags=re.S)
        if n != 1:
            raise SystemExit("failed to patch gNB Slices block")
    conf, n = re.subn(r"snssaiList\s*=\s*\([^)]*\)", _build_snssai_list(), conf, count=1)
    if n != 1:
        raise SystemExit("failed to patch gNB snssaiList")
    return conf


def _rewrite_cm_gnb_conf(path: Path, conf: str) -> None:
    raw = path.read_text()
    m = re.search(r"(?m)^data:\s*$", raw)
    if not m:
        raise SystemExit(f"{path.name}: missing data: key")
    write_text(path, raw[: m.start()] + "data:\n  gnb.conf: " + json.dumps(conf) + "\n")


def patch_gnb_slices() -> None:
    """Force every gNB slice to dedicated/min/max = 0/0/100 and include slice 5."""
    du = ns_dir("edge-repo") / "44-configmap-oai-du-configmap.yaml"
    cucp = ns_dir("edge-repo") / "41-configmap-oai-cu-cp-configmap.yaml"
    du_doc = yaml.safe_load(du.read_text())
    _rewrite_cm_gnb_conf(du, _patch_gnb_conf(du_doc["data"]["gnb.conf"], slices=True))
    cucp_doc = yaml.safe_load(cucp.read_text())
    _rewrite_cm_gnb_conf(cucp, _patch_gnb_conf(cucp_doc["data"]["gnb.conf"], slices=False))
    print(
        f"  gNB slices default dedicated/min/max="
        f"{GNB_SLICE_DEDICATED:.0f}/{GNB_SLICE_MIN:.0f}/{GNB_SLICE_MAX:.0f} "
        f"(ids 0,{','.join(str(s) for s in sorted(SLICES))})"
    )


def _mysql_sql_json(obj: object) -> str:
    """JSON as stored in 05-configmap-mysql-initialization.yaml (quotes backslash-escaped)."""
    return json.dumps(obj, separators=(", ", ": ")).replace('"', r"\"")


def patch_mysql_ues() -> None:
    """Map scheme IMSIs to per-slice DNN/NSSAI and expand AM defaultSingleNssais."""
    path = ns_dir("central-repo") / "05-configmap-mysql-initialization.yaml"
    if not path.exists():
        print("  warn: mysql initialization ConfigMap missing; skip UDR patch")
        return
    text = path.read_text()
    am_obj = {
        "defaultSingleNssais": [{"sst": 1, "sd": f"{sid:06d}"} for sid in sorted(SLICES)]
    }
    am_new = f"('00101', '', '{_mysql_sql_json(am_obj)}');"
    text, am_n = re.subn(
        r"\('00101', '', '\{\\\"defaultSingleNssais\\\": \[.*?\]\}'\);",
        am_new,
        text,
        count=1,
    )
    if am_n:
        print(f"  mysql AM defaultSingleNssais → slices {','.join(str(s) for s in sorted(SLICES))}")
    else:
        print("  warn: mysql AM defaultSingleNssais row not patched")

    for sid, s in sorted(SLICES.items()):
        imsi = s["imsi"]
        dnn = s["dnn"]
        old = (
            f"('{imsi}', '00101', '"
            + _mysql_sql_json({"sst": 1, "sd": "1"})
            + "', '"
            + '{"oai1":'.replace('"', r"\"")
        )
        new = (
            f"('{imsi}', '00101', '"
            + _mysql_sql_json({"sst": 1, "sd": str(sid)})
            + "', '"
            + ('{"' + dnn + '":').replace('"', r"\"")
        )
        if old == new:
            continue
        if old not in text:
            print(f"  warn: mysql SM row for {imsi} not found (already patched?)")
            continue
        text = text.replace(old, new, 1)
        print(f"  mysql SM {imsi} → sd={sid} dnn={dnn}")
    write_text(path, text)


def patch_core_nssai() -> None:
    """AMF/SMF templates only list slices 1–4; append slice 5."""
    for name in ("24-nfconfig-amf.yaml", "25-nfconfig-smf.yaml"):
        path = ns_dir("central-repo") / name
        text = path.read_text()
        if "000005" in text:
            continue
        needle = "            subnet: 10.140.4.0/24\n"
        if needle not in text:
            print(f"  warn: {name} missing slice-4 nssai block; skip slice-5 patch")
            continue
        text = text.replace(needle, needle + NSSAI_SLICE5, 1)
        write_text(path, text)


def patch_smf_upf_slice5() -> None:
    """Exp1 SMF NFDeployment only refs UPF Configs 1–4; attach slice 5."""
    path = ns_dir("central-repo") / "25-nfdeployment-smf.yaml"
    if not path.exists():
        print("  warn: SMF NFDeployment missing; skip slice-5 parametersRef")
        return
    text = path.read_text()
    if "smf-core-upf-slice-5" in text:
        return
    needle = """  - name: smf-core-upf-slice-4
    apiVersion: ref.nephio.org/v1alpha1
    kind: Config
"""
    extra = needle + """
  - name: smf-core-upf-slice-5
    apiVersion: ref.nephio.org/v1alpha1
    kind: Config
"""
    if needle not in text:
        print("  warn: SMF NFDeployment missing slice-4 parametersRef")
        return
    write_text(path, text.replace(needle, extra, 1))
    print("  SMF NFDeployment parametersRefs += smf-core-upf-slice-5")


def clone_slice5_nf() -> None:
    """Clone slice-4 UPF/CU-UP/SMF/IPS templates to slice 5 on central."""
    src_repo = "central-repo"
    src_root = EXP1_TEMPLATES / src_repo / "namespaces" / "exp1-a"
    dest_repo = REPO_FOR[SLICES[5]["cu"]]
    patterns = (
        "25-config-smf-upf-slice-4.yaml",
        "30-slice-4-ips-configmap.yaml",
        "32-nad-upf-slice-4-*.yaml",
        "32-nad-cuup-slice4-*.yaml",
        "34-nfconfig-upf-slice-4.yaml",
        "35-nfdeployment-upf-slice-4.yaml",
        "36-serviceaccount-oai-cu-up-4-sa.yaml",
        "37-configmap-oai-cu-up-4-configmap.yaml",
        "38-deployment-oai-cu-up-4.yaml",
    )
    seen: set[Path] = set()
    for pat in patterns:
        for item in src_root.glob(pat):
            if item in seen:
                continue
            seen.add(item)
            text = transform_ns(item.read_text())
            text = rewrite_slice_token(text, 4, 5)
            text = rewrite_ips_sites(text, 5)
            fname = rewrite_slice_token(item.name, 4, 5)
            write_text(ns_dir(dest_repo) / fname, text)


def write_slice_ips_extras() -> None:
    """Ensure each slice IPS ConfigMap exists on the CU site for this scheme."""
    for sid, s in SLICES.items():
        fab = FABRIC[sid]
        repo = REPO_FOR[s["cu"]]
        path = ns_dir(repo) / f"30-slice-{sid}-ips-configmap.yaml"
        write_text(
            path,
            f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: ina-slice-{sid}-ips
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/part-of: {PART_OF}
    ina-infra.nephio.lab/slice: "{sid}"
    ina.lab/scheme: {SCHEME_ID}
data:
  slice_id: "{sid}"
  n: "{sid}"
  upf_n3: "{fab['upf_n3']}"
  upf_n4: "{fab['upf_n4']}"
  upf_n6: "dhcp:10.1.137.0/24"
  cuup_e1: "{fab['cuup_e1']}"
  cuup_f1u: "{fab['cuup_f1u']}"
  cuup_n3: "{fab['cuup_n3']}"
  ue_rf: "{fab['ue_rf']}"
  dnn_cidr: "{fab['dnn_cidr']}"
  site_cu: "{site_label(s['cu'])}"
  site_upf: "{site_label(s['upf'])}"
  site_app: "{site_label(s['app'])}"
  cluster_cu: "{s['cu']}"
  cluster_upf: "{s['upf']}"
  peers: |
    CU-UP E1 -> CU-CP E1 10.1.140.202
    CU-UP N3 -> UPF N3 {fab['upf_n3']}
    UPF N4 -> SMF N4 10.1.140.12
    UPF N6 -> DHCP on Multus (Glass 10.1.137.0/24, gw 10.1.137.1)
    UE RF -> DU RF 10.1.140.204:4043
""",
        )


def write_operator_rbac() -> None:
    """Bind OAI kopf operators in this scheme namespace to the existing ClusterRoles."""
    central_nfs = ("amf", "ausf", "nrf", "smf", "udm", "udr", "upf")
    upf_only = ("upf",)
    for repo, nfs in (
        ("central-repo", central_nfs),
        ("regional-repo", upf_only),
        ("edge-repo", upf_only),
    ):
        for nf in nfs:
            write_text(
                OUT / repo / "cluster" / f"clusterrolebinding-{nf}-operator-{NAMESPACE}.yaml",
                f"""apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {nf}-operator-{NAMESPACE}
  labels:
    app.kubernetes.io/part-of: {PART_OF}
    ina.lab/scheme: {SCHEME_ID}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: oai-{nf}-operator-cluster-role
subjects:
- kind: ServiceAccount
  name: oai-{nf}-operator
  namespace: {NAMESPACE}
""",
            )


def render(scheme: types.ModuleType | None = None, out_dir: Path | None = None) -> None:
    if scheme is not None and out_dir is not None:
        _bind(scheme, out_dir)
    if not EXP1_TEMPLATES.exists():
        raise SystemExit(f"exp1 templates missing: {EXP1_TEMPLATES}")
    if OUT.exists():
        shutil.rmtree(OUT)
    print(f"Rendering {SCHEME_ID} ({SCHEME_NAME}) namespace={NAMESPACE}")
    print(f"  PL={PL_ENABLED} PM={PM_ENABLED} PS={PS_ENABLED} xapp_replicas={XAPP_REPLICAS}")
    for sid, s in SLICES.items():
        print(f"  slice {sid} {s['name']}: {s['cu']}/{s['upf']}/{s['app']}")
    for repo in (REPO_FOR[c] for c in CLUSTERS):
        write_namespace(repo)
        write_placement(repo)
        write_scheme_cm(repo)
    copy_shared_and_nfs()
    from baked_apps import write_baked_apps

    write_baked_apps()
    write_operator_rbac()
    clone_slice5_nf()
    patch_core_nssai()
    patch_smf_upf_slice5()
    patch_mysql_ues()
    patch_gnb_slices()
    write_slice_ips_extras()
    print(f"Rendered into {OUT}")
