#!/usr/bin/env python3
"""Bring up five DL UEs + app clients on edge `usrp` for Exp4 S2 (exp4-s0)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))

from ott_chromium import chromium_sidecar_yaml, chromium_volumes_yaml, extra_ott_env  # noqa: E402
from scheme import NAMESPACE, REGISTRY, SCHEME_ID, SLICES  # noqa: E402

EDGE_CONTEXT = "edge@edge"
UE_IMAGE = f"{REGISTRY}/oai-nr-ue:nws-v0.8.2-amd64"

CLIENT_IMAGES = {
    1: "docker.io/nicolaka/netshoot:latest",
    2: f"{REGISTRY}/cctv-ue-console:nws-v0.9-amd64",
    3: f"{REGISTRY}/ott-ue-console:nws-v0.33-amd64",
    4: "docker.io/nicolaka/netshoot:latest",
    5: f"{REGISTRY}/iot-ue-console:nws-v0.10-amd64",
}


def extra_env(sid: int) -> str:
    s = SLICES[sid]
    ip = s["app_ip"]
    common = f"""
        - name: CONSOLE_ROLE
          value: "backend"
        - name: TARGET_SERVER_IP
          value: "{ip}"
        - name: TO_SERVER_IFACE
          value: "oaitun_ue1"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: CONSOLE_IFACE
          value: "net1"
        - name: PDU_ROUTE_HOSTS
          value: "{ip}"
        - name: PDU_WAIT_TIMEOUT
          value: "300"
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
""",
        2: f"""
        - name: RTSP_TARGET_HOST
          value: "{ip}"
        - name: RTSP_PORT
          value: "8554"
""",
        3: f"""
        - name: SERVER_URL
          value: "http://{ip}:80"
"""
        + extra_ott_env(s["ue_console_ip"]),
        4: f"""
        - name: DOWNLOAD_URL
          value: "http://{ip}/download"
""",
        5: f"""
        - name: BROKER_HOST
          value: "{ip}"
        - name: BROKER_PORT
          value: "1883"
        - name: LATENCY_PROBE_PERIOD_S
          value: "1.0"
""",
    }
    return common + extras[sid]


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
        k8s.v1.cni.cncf.io/networks: '[{{"name": "{rf_net}", "interface": "rf", "ips": ["{s['ue_rf']}/24"], "gateways": ["10.1.140.3"]}}, {{"name": "ue{sid}-console-multus", "interface": "net1", "ips": ["{console_ip}/24"], "mac": "{console_mac}"}}]'
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
          value: "-r 133 --numerology 1 -C 3325620000 --ssb 144 --rfsim --log_config.global_log_options level,nocolor,time --rfsimulator.serveraddr 10.1.140.204"
        - name: TZ
          value: Europe/Paris
        volumeMounts:
        - name: configuration
          mountPath: /opt/oai-nr-ue/etc/nr-ue.conf
          subPath: ue.conf
      - name: app-client
        image: {CLIENT_IMAGES[sid]}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
          capabilities:
            add: ["NET_ADMIN", "NET_RAW"]
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
        resources:
          requests: {{cpu: 100m, memory: 128Mi}}
          limits: {{cpu: "1", memory: 1Gi}}
      - name: traffic-tester
        image: docker.io/nicolaka/netshoot:latest
        imagePullPolicy: IfNotPresent
        command: ["sleep", "infinity"]
        env:
        - name: SERVER_URL
          value: "http://{s['app_ip']}:80"
        - name: SLICE_ID
          value: "{sid}"
        securityContext:
          capabilities:
            add: ["NET_ADMIN", "NET_RAW"]
        resources:
          requests: {{cpu: 50m, memory: 64Mi}}
          limits: {{cpu: 200m, memory: 128Mi}}
{chromium_sidecar_yaml(ue_name) if sid == 3 else ""}      volumes:
      - name: configuration
        configMap:
          name: {ue_name}-configmap
{chromium_volumes_yaml() if sid == 3 else ""}
"""


def main() -> None:
    print("=" * 64)
    print(f" Bringing up {SCHEME_ID} UEs in {NAMESPACE} on {EDGE_CONTEXT}")
    print("=" * 64)
    out_dir = HERE / "manifests"
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = out_dir / "exp4_s2_ues.yaml"
    parts = [generate_ue_yaml(sid) for sid in SLICES]
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
    names = ",".join(f"oai-ue-{sid}" for sid in SLICES)
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
