FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 \
      iputils-ping \
      coreutils \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY probe.py /app/probe.py

ENTRYPOINT ["python3", "-u", "/app/probe.py"]
