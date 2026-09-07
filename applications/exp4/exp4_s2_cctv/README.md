# Exp4 slice 2 — DeepStream-Yolo multi-stream (N=4)

Defaults: N6 server `10.1.137.212`, UE console `10.1.137.222`.

## Architecture

1. File feeders publish `raw/cam1..N` into MediaMTX.
2. **DeepStream** + [DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo) (YOLOv8n ONNX + custom bbox parser) detects and OSD.
3. Annotated RTSP published as `annotated/cam1..N`.
4. UE MediaMTX pulls all N paths; console shows a **2×2** WHEP/HLS grid.

`DS_NUM_STREAMS` (default **4**) scales both sides. First boot builds the TensorRT engine under `/models` (can take several minutes on A40).

Legacy Ultralytics path: `YOLO_BACKEND=ultralytics`.

## Build / deploy

```bash
IMAGE_TAG=nws-v0.6-amd64 ./applications/exp4/build_images.sh --push s2
IMAGE_TAG=nws-v0.6-amd64 ./applications/exp4/exp4_deploy.sh s2
```

Needs NGC (or lab mirror) DeepStream base: `nvcr.io/nvidia/deepstream:7.1-gc-triton-devel`.
Override: `DS_BASE=10.1.132.30:5000/deepstream:7.1-gc-triton-devel`.
