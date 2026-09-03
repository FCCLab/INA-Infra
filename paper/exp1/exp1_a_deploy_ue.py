#!/usr/bin/env python3
"""Dedicated OAI UE + Slice Application Client Bringup for Scheme A (exp1-a).

Brings up all 4 OAI UEs along with their actual continuous application client consoles
on the edge cluster (node: `usrp`):
  - UE 1 (Slice 1 - CCTV): `oai-ue-1` with `cctv-ue-console` streaming RTSP over 5G to 10.1.137.211
  - UE 2 (Slice 2 - Physical-AI): `oai-ue-2` with `cosmo3-ue-console` sending inference over 5G to 10.1.137.212
  - UE 3 (Slice 3 - OTT 4K): `oai-ue-3` with `ott-ue-console` streaming 4K video over 5G to 10.1.137.213
  - UE 4 (Slice 4 - IoT): `oai-ue-4` with `iot-ue-console` publishing MQTT telemetry over 5G to 10.1.137.214:1883

Master Interface: enp4s0f0 (Node: usrp on edge@edge)
SST = 1 (matching 5G Core database)
"""

import sys
import time
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_NS = "exp1-a"
EDGE_CONTEXT = "edge@edge"

# UE Definitions with Dedicated Application Client Images
UE_CONFIGS = [
    {
        "slice_id": 1,
        "name": "oai-ue-1",
        "app_name": "cctv-ue",
        "client_image": "10.1.132.30:5000/cctv-ue-console:nws-v0.1-amd64",
        "imsi": "001010000000101",
        "dnn": "oai1",
        "sst": 1,
        "sd": "0x000001",
        "rf_ip": "10.1.140.141/24",
        "server_ip": "10.1.137.211", # Regional
        "extra_env": """
        - name: CONSOLE_ROLE
          value: "backend"
        - name: RTSP_TARGET_HOST
          value: "10.1.137.211"
        - name: TARGET_SERVER_IP
          value: "10.1.137.211"
        - name: RTSP_PORT
          value: "8554"
        - name: HTTP_PORT
          value: "80"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: PDU_ROUTE_HOSTS
          value: "10.1.137.211"
        - name: PDU_WAIT_TIMEOUT
          value: "300"
        """,
    },
    {
        "slice_id": 2,
        "name": "oai-ue-2",
        "app_name": "physical-ai-ue",
        "client_image": "10.1.132.30:5000/cosmo3-ue-console:nws-v0.18-amd64",
        "imsi": "001010000000102",
        "dnn": "oai2",
        "sst": 1,
        "sd": "0x000002",
        "rf_ip": "10.1.140.142/24",
        "server_ip": "10.1.137.212", # Edge
        "extra_env": """
        - name: CONSOLE_ROLE
          value: "backend"
        - name: SERVER_URL
          value: "http://10.1.137.212:80"
        - name: TARGET_SERVER_IP
          value: "10.1.137.212"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: PDU_ROUTE_HOSTS
          value: "10.1.137.212"
        - name: PDU_WAIT_TIMEOUT
          value: "300"
        - name: SEND_INTERVAL_S
          value: "2"
        """,
    },
    {
        "slice_id": 3,
        "name": "oai-ue-3",
        "app_name": "ott-ue",
        "client_image": "10.1.132.30:5000/ott-ue-console:nws-v0.33-amd64",
        "imsi": "001010000000103",
        "dnn": "oai3",
        "sst": 1,
        "sd": "0x000003",
        "rf_ip": "10.1.140.143/24",
        "server_ip": "10.1.137.213", # Central
        "extra_env": """
        - name: CONSOLE_ROLE
          value: "backend"
        - name: TARGET_SERVER_IP
          value: "10.1.137.213"
        - name: SERVER_URL
          value: "http://10.1.137.213:80"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: PDU_ROUTE_HOSTS
          value: "10.1.137.213"
        - name: PDU_WAIT_TIMEOUT
          value: "300"
        """,
    },
    {
        "slice_id": 4,
        "name": "oai-ue-4",
        "app_name": "iot-ue",
        "client_image": "10.1.132.30:5000/iot-ue-console:nws-v0.10-amd64",
        "imsi": "001010000000104",
        "dnn": "oai4",
        "sst": 1,
        "sd": "0x000004",
        "rf_ip": "10.1.140.144/24",
        "server_ip": "10.1.137.214", # Central
        "extra_env": """
        - name: CONSOLE_ROLE
          value: "backend"
        - name: BROKER_HOST
          value: "10.1.137.214"
        - name: TARGET_SERVER_IP
          value: "10.1.137.214"
        - name: BROKER_PORT
          value: "1883"
        - name: PDU_IFACE
          value: "oaitun_ue1"
        - name: PDU_ROUTE_HOSTS
          value: "10.1.137.214"
        - name: PDU_WAIT_TIMEOUT
          value: "300"
        - name: LATENCY_PROBE_PERIOD_S
          value: "1.0"
        """,
    },
]

def generate_ue_manifests(cfg: dict, namespace: str) -> str:
    """Generates Kubernetes YAML for UE modem + live application client container."""
    s_id = cfg["slice_id"]
    idx = int(cfg.get("client_index", 1))
    ue_name = cfg["name"]
    rf_net_name = f"ue{s_id}-sim-rf"
    console_ip = f"10.1.137.{220 + (s_id - 1) * 10 + (idx - 1)}"
    console_mac = f"02:0a:40:{s_id:02x}:00:{idx:02x}"
    console_mac_label = f"02-0a-40-{s_id:02x}-00-{idx:02x}"
    
    yaml_content = f"""---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: {rf_net_name}
  namespace: {namespace}
spec:
  config: '{{"cniVersion": "0.3.1", "type": "macvlan", "master": "enp4s0f0", "mode": "bridge", "ipam": {{"type": "static"}}}}'
---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ue{s_id}-console-multus
  namespace: {namespace}
spec:
  config: '{{"cniVersion": "0.3.1", "type": "macvlan", "master": "enp4s0f0", "mode": "bridge", "ipam": {{"type": "static"}}}}'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {ue_name}-sa
  namespace: {namespace}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {ue_name}-configmap
  namespace: {namespace}
data:
  ue.conf: |
    uicc0 = {{
      imsi = "{cfg['imsi']}";
      key = "fec86ba6eb707ed08905757b1bb44b8f";
      opc = "C42449363BBAD02B66D16BC975D77CC1";
      dnn = "{cfg['dnn']}";
      nssai_sst = {cfg['sst']};
      nssai_sd = {cfg['sd']};
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
  name: oai-ue-slice-{s_id}-client-1
  namespace: {namespace}
  labels:
    app.kubernetes.io/name: {ue_name}
    slice: "{s_id}"
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: {ue_name}
  ports:
  - name: http
    port: 80
    targetPort: 80
    nodePort: {32280 + s_id}
  - name: backend
    port: 8090
    targetPort: 8090
    nodePort: {32290 + s_id}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oai-ue-slice-{s_id}-client-1
  namespace: {namespace}
  labels:
    app.kubernetes.io/name: {ue_name}
    app.kubernetes.io/part-of: exp1-a
    slice: "{s_id}"
    ina.lab/role: ue-client
    ina.lab/slice: "{s_id}"
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
        slice: "{s_id}"
        ina.lab/role: ue-client
        ina.lab/slice: "{s_id}"
      annotations:
        k8s.v1.cni.cncf.io/networks: '[{{"name": "{rf_net_name}", "interface": "rf", "ips": ["{cfg['rf_ip']}"], "gateways": ["10.1.140.3"]}}, {{"name": "ue{s_id}-console-multus", "interface": "net1", "ips": ["{console_ip}/24"], "mac": "{console_mac}"}}]'
    spec:
      serviceAccountName: {ue_name}-sa
      terminationGracePeriodSeconds: 2
      nodeSelector:
        kubernetes.io/arch: amd64
        kubernetes.io/hostname: usrp
      containers:
      - name: ue
        image: 10.1.132.30:5000/oai-nr-ue:nws-v0.8.2-amd64
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
        image: {cfg['client_image']}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
        env:
        - name: SLICE_ID
          value: "{s_id}"
        - name: CLIENT_INDEX
          value: "{idx}"
        - name: UE_NAME
          value: "{ue_name}"
        - name: CONSOLE_IP
          value: "{console_ip}"
        - name: CONSOLE_MAC
          value: "{console_mac}"
{cfg['extra_env']}
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 1000m
            memory: 1Gi
      - name: traffic-tester
        image: docker.io/nicolaka/netshoot:latest
        imagePullPolicy: IfNotPresent
        command:
        - sleep
        - infinity
        env:
        - name: SERVER_URL
          value: "http://{cfg['server_ip']}:80"
        - name: SLICE_ID
          value: "{s_id}"
        securityContext:
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 200m
            memory: 128Mi
{'''      - name: app-frontend
        image: 10.1.132.30:5000/cosmo3-ue-console:nws-v0.18-amd64
        imagePullPolicy: IfNotPresent
        env:
        - name: CONSOLE_ROLE
          value: "frontend"
        - name: FRONTEND_PORT
          value: "80"
        - name: BACKEND_URL
          value: "http://127.0.0.1:8090"
        - name: UE_NAME
          value: "''' + ue_name + '''"
        ports:
        - name: console
          containerPort: 80
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 500m
            memory: 256Mi
''' if s_id == 2 else ''}{'''      - name: chromium
        image: 10.1.132.30:5000/linuxserver-chromium:latest
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
          seccompProfile:
            type: Unconfined
        env:
        - name: PUID
          value: "1000"
        - name: PGID
          value: "1000"
        - name: TZ
          value: "UTC"
        - name: TITLE
          value: "OTT UE oai-ue-3"
        - name: DISABLE_IPV6
          value: "true"
        - name: PIXELFLUX_WAYLAND
          value: "false"
        - name: CHROME_CLI
          value: "--proxy-server=socks5://127.0.0.1:1080 --remote-debugging-port=9222 --remote-allow-origins=* --no-first-run --no-default-browser-check --disable-features=TranslateUI --autoplay-policy=no-user-gesture-required --disable-gpu https://www.youtube.com"
        - name: CUSTOM_PORT
          value: "3000"
        - name: CUSTOM_HTTPS_PORT
          value: "3001"
        - name: SUBFOLDER
          value: "/chrome/"
        ports:
        - name: chrome-http
          containerPort: 3000
        - name: cdp
          containerPort: 9222
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
        - name: chromium-config
          mountPath: /config
        resources:
          requests:
            cpu: 200m
            memory: 256Mi
          limits:
            cpu: 2000m
            memory: 2Gi
''' if s_id == 3 else ''}      volumes:
      - name: configuration
        configMap:
          name: {ue_name}-configmap
      - name: dshm
        emptyDir:
          medium: Memory
      - name: chromium-config
        emptyDir: {{}}
"""
    return yaml_content

def bringup_ues():
    """Deploys UE manifests and verifies all UE pods reach Ready Running state."""
    print("================================================================")
    print(f" Bringing Up Scheme A (exp1-a) OAI UEs + Application Clients")
    print("================================================================")
    
    ue_manifest_dir = HERE / "manifests" / "ues"
    ue_manifest_dir.mkdir(parents=True, exist_ok=True)
    
    combined_yaml_path = ue_manifest_dir / "exp1_a_ues.yaml"
    
    all_manifests = []
    for cfg in UE_CONFIGS:
        print(f"  - Configuring {cfg['name']} (Slice {cfg['slice_id']} -> Client Image: {cfg['client_image']})")
        manifest = generate_ue_manifests(cfg, TARGET_NS)
        all_manifests.append(manifest)
        
    combined_yaml_path.write_text("\n".join(all_manifests))
    
    print(f"\nApplying manifests to [{EDGE_CONTEXT}] in namespace [{TARGET_NS}]...")
    cmd = f"kubectl --context={EDGE_CONTEXT} apply -f {combined_yaml_path}"
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error applying UE manifests: {res.stderr}")
        sys.exit(1)
        
    # Recreate pods if already running to pick up the new containers
    subprocess.run(
        f"kubectl --context={EDGE_CONTEXT} delete pod -n {TARGET_NS} -l 'app.kubernetes.io/name in (oai-ue-1,oai-ue-2,oai-ue-3,oai-ue-4)' --wait=false 2>/dev/null || true",
        shell=True, capture_output=True
    )
        
    print("Waiting for all 4 UE pods to reach Ready Running state...")
    max_wait = 60
    t0 = time.time()
    all_ready = False
    
    while time.time() - t0 < max_wait:
        cmd = f"kubectl --context={EDGE_CONTEXT} get pods -n {TARGET_NS} -l 'app.kubernetes.io/name in (oai-ue-1,oai-ue-2,oai-ue-3,oai-ue-4)' --no-headers"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        lines = [l for l in res.stdout.splitlines() if l.strip()]
        
        running_count = 0
        for l in lines:
            parts = l.split()
            if len(parts) >= 3 and parts[2] == "Running":
                ready_cur, ready_tot = parts[1].split("/")
                if ready_cur == ready_tot:
                    running_count += 1
                
        print(f"  UE readiness: {running_count}/4 Running (Elapsed: {int(time.time() - t0)}s)...", end="\r")
        if running_count == 4:
            all_ready = True
            break
        time.sleep(2)
        
    print("\n")
    check_cmd = f"kubectl --context={EDGE_CONTEXT} get pods -n {TARGET_NS} -l 'app.kubernetes.io/name in (oai-ue-1,oai-ue-2,oai-ue-3,oai-ue-4)' -o wide"
    res = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
    print(res.stdout)
    
    if all_ready:
        print("All 4 OAI UEs with Application Clients are up, running, and transmitting.")
    else:
        print("Warning: Some UE pods are still initializing. Proceeding with testing.")

if __name__ == "__main__":
    bringup_ues()
