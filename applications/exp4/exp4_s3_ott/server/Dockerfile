# Exp4 slice 3: OTT / MediaMTX watch-DL server.
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
      gir1.2-gst-rtsp-server-1.0 \
      libgstrtspserver-1.0-0 \
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
      ffmpeg \
      iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c "import gi; gi.require_version('GstRtspServer', '1.0'); from gi.repository import GstRtspServer"

WORKDIR /app
COPY requirements.txt /app/server/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/server/requirements.txt

COPY common/ /app/common/
COPY __init__.py main.py ott.py api.py state.py youtube_resolver.py server.py mediamtx.yml entrypoint.sh influx_publish.py /app/server/
COPY frontend-console/ /app/frontend-console/
RUN chmod +x /app/server/entrypoint.sh /app/server/influx_publish.py

EXPOSE 80 8080
ENTRYPOINT ["/app/server/entrypoint.sh"]
