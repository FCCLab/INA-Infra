FROM python:3.11-slim-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iperf3 \
        iproute2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "websockets>=15,<17"

WORKDIR /app
COPY influx_writer.py server.py client.py client_entrypoint.sh ./
RUN chmod +x server.py client.py client_entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PORT_START=5201 \
    PORT_COUNT=126 \
    IDLE_TIMEOUT=30 \
    REPORT_INTERVAL=1 \
    LOG_INTERVAL=5 \
    BIND_IFACE=n3 \
    N6_IFACE=n3 \
    INFLUX_URL=http://influxdb.influxdb.svc.cluster.local:8086 \
    INFLUX_TOKEN=ina-infra-influxdb-token \
    INFLUX_ORG=ina-infra \
    INFLUX_BUCKET=default \
    PROCESSES=5 \
    BANDWIDTH=50M \
    PROTOCOL=udp \
    INA_INFRA_API_URL=http://10.1.132.200:8082

ENTRYPOINT ["python3"]
CMD ["server.py"]
