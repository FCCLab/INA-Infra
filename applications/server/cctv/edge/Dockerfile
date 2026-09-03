# syntax=docker/dockerfile:1
# Slice A edge: MediaMTX + FastAPI dashboard + GStreamer/YOLO analyzer.
#
# Layer order is intentional: apt/pip/torch/YOLO weights are cached unless
# requirements or TORCH_INDEX_URL change. Frontend src and Python src are
# copied last so code edits only rebuild the cheap layers.

FROM node:20-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* /ui/
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund
COPY frontend/ /ui/
RUN npm run build

FROM bluenviron/mediamtx:1.12.2 AS mtx

FROM debian:bookworm-slim AS python-deps

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    GST_DEBUG=1 \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    FRONTEND_DIR=/app/frontend/dist

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-gi \
      python3-gst-1.0 \
      gir1.2-glib-2.0 \
      gir1.2-gstreamer-1.0 \
      gir1.2-gst-plugins-base-1.0 \
      gir1.2-gst-rtsp-server-1.0 \
      libgstrtspserver-1.0-0 \
      gstreamer1.0-tools \
      gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-ugly \
      gstreamer1.0-libav \
      gstreamer1.0-rtsp \
      libgl1 \
      libglib2.0-0 \
      chrony \
      iproute2 \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c "import gi; gi.require_version('GstRtspServer', '1.0'); from gi.repository import GstRtspServer"

WORKDIR /app
COPY edge/requirements.txt /app/edge/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages \
      torch torchvision --extra-index-url "${TORCH_INDEX_URL}" \
    && pip3 install --break-system-packages -r /app/edge/requirements.txt \
    && python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

FROM python-deps
COPY --from=mtx /mediamtx /usr/local/bin/mediamtx
COPY --from=frontend /ui/dist /app/frontend/dist
COPY common/ /app/common/
COPY edge/ /app/edge/
RUN chmod +x /app/edge/entrypoint.sh /usr/local/bin/mediamtx

EXPOSE 8080 8554 8555 8888 8889 9102 9997
ENTRYPOINT ["/app/edge/entrypoint.sh"]
