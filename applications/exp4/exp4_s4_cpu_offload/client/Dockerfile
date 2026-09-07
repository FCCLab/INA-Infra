# Exp4 slice 4 UE: backend (HTTP download) + frontend console.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DASHBOARD_STATIC=/app/frontend-console/static \
    BACKEND_URL=http://127.0.0.1:8090

RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 \
      iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir fastapi uvicorn

WORKDIR /app
COPY backend/ /app/backend/
COPY frontend-console/ /app/frontend-console/
COPY download.py /app/download.py
COPY influx_publish.py /usr/local/bin/exp4_influx_publish.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /usr/local/bin/exp4_influx_publish.py
RUN chmod +x /app/entrypoint.sh
EXPOSE 80 8090
ENTRYPOINT ["/app/entrypoint.sh"]
