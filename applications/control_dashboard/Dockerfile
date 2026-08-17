# Shared OTT / IoT control dashboard sidecar — linux/amd64.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:/root/.local/bin:${PATH} \
    DASHBOARD_STATIC=/app/static \
    APP_KIND=ott

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl python3 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && /root/.local/bin/uv venv --python 3.12 --seed --managed-python /opt/venv \
    && /root/.local/bin/uv pip install --python /opt/venv fastapi uvicorn paho-mqtt

WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
