# Exp4 slice 4 UE: same as S1 (SFTP/iperf) of encrypted 1 MB files.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DASHBOARD_STATIC=/app/frontend-console/static \
    BACKEND_URL=http://127.0.0.1:8090

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      iperf3 \
      iproute2 \
      iputils-ping \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages --no-cache-dir fastapi uvicorn paramiko

WORKDIR /app
COPY backend/ /app/backend/
COPY frontend-console/ /app/frontend-console/
COPY influx_publish.py /usr/local/bin/exp4_influx_publish.py
COPY to_server.py /usr/local/bin/to_server.py
COPY ue_control.py /usr/local/bin/ue_control.py
COPY ifaces.sh /app/common/ifaces.sh
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /usr/local/bin/exp4_influx_publish.py
RUN chmod +x /app/entrypoint.sh
EXPOSE 80 8090
ENTRYPOINT ["/app/entrypoint.sh"]
