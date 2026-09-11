#!/usr/bin/env python3
"""Bring up DL UEs + app clients on edge `usrp` for Exp4 S3 (exp4-s3).

Examples:
  python3 paper/exp4/s3/deploy_ue.py
  python3 paper/exp4/s3/deploy_ue.py --ue 5
  python3 paper/exp4/s3/deploy_ue.py 1,2,3,4
  python3 paper/exp4/s3/deploy_ue.py --undeploy --ue 1,2,3,4
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))

from ott_chromium import chromium_sidecar_yaml, chromium_volumes_yaml, extra_ott_env  # noqa: E402
from scheme import NAMESPACE, REGISTRY, SCHEME_ID, SLICES  # noqa: E402
from ue_resources import app_client_resources_yaml, ue_ran_resources_yaml  # noqa: E402
from ue_teardown import parse_ue_ids, undeploy_ues  # noqa: E402

EDGE_CONTEXT = "edge@edge"
UE_IMAGE = f"{REGISTRY}/oai-nr-ue:nws-v0.8.4-amd64"
IFACES_SH = HERE.parents[2] / "applications" / "exp4" / "common" / "ifaces.sh"
INFLUX_PY = IFACES_SH.parent / "influx_publish.py"

CLIENT_IMAGES = {
    1: f"{REGISTRY}/exp4-s1-iperf-sftp-client:nws-v0.30-amd64",
    2: f"{REGISTRY}/exp4-s2-cctv-client:nws-v0.30-amd64",
    3: f"{REGISTRY}/exp4-s3-ott-client:nws-v0.30-amd64",
    4: f"{REGISTRY}/exp4-s4-cpu-offload-client:nws-v0.30-amd64",
    5: f"{REGISTRY}/exp4-s5-iot-client:nws-v0.30-amd64",
}


def extra_env(sid: int) -> str:
    s = SLICES[sid]
    ip = s["app_ip"]
    common = f"""
        - name: CONSOLE_ROLE
          value: "all"
        - name: TARGET_SERVER_IP
          value: "{ip}"
        - name: TO_SERVER_IFACE
          value: "oaitun_ue1"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: CONSOLE_IFACE
          value: "net2"
        - name: MULTUS_GW
          value: "10.1.137.1"
        - name: PUBLIC_BASE_URL
          value: "http://{s['ue_console_ip']}"
        - name: PDU_ROUTE_HOSTS
          value: "{ip}"
        - name: PDU_WAIT_TIMEOUT
          value: "0"
        - name: EXP4_APP_TYPE
          value: "exp4-s{sid}"
        - name: SCHEME_ID
          value: "{SCHEME_ID}"
        - name: EXP4_METRICS_ORIGIN
          value: "client"
"""
    extras = {
        1: f"""
        - name: IPERF_HOST
          value: "{ip}"
        - name: SFTP_HOST
          value: "{ip}"
        - name: SFTP_USER
          value: "ina"
        - name: SFTP_PASS
          value: "ina"
        - name: IPERF_AUTOSTART
          value: "0"
        - name: IPERF_PORT_COUNT
          value: "8"
""",
        2: f"""
        - name: RTSP_TARGET_HOST
          value: "{ip}"
        - name: SERVER_URL
          value: "http://{ip}:8080"
        - name: MTX_SOURCE_HOST
          value: "{ip}"
        - name: MTX_SOURCE_RTSP_PORT
          value: "8555"
        - name: DS_NUM_STREAMS
          value: "4"
        - name: STREAMING_ENABLED
          value: "0"
        - name: IPERF_HOST
          value: "{ip}"
        - name: IPERF_AUTOSTART
          value: "0"
""",
        3: f"""
        - name: SERVER_HOST
          value: "{ip}"
        - name: SERVER_URL
          value: "http://{ip}"
        - name: SERVER_RTSP_PORT
          value: "8555"
        - name: IPERF_HOST
          value: "{ip}"
        - name: IPERF_AUTOSTART
          value: "0"
"""
        + extra_ott_env(s["ue_console_ip"]),
        4: f"""
        - name: SFTP_HOST
          value: "{ip}"
        - name: IPERF_HOST
          value: "{ip}"
        - name: SFTP_USER
          value: "ina"
        - name: SFTP_PASS
          value: "ina"
        - name: IPERF_AUTOSTART
          value: "0"
        - name: IPERF_PORT_COUNT
          value: "8"
""",
        5: f"""
        - name: BROKER_HOST
          value: "{ip}"
        - name: BROKER_PORT
          value: "1883"
        - name: SEND_ENABLED
          value: "0"
        - name: LATENCY_PROBE_ENABLED
          value: "0"
        - name: IPERF_HOST
          value: "{ip}"
        - name: IPERF_AUTOSTART
          value: "0"
""",
    }
    return common + extras[sid]


def ifaces_configmap_yaml() -> str:
    def _indent(body: str) -> str:
        return "\n".join(f"    {line}" if line else "" for line in body.splitlines())

    ifaces = _indent(IFACES_SH.read_text())
    influx = _indent(INFLUX_PY.read_text())
    return f"""---
apiVersion: v1
kind: ConfigMap
metadata:
  name: exp4-ifaces
  namespace: {NAMESPACE}
data:
  ifaces.sh: |
{ifaces}
  influx_publish.py: |
{influx}
"""


def generate_ue_yaml(sid: int) -> str:
    s = SLICES[sid]
    ue_name = f"oai-ue-{sid}"
    rf_net = f"ue{sid}-sim-rf"
    console_ip = s["ue_console_ip"]
    console_mac = f"02:0a:40:{sid:02x}:00:01"
    return f"""---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: {rf_net}
  namespace: {NAMESPACE}
spec:
  config: '{{"cniVersion": "0.3.1", "type": "macvlan", "master": "enp4s0f0", "mode": "bridge", "ipam": {{"type": "static"}}}}'
---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ue{sid}-console-multus
  namespace: {NAMESPACE}
spec:
  config: '{{"cniVersion": "0.3.1", "type": "macvlan", "master": "enp4s0f0", "mode": "bridge", "ipam": {{"type": "static"}}}}'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {ue_name}-sa
  namespace: {NAMESPACE}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {ue_name}-configmap
  namespace: {NAMESPACE}
data:
  ue.conf: |
    uicc0 = {{
      imsi = "{s['imsi']}";
      key = "fec86ba6eb707ed08905757b1bb44b8f";
      opc = "C42449363BBAD02B66D16BC975D77CC1";
      dnn = "{s['dnn']}";
      nssai_sst = 1;
      nssai_sd = {s['sd']};
    }}
    thread-pool = "-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1"
    rfsimulator = {{
      serveraddr = "10.1.140.204";
    }}
    log_config = {{
      global_log_options = "level,nocolor,time";
    }}
---
apiVersion: v1
kind: Service
metadata:
  name: oai-ue-slice-{sid}-client-1
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: {ue_name}
    slice: "{sid}"
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: {ue_name}
  ports:
  - name: http
    port: 80
    targetPort: 80
    nodePort: {32280 + sid}
  - name: backend
    port: 8090
    targetPort: 8090
    nodePort: {32290 + sid}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oai-ue-slice-{sid}-client-1
  namespace: {NAMESPACE}
  labels:
    app.kubernetes.io/name: {ue_name}
    app.kubernetes.io/part-of: exp4
    ina.lab/scheme: {SCHEME_ID}
    ina.lab/role: ue-client
    ina.lab/slice: "{sid}"
    slice: "{sid}"
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: {ue_name}
  template:
    metadata:
      labels:
        app: {ue_name}
        app.kubernetes.io/name: {ue_name}
        ina.lab/role: ue-client
        ina.lab/slice: "{sid}"
        slice: "{sid}"
      annotations:
        k8s.v1.cni.cncf.io/networks: '[{{"name": "{rf_net}", "interface": "rf", "ips": ["{s['ue_rf']}/24"], "gateways": ["10.1.140.3"]}}, {{"name": "ue{sid}-console-multus", "interface": "net2", "ips": ["{console_ip}/24"], "gateways": ["10.1.137.1"], "mac": "{console_mac}"}}]'
    spec:
      serviceAccountName: {ue_name}-sa
      terminationGracePeriodSeconds: 2
      nodeSelector:
        kubernetes.io/arch: amd64
        kubernetes.io/hostname: usrp
      containers:
      - name: ue
        image: {UE_IMAGE}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
        env:
        - name: USE_ADDITIONAL_OPTIONS
          value: "-r 133 --numerology 1 -C 3325620000 --ssb 144 --rfsim --log_config.global_log_options level,nocolor,time --serveraddr 10.1.140.204"
        - name: TZ
          value: Europe/Paris
        volumeMounts:
        - name: configuration
          mountPath: /opt/oai-nr-ue/etc/nr-ue.conf
          subPath: ue.conf
{ue_ran_resources_yaml()}      - name: app-client
        image: {CLIENT_IMAGES[sid]}
        imagePullPolicy: Always
        securityContext:
          privileged: true
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
        command:
        - bash
        - -c
        - |
          set -euo pipefail
          for p in /exp4/ifaces.sh /app/common/ifaces.sh; do
            if [ -f "$p" ]; then
              . "$p"
              exp4_client_ifaces || true
              break
            fi
          done
          exec /app/entrypoint.sh
        volumeMounts:
        - name: exp4-ifaces
          mountPath: /exp4
          readOnly: true
        - name: exp4-ifaces
          mountPath: /usr/local/bin/exp4_influx_publish.py
          subPath: influx_publish.py
          readOnly: true
        env:
        - name: SLICE_ID
          value: "{sid}"
        - name: CLIENT_INDEX
          value: "1"
        - name: UE_NAME
          value: "{ue_name}"
        - name: CONSOLE_IP
          value: "{console_ip}"
        - name: CONSOLE_MAC
          value: "{console_mac}"
{extra_env(sid)}
{app_client_resources_yaml(sid)}{chromium_sidecar_yaml(ue_name) if sid == 3 else ""}      volumes:
      - name: configuration
        configMap:
          name: {ue_name}-configmap
      - name: exp4-ifaces
        configMap:
          name: exp4-ifaces
{chromium_volumes_yaml() if sid == 3 else ""}
"""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Deploy or undeploy {SCHEME_ID} UEs on {EDGE_CONTEXT}"
    )
    parser.add_argument(
        "ues",
        nargs="*",
        help="UE/slice ids (default: all). Examples: 5  1,2,4  s3  oai-ue-1",
    )
    parser.add_argument(
        "--ue",
        action="append",
        default=[],
        metavar="ID",
        help="UE/slice id (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--undeploy",
        action="store_true",
        help="Remove the selected UE(s) instead of applying them",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sids = parse_ue_ids(list(args.ues) + list(args.ue))
    label = ",".join(str(s) for s in sids)
    if args.undeploy:
        undeploy_ues(namespace=NAMESPACE, context=EDGE_CONTEXT, sids=sids)
        return
    print("=" * 64)
    print(f" Bringing up {SCHEME_ID} UE(s) {label} in {NAMESPACE} on {EDGE_CONTEXT}")
    print("=" * 64)
    out_dir = HERE / "manifests"
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = out_dir / "exp4_s3_ues.yaml"
    parts = [ifaces_configmap_yaml()] + [generate_ue_yaml(sid) for sid in sids]
    combined.write_text("\n".join(parts))
    print(f"  wrote {combined}")
    res = subprocess.run(
        ["kubectl", f"--context={EDGE_CONTEXT}", "apply", "-f", str(combined)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        sys.exit(res.returncode)
    print(res.stdout)
    names = ",".join(f"oai-ue-{sid}" for sid in sids)
    subprocess.run(
        [
            "kubectl",
            f"--context={EDGE_CONTEXT}",
            "delete",
            "pod",
            "-n",
            NAMESPACE,
            "-l",
            f"app.kubernetes.io/name in ({names})",
            "--wait=false",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("Applied. Watch:")
    print(f"  kubectl --context={EDGE_CONTEXT} -n {NAMESPACE} get pods -o wide -w")


if __name__ == "__main__":
    main()
