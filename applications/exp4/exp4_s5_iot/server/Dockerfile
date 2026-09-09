# Exp4 slice 5 server: Mosquitto (:1883 OTA, :1884 local) + DL controller.
FROM python:3.12-slim

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      mosquitto \
      mosquitto-clients \
      chrony \
      iproute2 \
      curl \
      ca-certificates \
      iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/edge/requirements.txt
RUN pip3 install --no-cache-dir -r /app/edge/requirements.txt

COPY common/ /app/common/
COPY controller.py entrypoint.sh mosquitto.conf.tpl influx_publish.py control_api.py dl_runtime.py connected_clients.py mqtt_tx_latency.py /app/edge/
COPY frontend-console/ /app/frontend-console/
COPY mosquitto/mosquitto.conf /etc/mosquitto/mosquitto.conf
RUN chmod +x /app/edge/entrypoint.sh /app/edge/influx_publish.py

EXPOSE 80 8080 1883 1884
ENTRYPOINT ["/app/edge/entrypoint.sh"]
