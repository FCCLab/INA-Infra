# Exp4 slice 4: encrypt/zip/scan/LUT then HTTP download.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 \
      iputils-ping \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY server.py entrypoint.sh influx_publish.py connected_clients.py /app/
COPY frontend-console/ /app/frontend-console/
RUN chmod +x /app/entrypoint.sh /app/influx_publish.py
EXPOSE 80 8080
ENTRYPOINT ["/app/entrypoint.sh"]
