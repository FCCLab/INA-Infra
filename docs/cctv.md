# CCTV (slice 1)

Vision streaming over 5G: UE **publishers** push RTSP RECORD to the analyzer; YOLO annotates frames; [MediaMTX](https://github.com/bluenviron/mediamtx) is the **dashboard** pub/sub path. Source: [`applications/cctv/`](../applications/cctv/).

| | |
|---|---|
| Slice | 1 (eMBB / CCTV) |
| Server cluster | **regional** (PL placement; N6 `10.1.137.161`) |
| Client UEs | **edge `usrp`**, on-demand K8s (not GitOps) |
| Namespace | `ina-infra` |
| Images | `10.1.132.30:5000/slicea-analyzer:nws-v0.7-amd64` · `slicea-publisher:nws-v0.6-amd64` |

## URLs (lab)

| What | URL |
|------|-----|
| Dashboard (hostPort 8080 on regional) | [http://10.1.137.121:8080/](http://10.1.137.121:8080/) |
| Swagger | [http://10.1.137.121:8080/docs](http://10.1.137.121:8080/docs) |
| Grafana CCTV | [http://10.1.137.105:3000/d/ffvbyfvl0i29sd/cctv](http://10.1.137.105:3000/d/ffvbyfvl0i29sd/cctv) (`inainfra` / `inainfra`) |
| InfluxDB | [http://10.1.137.104:8086](http://10.1.137.104:8086) — see [influxdb-grafana.md](influxdb-grafana.md) |

## Data path

```text
UE publisher  --RTSP RECORD-->  gst-rtsp-server :8554
                                      |
                                   YOLO (per-camera process)
                                      |
                              MediaMTX publish :8555 /cam_*
                                      |
         dashboard subscribe  HLS /live/cam_*/index.m3u8  or  WHEP /whep/cam_*
         fallback             MJPEG /video/{client_id}
```

UE ingest stays on GStreamer so RTP NTP-64 / RTCP SR timestamps are not relayed through MediaMTX (M1 `useAbsoluteTimestamp` issues). MediaMTX is **only** backend → frontend video.

GitOps deploys the **server**. Client UEs (`oai-ue-1`, `oai-ue-1-client-N`) are applied on edge via the INA-Infra Applications page (direct kubectl).

## Ports (server pod)

| Port | Role |
|------|------|
| 8554 | UE RTSP RECORD (GStreamer) |
| 8555 | MediaMTX RTSP (internal publish) |
| 8888 | MediaMTX HLS (proxied as `/live/…` on 8080) |
| 8889 | MediaMTX WebRTC / WHEP |
| 8080 | FastAPI + React dashboard (`hostPort`) |
| 9102 | Prometheus metrics |
| 9997 | MediaMTX Control API (localhost) |

N6 macvlan: `10.1.137.161/24` (`app-slice1-multus`). Video wall `hostPort` is the **node** site IP (regional-1 `10.1.137.121`), not the Multus IP.

## API (Swagger)

Base `http://<wall-host>:8080`. Interactive docs: `/docs`. OpenAPI: `/openapi.json`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Analyzer + MediaMTX + frontend present |
| GET | `/api/v1/status` | YOLO flags, MediaMTX paths, all cameras |
| GET | `/api/v1/clients` | Camera list + HLS/WHEP/MJPEG URLs |
| GET | `/live/{path}` | HLS subscribe (proxy to MediaMTX :8888) |
| POST | `/whep/{path}` | WHEP subscribe (proxy to :8889) |
| GET | `/video/{id}` | MJPEG fallback |
| GET | `/snapshot/{id}` | Single JPEG |

## Server image layout

| Component | Path | Role |
|-----------|------|------|
| Analyzer | `applications/cctv/edge/analyzer.py` | RECORD ingest, YOLO, starts FastAPI thread |
| API | `applications/cctv/edge/api.py` | FastAPI / Swagger |
| Publisher | `applications/cctv/edge/mtx_publish.py` | GStreamer `rtspclientsink` → MediaMTX |
| MediaMTX | `applications/cctv/edge/mediamtx.yml` | Pub/sub ([upstream](https://github.com/bluenviron/mediamtx)) |
| Dashboard | `applications/cctv/frontend/` | React (INA-Infra shell) |
| UE client | `applications/cctv/client/publisher.py` | RECORD push over PDU |

Env (selected): `YOLO_PROCESS_PER_CLIENT=true`, `YOLO_DEVICE=cpu`, `FRAME_SKIP=1`, `FRONTEND_DIR=/app/frontend/dist`.

## Build / roll

```bash
cd /home/fcp/INA-Infra/applications/cctv
IMAGE_TAG=nws-v0.7-amd64 PLATFORM=linux/amd64 ./build_push.sh
# Publisher image: BUILD_PUBLISHER=1
```

Then set the profile **server image** and **PL Deploy** (GitOps). Do not `kubectl patch` — RootSync overwrites. Clients: Applications page **Deploy UEs**.

Push GitOps: `./bringup/03_push_to_git_repos/push_git_repos.sh regional`

## Metrics

Analyzer `:9102/metrics` (`cctv_*` / `slicea_*` histograms). Sidecar pushes to Influx `application_metrics`. Grafana dashboard uid `ffvbyfvl0i29sd` (`applications/cctv/dashboard/dashboard.json`).
