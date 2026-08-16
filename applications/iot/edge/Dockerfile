# Slice D edge image: Mosquitto broker + Python downlink controller in one
# container (supervised by entrypoint.sh). eclipse-mosquitto carries no Python,
# so we start from python:3.12-slim and apt-install the broker.
FROM python:3.12-slim

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      mosquitto \
      mosquitto-clients \
      gettext-base \
      chrony \
      iproute2 \
      curl \
      ca-certificates \
      procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY edge/requirements.txt /app/edge/requirements.txt
RUN pip3 install --no-cache-dir -r /app/edge/requirements.txt

COPY common/ /app/common/
COPY edge/ /app/edge/

RUN chmod +x /app/edge/entrypoint.sh

ENTRYPOINT ["/app/edge/entrypoint.sh"]
