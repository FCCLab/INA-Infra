# Application Container Images & Build Source

This directory contains the source code, Dockerfiles, and build/push automation for the application workloads running across network slices.

---

## Directory Overview

| Directory | Slice Mapping | Applications & Roles | Container Images | Build Script |
| :--- | :--- | :--- | :--- | :--- |
| **[`cctv`](cctv/)** | **Slice A** (CCTV Vision Streaming) | • `edge/`: Real-time YOLO object detection analyzer<br>• `client/`: RTSP video publisher stream | • `slicea-analyzer:nws-v0.5-amd64`<br>• `slicea-publisher:nws-v0.5-amd64` | `./build_push.sh` |
| **[`physical_ai`](physical_ai/)** | **Slice B** (Physical AI / VLM) | • `Dockerfile.vllm`: Cosmos VLM server on GH200 (arm64)<br>• `Dockerfile.aiperf`: Benchmark & inference client (amd64) | • `cosmo3-vllm:nws-v0.5-arm64-cu128`<br>• `cosmo3-aiperf:nws-v0.5-amd64` | • `./build-push-aiperf.sh`<br>• `./build-push-vllm-gh82.sh` |
| **[`ott`](ott/)** | **Slice C** (High-Bandwidth eMBB OTT) | • `server/`: RTSP HD video streaming server<br>• `client/`: RTSP client player | • `hd-stream-server:hdstream-v2`<br>• `hd-stream-client:hdstream-v2` | `./build_push.sh` |
| **[`iot`](iot/)** | **Slice D** (Background Best-Effort IoT) | • `edge/`: Mosquitto MQTT broker & downlink controller<br>• `client/`: Synthetic IoT traffic generator | • `sliced-edge:nws-v0.5-amd64`<br>• `sliced-client:nws-v0.5-amd64` | `./build_push.sh` |
| **[`common/console`](common/console/)** | **Common (Slices C & D)** | • Web control console sidecar for OTT / IoT servers | • `ina-control-dashboard:nws-v0.21-amd64` | `./build-push.sh` |

---

## Quick Build & Push Instructions

All images are pushed to the local container registry (`10.1.132.30:5000`) by default.

### 1. Slice A: CCTV Vision Streaming
```bash
cd /home/fcp/INA-Infra/applications/cctv
./build_push.sh
```

### 2. Slice B: Physical AI (Cosmos3 VLM)
```bash
cd /home/fcp/INA-Infra/applications/physical_ai
# Build & push client (amd64)
./build-push-aiperf.sh

# Build & push vLLM server on GH200 node gh82 (arm64)
./build-push-vllm-gh82.sh
```

### 3. Slice C: High-Bandwidth OTT Video Streaming
```bash
cd /home/fcp/INA-Infra/applications/ott
./build_push.sh
```

### 4. Slice D: Background IoT
```bash
cd /home/fcp/INA-Infra/applications/iot
./build_push.sh
```
