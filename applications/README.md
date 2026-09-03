# Application Container Images & Build Source

This directory contains the source code, Dockerfiles, and build/push automation for the application workloads running across network slices.

---

## Directory Overview

### 1. Server Workloads (`servers/`)

| Directory | Slice Mapping | Applications & Roles | Container Images | Build Script |
| :--- | :--- | :--- | :--- | :--- |
| **[`servers/cctv`](servers/cctv/)** | **Slice A** (CCTV Vision Streaming) | • `edge/`: Real-time YOLO object detection analyzer<br>• `frontend/`: Web dashboard | • `application-cctv:nws-v0.9-amd64`<br>• `application-cctv-frontend:nws-v0.15-amd64` | `./build_push.sh` |
| **[`servers/physical_ai`](servers/physical_ai/)** | **Slice B** (Physical AI / VLM) | • `Dockerfile.vllm`: Cosmos VLM server (NVIDIA A40/GH200)<br>• `dashboard/`: FastAPI model manager & proxy | • `cosmo3-vllm:nws-v0.7-amd64`<br>• `cosmo3-dashboard:nws-v0.11-amd64` | • `./build-push-vllm.sh`<br>• `./build-push-dashboard.sh` |
| **[`servers/ott`](servers/ott/)** | **Slice C** (High-Bandwidth eMBB OTT) | • `server/`: 4K MediaMTX RTSP streaming server<br>• `frontend/`: React/Nginx video portal | • `application-ott:nws-v0.10-amd64`<br>• `application-ott-frontend:nws-v0.16-amd64` | `./server/Dockerfile`<br>`./frontend/Dockerfile` |
| **[`servers/iot`](servers/iot/)** | **Slice D** (Background Best-Effort IoT) | • `edge/`: Mosquitto MQTT broker & telemetry processor | • `iot-mosquitto:nws-v0.1-amd64`<br>• `sliced-edge:nws-v0.9-amd64` | `edge/build_push.sh` |
| **[`servers/common/console`](servers/common/console/)** | **Common (Slices C & D)** | • Web control console sidecar for OTT / IoT servers | • `ina-control-dashboard:nws-v0.21-amd64` | `./build-push.sh` |
| **[`servers/common/shared`](servers/common/shared/)** | **Common** | • Shared stylesheet tokens and shell layouts | — | — |

### 2. Client & UE Console Workloads (`clients/`)

| Directory | Slice Mapping | Applications & Roles | Container Images | Build Script |
| :--- | :--- | :--- | :--- | :--- |
| **[`clients/cctv`](clients/cctv/)** | **Slice A** (CCTV Vision Streaming) | • `backend/`: Video publisher & stream controller<br>• `frontend-console/`: CCTV UE Console UI | • `cctv-ue-console:nws-v0.9-amd64` | `./build-push.sh` |
| **[`clients/physical_ai`](clients/physical_ai/)** | **Slice B** (Physical AI / VLM) | • `backend/`: Cosmos3 prompt generator & AIPerf<br>• `frontend-console/`: Physical AI UE Console UI | • `cosmo3-ue-console:nws-v0.18-amd64`<br>• `cosmo3-aiperf:nws-v0.5-amd64` | • `./build-push-ue-console.sh`<br>• `./build-push-aiperf.sh` |
| **[`clients/ott`](clients/ott/)** | **Slice C** (High-Bandwidth eMBB OTT) | • `backend/`: Chromium 4K automation & PDU proxy<br>• `frontend-console/`: OTT UE Console UI | • `ott-ue-console:nws-v0.33-amd64` | `./build-push-ue-console.sh` |
| **[`clients/iot`](clients/iot/)** | **Slice D** (Background Best-Effort IoT) | • `backend/`: Synthetic MQTT telemetry publisher<br>• `frontend-console/`: IoT UE Console UI | • `iot-ue-console:nws-v0.10-amd64` | `./build-push-ue-console.sh` |
| **[`clients/rtt_probe`](clients/rtt_probe/)** | **Common** | • Dedicated round-trip time (RTT) probe utility | • `rtt-probe:latest` | `./build_push.sh` |
| **[`clients/throughput_statistics`](clients/throughput_statistics/)** | **Common** | • Real-time throughput statistical exporter | • `throughput-statistics:latest` | `./build_push.sh` |

---

## Quick Build & Push Instructions

All images are pushed to the local container registry (`10.1.132.30:5000`) by default.

### 1. Server Workloads
```bash
# Slice A: CCTV Analyzer & Frontend
cd /home/fcp/INA-Infra/applications/servers/cctv && ./build_push.sh

# Slice B: Physical AI vLLM & Server Dashboard
cd /home/fcp/INA-Infra/applications/servers/physical_ai
./build-push-dashboard.sh
./build-push-vllm.sh

# Slice C: OTT RTSP MediaMTX & Frontend Portal
cd /home/fcp/INA-Infra/applications/servers/ott && ./build_push.sh

# Slice D: IoT Mosquitto Broker & Backend Controller
cd /home/fcp/INA-Infra/applications/servers/iot && ./build_push.sh

# Common: OTT/IoT Control Console Sidecar
cd /home/fcp/INA-Infra/applications/servers/common/console && ./build-push.sh
```

### 2. Client & UE Console Workloads
```bash
# Slice A: CCTV UE Console & Publisher
cd /home/fcp/INA-Infra/applications/clients/cctv && ./build-push.sh

# Slice B: Physical AI UE Console & AIPerf
cd /home/fcp/INA-Infra/applications/clients/physical_ai
./build-push-ue-console.sh
./build-push-aiperf.sh

# Slice C: OTT Chromium UE Console
cd /home/fcp/INA-Infra/applications/clients/ott && ./build-push-ue-console.sh

# Slice D: IoT MQTT UE Console
cd /home/fcp/INA-Infra/applications/clients/iot && ./build-push-ue-console.sh
```
