# syntax=docker/dockerfile:1
# Exp4 S2: DeepStream 7.1 + DeepStream-Yolo (YOLOv8n) + MediaMTX + FastAPI.
# https://github.com/marcoslucianops/DeepStream-Yolo
#
# Override NGC base if mirrored:
#   docker build --build-arg DS_BASE=10.1.132.30:5000/deepstream:7.1-gc-triton-devel ...

ARG DS_BASE=nvcr.io/nvidia/deepstream:7.1-gc-triton-devel

# --- Export YOLOv8n ONNX with DeepStream-Yolo output head (CPU) ---
FROM python:3.10-slim-bookworm AS yolo-export
WORKDIR /export
# ultralytics → cv2 needs X11/xcb libs even for headless ONNX export
RUN apt-get update && apt-get install -y --no-install-recommends \
      wget \
      ca-certificates \
      libgl1 \
      libglib2.0-0 \
      libxcb1 \
      libx11-6 \
      libxext6 \
      libxrender1 \
      libsm6 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ultralytics==8.3.40 onnx onnxslim onnxruntime onnxscript
COPY ds_configs/export_yoloV8.py /export/export_yoloV8.py
RUN wget -q -O yolov8n.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt \
    && python3 export_yoloV8.py -w yolov8n.pt --dynamic --simplify -s 640 \
    && test -f yolov8n.onnx && test -f labels.txt

FROM bluenviron/mediamtx:1.12.2 AS mtx

FROM ${DS_BASE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app:/app/edge \
    PYTHONUNBUFFERED=1 \
    GST_DEBUG=1 \
    YOLO_BACKEND=deepstream \
    DS_NUM_STREAMS=4 \
    DS_PGIE_CONFIG=/opt/DeepStream-Yolo/config_infer_primary_yoloV8.txt \
    CUDA_VER=12.6 \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    FRONTEND_DIR=/app/frontend/dist \
    DASHBOARD_STATIC=/app/frontend-console/static

RUN <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  curl ca-certificates git make g++ apt-file \
  ffmpeg libavcodec58 libavutil56 libavformat58 libavfilter7 \
  libswscale5 libswresample3 \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  iproute2 iputils-ping iputils-arping chrony
# DeepStream base often marks codec packages "installed" without shipping .so files.
apt-file update
for round in $(seq 1 25); do
  miss=$(env -u LD_LIBRARY_PATH ldd /usr/bin/ffmpeg 2>/dev/null | awk '/not found/{print $1}' | sort -u || true)
  if [ -z "$miss" ]; then
    break
  fi
  echo "ffmpeg missing libs (round $round): $miss"
  pkgs=""
  for lib in $miss; do
    pkg=$(apt-file -l find "$lib" 2>/dev/null | head -1)
    if [ -z "$pkg" ]; then
      echo "no package provides $lib"
      exit 1
    fi
    pkgs="$pkgs $pkg"
  done
  apt-get install -y --no-install-recommends --reinstall $pkgs
done
test -e /usr/lib/x86_64-linux-gnu/libavcodec.so.58
test -e /usr/lib/x86_64-linux-gnu/libavutil.so.56
env -u LD_LIBRARY_PATH /usr/bin/ffmpeg -version
rm -rf /var/lib/apt/lists/*
python3 -m pip install --no-cache-dir --upgrade pip
python3 -m pip install --no-cache-dir \
  fastapi==0.115.6 \
  "uvicorn[standard]==0.32.1" \
  httpx==0.28.1 \
  pydantic==2.10.4 \
  prometheus-client==0.21.1 \
  numpy==1.26.4
EOF

# DeepStream-Yolo custom bbox parser (required for YOLOv8 ONNX).
RUN git clone --depth 1 https://github.com/marcoslucianops/DeepStream-Yolo.git /opt/DeepStream-Yolo \
    && cd /opt/DeepStream-Yolo \
    && CUDA_VER=12.6 make -C nvdsinfer_custom_impl_Yolo clean \
    && CUDA_VER=12.6 make -C nvdsinfer_custom_impl_Yolo \
    && test -f nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so

COPY --from=yolo-export /export/yolov8n.onnx /opt/DeepStream-Yolo/yolov8n.onnx
COPY --from=yolo-export /export/labels.txt /opt/DeepStream-Yolo/labels.txt
COPY ds_configs/config_infer_primary_yoloV8.txt /opt/DeepStream-Yolo/config_infer_primary_yoloV8.txt

COPY --from=mtx /mediamtx /usr/local/bin/mediamtx

WORKDIR /app
RUN mkdir -p /data /app/edge /models /opt/exp4-s2-engines
# A40 TensorRT engine (optional). Baked at build if ds_engines/*.engine exists.
# Not placed under /models: that path is a hostPath mount at runtime.
COPY ds_engines/ /opt/exp4-s2-engines/

COPY common/ /app/common/
COPY __init__.py cctv.py yolo_worker.py state.py api.py mtx_publish.py \
     mediamtx.yml entrypoint.sh influx_publish.py \
     ds_feed.py ds_pipeline.py ds_main.py ds_latency.py \
     /app/edge/
COPY ds_configs/ /app/edge/ds_configs/
COPY frontend-console/ /app/frontend-console/
COPY requirements.txt /app/edge/requirements.txt

RUN chmod +x /app/edge/entrypoint.sh /app/edge/influx_publish.py \
      /app/edge/ds_feed.py /app/edge/ds_pipeline.py /app/edge/ds_main.py \
      /usr/local/bin/mediamtx

# Bake Intel sample clips into /data so boot never hits GitHub (cam3 timed out at runtime).
RUN mkdir -p /data \
 && curl -fL --retry 5 --retry-delay 2 -o /data/classroom.mp4 \
      https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4 \
 && curl -fL --retry 5 --retry-delay 2 -o /data/street.mp4 \
      https://github.com/intel-iot-devkit/sample-videos/raw/master/person-bicycle-car-detection.mp4 \
 && curl -fL --retry 5 --retry-delay 2 -o /data/walking.mp4 \
      https://github.com/intel-iot-devkit/sample-videos/raw/master/face-demographics-walking.mp4 \
 && curl -fL --retry 5 --retry-delay 2 -o /data/car-detection.mp4 \
      https://github.com/intel-iot-devkit/sample-videos/raw/master/car-detection.mp4 \
 && test "$(stat -c%s /data/classroom.mp4)" -gt 10000 \
 && test "$(stat -c%s /data/street.mp4)" -gt 10000 \
 && test "$(stat -c%s /data/walking.mp4)" -gt 10000 \
 && test "$(stat -c%s /data/car-detection.mp4)" -gt 10000

EXPOSE 80 8080 8554 8555 8888 8889 9102 9997
ENTRYPOINT ["/app/edge/entrypoint.sh"]
