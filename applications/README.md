# Application Container Images & Build Source

This directory contains the source code, Dockerfiles, and build/push automation for the application workloads running across network slices.

---

## Directory Overview

### 1. Server Workloads (`server/`)

| Directory | Slice Mapping | Applications & Roles | Container Images | Build Script |
| :--- | :--- | :--- | :--- | :--- |
| **[`server/cctv`](server/cctv/)** | **Slice A** (CCTV Vision Streaming) | • `edge/`: Real-time YOLO object detection analyzer<br>• `frontend/`: Web dashboard | • `application-cctv:nws-v0.9-amd64`<br>• `application-cctv-frontend:nws-v0.15-amd64` | `./build_push.sh` |
| **[`server/physical_ai`](server/physical_ai/)** | **Slice B** (Physical AI / VLM) | • `Dockerfile.vllm`: Cosmos VLM server (NVIDIA A40/GH200)<br>• `dashboard/`: FastAPI model manager & proxy | • `cosmo3-vllm:nws-v0.7-amd64`<br>• `cosmo3-dashboard:nws-v0.11-amd64` | • `./build-push-vllm.sh`<br>• `./build-push-dashboard.sh` |
| **[`server/ott`](server/ott/)** | **Slice C** (High-Bandwidth eMBB OTT) | • `server/`: 4K MediaMTX RTSP streaming server<br>• `frontend/`: React/Nginx video portal | • `application-ott:nws-v0.10-amd64`<br>• `application-ott-frontend:nws-v0.16-amd64` | `./server/Dockerfile`<br>`./frontend/Dockerfile` |
| **[`server/iot`](server/iot/)** | **Slice D** (Background Best-Effort IoT) | • `edge/`: Mosquitto MQTT broker & telemetry processor | • `iot-mosquitto:nws-v0.1-amd64`<br>• `sliced-edge:nws-v0.9-amd64` | `edge/build_push.sh` |
| **[`server/common/console`](server/common/console/)** | **Common (Slices C & D)** | • Web control console sidecar for OTT / IoT servers | • `ina-control-dashboard:nws-v0.21-amd64` | `./build-push.sh` |
| **[`server/common/shared`](server/common/shared/)** | **Common** | • Shared stylesheet tokens and shell layouts | — | — |

### 2. Client & Probe Workloads (`client/`)

| Directory | Role | Description |
| :--- | :--- | :--- |
| **[`client/rtt_probe`](client/rtt_probe/)** | Latency Measurement | Round-trip time (RTT) probe utility |
| **[`client/throughput_statistics`](client/throughput_statistics/)** | Throughput Analysis | Periodic throughput calculation and telemetry exporter |

---

## Quick Build & Push Instructions

All images are pushed to the local container registry (`10.1.132.30:5000`) by default.

### 1. Slice A: CCTV Vision Streaming
```bash
cd /home/fcp/INA-Infra/applications/server/cctv
./build_push.sh
```

### 2. Slice B: Physical AI (Cosmos3 VLM)
```bash
cd /home/fcp/INA-Infra/applications/server/physical_ai
./build-push-dashboard.sh
./build-push-ue-console.sh
```

### 3. Slice C: High-Bandwidth OTT Video Streaming
```bash
cd /home/fcp/INA-Infra/applications/server/ott
```

### 4. Slice D: Background IoT
```bash
cd /home/fcp/INA-Infra/applications/server/iot
```
