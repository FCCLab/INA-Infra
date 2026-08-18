# Slice D controller only. Mosquitto runs in a dedicated sidecar / compose service.
FROM python:3.12-slim

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      chrony \
      iproute2 \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY edge/requirements.txt /app/edge/requirements.txt
RUN pip3 install --no-cache-dir -r /app/edge/requirements.txt

COPY common/ /app/common/
COPY edge/ /app/edge/

RUN chmod +x /app/edge/entrypoint.sh

ENTRYPOINT ["/app/edge/entrypoint.sh"]
