# Exp4 slice 2 — DeepStream-Yolo multi-stream (N=4)

Defaults: N6 server `10.1.137.212`, UE console `10.1.137.222`.

## Architecture

1. File feeders publish `raw/cam1..N` into MediaMTX.
2. **DeepStream** + [DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo) (YOLOv8n ONNX + custom bbox parser) detects and OSD.
3. Annotated RTSP published as `annotated/cam1..N`.
4. UE MediaMTX pulls all N paths; console shows a **2×2** WHEP/HLS grid.

`DS_NUM_STREAMS` (default **4**) scales both sides. TensorRT engines are A40-specific and cannot be compiled in a CPU `docker build`. `build_images.sh` copies an already-built engine from `gpu-a40:/var/lib/ina-infra/exp4-s2-models/model_b1_gpu0_fp16.engine` into the image (`/opt/exp4-s2-engines`). On start it is seeded into `/models` (the hostPath mount would hide a baked `/models` copy). Without that file, first boot still takes ~20 min.

Sample clips (`classroom.mp4`, `street.mp4`, `walking.mp4`, `car-detection.mp4`) are downloaded into `/data` in the **last Dockerfile RUN** so feeders do not hit GitHub at boot.

## Latency

Application E2E (Grafana `latency_ms`) is the **mean across all cameras**. Each stream has its own stamps; the client averages them:

| Stage | What it measures |
| :--- | :--- |
| `camera_ms_i` | raw frame into DeepStream (after camera/MTX) until YOLO starts |
| `yolo_ms_i` | nvinfer |
| `rtsp_hls_ms_i` | annotated encode + RTSP over `net1` + LL-HLS part (console) |

`e2e_i = camera_ms_i + yolo_ms_i + rtsp_hls_ms_i`. Grafana `latency_ms = mean(e2e_i)`. `t_send` is that stream’s mux wall clock **before** YOLO; `t_recv` is its annotated frame on the UE.

Legacy Ultralytics path: `YOLO_BACKEND=ultralytics`.

## Build / deploy

```bash
IMAGE_TAG=nws-v0.21-amd64 ./applications/exp4/build_images.sh --push s2
IMAGE_TAG=nws-v0.21-amd64 ./applications/exp4/exp4_deploy.sh s2
```

Needs NGC (or lab mirror) DeepStream base: `nvcr.io/nvidia/deepstream:7.1-gc-triton-devel`.
Override: `DS_BASE=10.1.132.30:5000/deepstream:7.1-gc-triton-devel`.
