# syntax=docker/dockerfile:1
# Slice A client (publisher) image.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    GST_DEBUG=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-gi \
      python3-gst-1.0 \
      gir1.2-glib-2.0 \
      gir1.2-gstreamer-1.0 \
      gir1.2-gst-plugins-base-1.0 \
      gstreamer1.0-tools \
      gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-ugly \
      gstreamer1.0-libav \
      gstreamer1.0-rtsp \
      chrony \
      iproute2 \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && gst-inspect-1.0 rtspclientsink > /dev/null

WORKDIR /app
COPY client/requirements.txt /app/client/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages -r /app/client/requirements.txt

COPY common/ /app/common/
COPY client/ /app/client/
COPY data/example.mp4 /data/example.mp4
RUN chmod +x /app/client/entrypoint.sh

ENTRYPOINT ["/app/client/entrypoint.sh"]
