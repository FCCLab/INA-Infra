# Slice D client (simulated IoT fleet) image.
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

COPY client/requirements.txt /app/client/requirements.txt
RUN pip3 install --no-cache-dir -r /app/client/requirements.txt

COPY common/ /app/common/
COPY client/ /app/client/

RUN chmod +x /app/client/entrypoint.sh

ENTRYPOINT ["/app/client/entrypoint.sh"]
