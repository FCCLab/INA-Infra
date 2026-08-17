# CCTV (slice 1)

Vision streaming over 5G: UE **publishers** push RTSP RECORD to the analyzer; YOLO annotates frames; [MediaMTX](https://github.com/bluenviron/mediamtx) is the **dashboard** pub/sub path. Source: [`applications/cctv/`](../applications/cctv/).

| | |
|---|---|
| Slice | 1 (eMBB / CCTV) |
| Server cluster | **regional** (PL placement; Multus N6 `10.1.137.161`) |
| Client UEs | **edge `usrp`**, on-demand K8s (applied via INA-Infra UI) |
| Namespace | `ina-infra` |
| Images | `10.1.132.30:5000/application-cctv:nws-v0.9-amd64`<br>`10.1.132.30:5000/application-cctv-frontend:nws-v0.9-amd64`<br>`10.1.132.30:5000/application-cctv-publisher:nws-v0.9-amd64`<br>`docker.io/bluenviron/mediamtx:1.12.2` |

## URLs (lab)

| What | URL |
|------|-----|
| Video Wall Web Dashboard | [http://10.1.137.120:30080/](http://10.1.137.120:30080/) (NodePort 30080) |
| Swagger API Docs | [http://10.1.137.120:30080/docs](http://10.1.137.120:30080/docs) |
| Grafana CCTV Dashboard | [http://10.1.137.105:3000/d/ffvbyfvl0i29sd/cctv-dashboard?orgId=1&refresh=2s](http://10.1.137.105:3000/d/ffvbyfvl0i29sd/cctv-dashboard?orgId=1&refresh=2s) (`inainfra` / `inainfra`) — other apps: [influxdb-grafana.md](influxdb-grafana.md) |
| InfluxDB | [http://10.1.137.104:8086](http://10.1.137.104:8086) — see [influxdb-grafana.md](influxdb-grafana.md) |

## Data Path & Multi-Container Pod Architecture

```text
[ UE Publisher (5G) ]
        │
(RTSP RECORD :8554 on Multus 10.1.137.161)
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Pod: application-cctv (Regional Cluster)                               │
│                                                                        │
│ 1. [cctv]             GStreamer RECORD Ingest (:8554)                  │
│                       YOLOv8 Inference Engine (multiprocess)           │
│                       FastAPI Engine (:8080, /snapshot, /video)        │
│                       Prometheus Telemetry (:9102)                     │
│                             │ (RTSP Push to localhost:8555)            │
│                             ▼                                          │
│ 2. [mediamtx]         MediaMTX Hub (RTSP :8555, HLS :8888, WHEP :8889) │
│                             │ (HLS fMP4 stream to localhost:8888)      │
│                             ▼                                          │
│ 3. [frontend]         Nginx Web Server (:80 on NodePort 30080)         │
│                       React Video Wall (Auto, 1, 2x2, 3x3, 4x4 Grid)   │
│                       Reverse Proxy to Backend (:8080) & MTX (:8888)   │
│                                                                        │
│ 4. [metrics-exporter] Prometheus Scraper (:9102) -> InfluxDB (:8086)   │
└────────────────────────────────────────────────────────────────────────┘
```

UE ingest stays on GStreamer so RTP NTP-64 / RTCP SR timestamps are not relayed through MediaMTX (avoiding timestamp drift). MediaMTX is **only** used for backend → frontend video streaming.

## Ports & Services

| Port | Service / Container | Scope | Role |
|------|---------------------|-------|------|
| **80** (NodePort 30080) | `frontend` (Nginx) | External | Video Wall Web UI, API reverse proxy, MediaMTX HLS streaming |
| **8554** (NodePort 30160) | `cctv` (GStreamer) | Multus (`10.1.137.161`) | UE RTSP RECORD Ingest (with NTP-64 timestamps) |
| **8080** | `cctv` (FastAPI) | Localhost | REST API, JPEG Snapshots (`/snapshot/{id}`) |
| **8555** | `mediamtx` (RTSP) | Localhost | Annotated RTSP stream push from YOLO workers (`/cam_*`) |
| **8888** | `mediamtx` (HLS) | Localhost | HLS stream remuxer (fMP4 playlist `/live/{path}`) |
| **8889** | `mediamtx` (WebRTC) | Localhost | WebRTC WHEP streaming (`/whep/{path}`) |
| **9102** (NodePort 32431) | `cctv` (Prometheus)| External | Telemetry metrics endpoint (`/metrics`) |
| **9997** | `mediamtx` (API) | Localhost | MediaMTX control & management API |

## Network & Multus Configuration

- **Static Deterministic MAC**: Pinned to `02:42:0a:01:89:a1` on Multus interface `net1` (`10.1.137.161/24`).
- **Gratuitous ARP**: On startup, `entrypoint.sh` executes `arping -c 3 -U -I net1 10.1.137.161` to instantly update switch and router ARP tables.
- **NetworkAttachmentDefinition**: Configured with `"capabilities": {"ips": true, "mac": true}` in `app-slice1-multus`.

## API Endpoints

Base: `http://10.1.137.120:30080`. Interactive docs: `/docs`. OpenAPI: `/openapi.json`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Backend + MediaMTX + Frontend readiness |
| GET | `/api/v1/status` | YOLO flags, MediaMTX active paths, camera telemetry & object detections |
| GET | `/api/v1/connected` | Fast lightweight status of connected clients and stream activity |
| GET | `/api/v1/clients` | Live camera list + HLS/WHEP URLs & latency stats |
| GET | `/live/{path}` | HLS subscribe (proxied to MediaMTX :8888) |
| POST | `/whep/{path}` | WHEP subscribe (proxied to MediaMTX :8889) |
| GET | `/snapshot/{id}` | Single JPEG image with YOLO bounding boxes (`Cache-Control: no-cache`) |

### Latency Measurement & Telemetry Format

Each camera card displays live performance metrics in standard sequence:
$$\text{Net Delay} \longrightarrow \text{Inference Delay} \longrightarrow \text{E2E Delay} \longrightarrow \text{Detected Object(s)}$$

- **Network Delay (`net_delay_ms`)**: Measures uplink transit from UE capture to edge GStreamer appsink via RTP NTP-64 header timestamps. A 30-sample rolling sliding-window baseline filters out host presentation timestamp / clock drift to isolate true 5G radio transit jitter (~5ms–90ms).
- **YOLO Inference Latency (`yolo_delay_ms`)**: Multi-process worker inference execution time per processed frame (~90ms–220ms).
- **End-to-End Latency (`e2e_delay_ms`)**: Calibrated total capture-to-inference display latency ($=\text{net\_delay} + \text{yolo\_delay}$).

## Video Wall Dashboard Features

- **Exclusive MediaMTX Pub/Sub Engine**: High-performance multi-subscriber streaming via HLS fMP4 and WebRTC WHEP. Zero CPU overhead on the Python YOLO backend.
- **On-Demand Viewport Streaming**: React `IntersectionObserver` ensures only visible, active camera feeds decode video in the browser. Inactive or hidden tiles are automatically paused/destroyed.
- **Non-Blocking Telemetry Polling**: Periodic `/api/v1/clients` updates use `AbortController` to cancel in-flight requests, preventing socket queues.
- **Pattern Layout Selector**: `Auto`, `1 (Focus View)`, `2x2`, `3x3`, `4x4`.
- **Camera Sorting & Reordering**: `Sort: Active First`, `Sort: Detections Count`, `Sort: Name`, `Sort: Ingest Order`.
- **Single View Focus**: Instant camera picker dropdown in `1` mode.

## Build and GitOps Deployment

```bash
cd /home/fcp/INA-Infra/applications/cctv
IMAGE_TAG=nws-v0.9-amd64 PLATFORM=linux/amd64 BUILD_PUBLISHER=1 ./build_push.sh
```

Push GitOps repo:
```bash
./bringup/03_push_to_git_repos/push_git_repos.sh
```

