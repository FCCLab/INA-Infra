"""Baked Exp4 server Deployments for scheme GitOps (namespace from scheme)."""

from __future__ import annotations

from pathlib import Path

import generate_gitops_lib as g

IFACES_SH = Path(__file__).resolve().parents[3] / "applications" / "exp4" / "common" / "ifaces.sh"
INFLUX_PY = IFACES_SH.parent / "influx_publish.py"
MOSQUITTO_CONF = (
    Path(__file__).resolve().parents[3]
    / "applications"
    / "exp4"
    / "exp4_s5_iot"
    / "server"
    / "mosquitto"
    / "mosquitto.conf"
)
SERVER_ENTRYPOINT = {
    1: "/entrypoint.sh",
    2: "/app/edge/entrypoint.sh",
    3: "/app/server/entrypoint.sh",
    4: "/entrypoint.sh",
    5: "/app/edge/entrypoint.sh",
}

REGISTRY = "10.1.132.30:5000"
# Burst cap on every app server. Requests stay at scheme cpu_app/mem_app so
# five pods still schedule; limits must be >= those requests (CCTV is 12Gi).
SERVER_CPU_LIMIT = "8"
SERVER_MEM_LIMIT_MIB = 8 * 1024


def _mem_to_mib(text: str) -> float:
    raw = str(text).strip().upper()
    if raw.endswith("GI"):
        return float(raw[:-2]) * 1024.0
    if raw.endswith("G"):
        return float(raw[:-1]) * 1024.0
    if raw.endswith("MI"):
        return float(raw[:-2])
    if raw.endswith("M"):
        return float(raw[:-1])
    return float(raw)


def _server_cpu_limit(cpu_app: object) -> str:
    try:
        req = float(cpu_app)
    except (TypeError, ValueError):
        req = 0.0
    cap = float(SERVER_CPU_LIMIT)
    return str(int(cap)) if req <= cap else str(cpu_app)


def _server_mem_limit(mem_app: str) -> str:
    if _mem_to_mib(mem_app) > SERVER_MEM_LIMIT_MIB:
        return str(mem_app)
    return "8Gi"

BAKED_APPS = {
    1: {
        "name": "application-iperf-sftp",
        "app_type": "iperf-sftp",
        "image": "exp4-s1-iperf-sftp-server",
        "tag": "nws-v0.30-amd64",
        "exp4_type": "exp4-s1",
        "ports": (("http", 80), ("sftp", 22), ("iperf", 5201), ("api", 8080)),
    },
    2: {
        "name": "application-cctv",
        "app_type": "cctv",
        "image": "exp4-s2-cctv-server",
        "tag": "nws-v0.30-amd64",
        "exp4_type": "exp4-s2",
        "ports": (("http", 80), ("api", 8080), ("rtsp", 8554), ("mtx-rtsp", 8555), ("iperf", 5201)),
    },
    3: {
        "name": "application-ott",
        "app_type": "ott",
        "image": "exp4-s3-ott-server",
        "tag": "nws-v0.30-amd64",
        "exp4_type": "exp4-s3",
        "ports": (("http", 80), ("api", 8080), ("mtx-rtsp", 8555), ("iperf", 5201)),
    },
    4: {
        "name": "application-cpu-offload",
        "app_type": "cpu-offload",
        "image": "exp4-s4-cpu-offload-server",
        "tag": "nws-v0.30-amd64",
        "exp4_type": "exp4-s4",
        "ports": (("http", 80), ("sftp", 22), ("iperf", 5201), ("api", 8080)),
    },
    5: {
        "name": "application-iot",
        "app_type": "iot",
        "image": "exp4-s5-iot-server",
        "tag": "nws-v0.30-amd64",
        "exp4_type": "exp4-s5",
        "ports": (("http", 80), ("api", 8080), ("mqtt", 1883), ("mqtt-local", 1884), ("iperf", 5201)),
    },
}


def _extra_server_env(sid: int) -> str:
    blocks = {
        1: """        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
""",
        2: """        - name: HTTP_PORT
          value: "8080"
        - name: RTSP_PORT
          value: "8554"
        - name: START_MEDIAMTX
          value: "true"
        - name: YOLO_BACKEND
          value: "deepstream"
        - name: YOLO_ENABLED
          value: "true"
        - name: DS_NUM_STREAMS
          value: "4"
        - name: YOLO_DEVICE
          value: "cuda:0"
""",
        3: """        - name: HTTP_PORT
          value: "8080"
        - name: START_MEDIAMTX
          value: "true"
""",
        4: """        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
        - name: SFTP_AUTOSTART
          value: "1"
        - name: EXP4_PBKDF2_ITERS
          value: "80000"
""",
        5: """        - name: LOCAL_BROKER_HOST
          value: "127.0.0.1"
        - name: LOCAL_BROKER_PORT
          value: "1884"
        - name: DL_MSGS_PER_S
          value: "3000"
        - name: DL_PAYLOAD_BYTES
          value: "128"
        - name: MQTT_MAX_QUEUED
          value: "256"
        - name: DL_DEVICE_IDS
          value: "ue1"
""",
    }
    return blocks.get(sid, "")


def _indent_cm(body: str) -> str:
    return "\n".join(f"    {line}" if line else "" for line in body.splitlines())


def _ifaces_configmap() -> str:
    ifaces = _indent_cm(IFACES_SH.read_text())
    influx = _indent_cm(INFLUX_PY.read_text())
    mosq = _indent_cm(MOSQUITTO_CONF.read_text()) if MOSQUITTO_CONF.exists() else ""
    mosq_block = f"  mosquitto.conf: |\n{mosq}\n" if mosq else ""
    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: exp4-ifaces
  namespace: {g.NAMESPACE}
data:
  ifaces.sh: |
{ifaces}
  influx_publish.py: |
{influx}
{mosq_block}"""


def _write_baked_app(sid: int) -> None:
    spec = BAKED_APPS[sid]
    s = g.SLICES[sid]
    name = spec["name"]
    repo = g.REPO_FOR[s["app"]]
    image = f"{REGISTRY}/{spec['image']}:{spec['tag']}"
    svc_ports = "\n".join(
        f"  - name: {pname}\n    port: {port}\n    targetPort: {port}" for pname, port in spec["ports"]
    )
    ctr_ports = "\n".join(f"        - containerPort: {port}" for _n, port in spec["ports"])
    gpu = g._uses_gpu(sid)
    runtime = "      runtimeClassName: nvidia\n" if gpu else ""
    gpu_res = '\n            nvidia.com/gpu: "1"' if gpu else ""
    node_host = "\n        kubernetes.io/hostname: gpu-a40" if gpu else ""
    master = g._multus_master(sid)
    extra_mounts = """        - name: exp4-ifaces
          mountPath: /app/edge/influx_publish.py
          subPath: influx_publish.py
        - name: exp4-ifaces
          mountPath: /app/server/influx_publish.py
          subPath: influx_publish.py
        - name: exp4-ifaces
          mountPath: /influx_publish.py
          subPath: influx_publish.py
"""
    extra_vols = ""
    iperf_sidecar = ""
    if sid == 2:
        extra_mounts += """        - name: ds-models
          mountPath: /models
"""
        extra_vols = """      - name: ds-models
        hostPath:
          path: /var/lib/ina-infra/exp4-s2-models
          type: DirectoryOrCreate
"""
    if sid == 5:
        extra_mounts += """        - name: exp4-ifaces
          mountPath: /etc/mosquitto/mosquitto.conf
          subPath: mosquitto.conf
"""
    if sid in (2, 3, 5):
        iperf_sidecar = """      - name: iperf
        image: docker.io/networkstatic/iperf3:latest
        imagePullPolicy: IfNotPresent
        args: ["-s", "-p", "5201"]
        ports:
        - containerPort: 5201
"""
    entrypoint = SERVER_ENTRYPOINT[sid]
    cpu_lim = _server_cpu_limit(s["cpu_app"])
    mem_lim = _server_mem_limit(s["mem_app"])
    g.write_text(
        g.ns_dir(repo) / f"60-app-{sid}-app-slice{sid}-multus-networkattachmentdefinition.yaml",
        g._multus_nad(sid),
    )
    g.write_text(
        g.ns_dir(repo) / f"60-app-{sid}-{name}.yaml",
        f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {name}
  namespace: {g.NAMESPACE}
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {g.NAMESPACE}
  labels:
    app.kubernetes.io/name: {name}
    ina.lab/slice: '{sid}'
spec:
  selector:
    app.kubernetes.io/name: {name}
  ports:
{svc_ports}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {g.NAMESPACE}
  labels: &id001
    app.kubernetes.io/name: {name}
    app.kubernetes.io/part-of: {g.PART_OF}
    ina.lab/slice: '{sid}'
    ina.lab/app-type: {spec['app_type']}
    ina.lab/role: server
    ina.lab/scheme: {g.SCHEME_ID}
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
        k8s.v1.cni.cncf.io/networks: '[{{"name": "app-slice{sid}-multus", "interface": "net1", "ips": ["{s['app_ip']}/24"], "gateways": ["10.1.137.1"], "mac": "{s['app_mac']}"}}]'
    spec:
{runtime}      serviceAccountName: {name}
      nodeSelector:
        kubernetes.io/arch: amd64
        ina-infra.nephio.lab/multus-master: {master}{node_host}
      containers:
      - name: app
        image: {image}
        imagePullPolicy: Always
        securityContext:
          capabilities:
            add: ["NET_ADMIN", "NET_RAW"]
        command:
        - bash
        - -c
        - |
          set -euo pipefail
          for p in /exp4/ifaces.sh /app/common/ifaces.sh; do
            if [ -f "$p" ]; then
              . "$p"
              exp4_server_ifaces || true
              break
            fi
          done
          if [ -f /exp4/influx_publish.py ]; then
            # Always run ConfigMap publisher (subPath mounts go stale).
            export EXP4_METRICS_ORIGIN="${{EXP4_METRICS_ORIGIN:-server}}"
            python3 /exp4/influx_publish.py &
          fi
          exec {entrypoint}
        env:
{g._server_influx_env(sid, spec['exp4_type'], s['app'], name)}{_extra_server_env(sid)}        ports:
{ctr_ports}
        volumeMounts:
        - name: exp4-ifaces
          mountPath: /exp4
          readOnly: true
{extra_mounts}        resources:
          requests:
            cpu: "{s['cpu_app']}"
            memory: {s['mem_app']}{gpu_res}
          limits:
            cpu: "{cpu_lim}"
            memory: {mem_lim}{gpu_res}
{iperf_sidecar}      volumes:
      - name: exp4-ifaces
        configMap:
          name: exp4-ifaces
{extra_vols}""",
    )


def write_baked_apps() -> None:
    repos = {g.REPO_FOR[g.SLICES[sid]["app"]] for sid in BAKED_APPS if sid in g.SLICES}
    for repo in repos:
        g.write_text(g.ns_dir(repo) / "60-app-0-exp4-ifaces-configmap.yaml", _ifaces_configmap())
    for sid in BAKED_APPS:
        if sid in g.SLICES:
            _write_baked_app(sid)
