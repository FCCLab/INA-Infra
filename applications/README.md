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

### 2. Client & Probe Workloads (`clients/`)

| Directory | Role | Description |
| :--- | :--- | :--- |
| **[`clients/rtt_probe`](clients/rtt_probe/)** | Latency Measurement | Round-trip time (RTT) probe utility |
| **[`clients/throughput_statistics`](clients/throughput_statistics/)** | Throughput Analysis | Periodic throughput calculation and telemetry exporter |

---

## Quick Build & Push Instructions

All images are pushed to the local container registry (`10.1.132.30:5000`) by default.

### 1. Slice A: CCTV Vision Streaming
```bash
cd /home/fcp/INA-Infra/applications/servers/cctv
./build_push.sh
```

### 2. Slice B: Physical AI (Cosmos3 VLM)
```bash
cd /home/fcp/INA-Infra/applications/servers/physical_ai
./build-push-dashboard.sh
./build-push-ue-console.sh
```

### 3. Slice C: High-Bandwidth OTT Video Streaming
```bash
cd /home/fcp/INA-Infra/applications/servers/ott
```

### 4. Slice D: Background IoT
```bash
cd /home/fcp/INA-Infra/applications/servers/iot
```
