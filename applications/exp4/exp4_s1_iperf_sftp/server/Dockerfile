# Exp4 slice 1 server: iperf3 + OpenSSH SFTP (queued 1 MB files).
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      iperf3 \
      openssh-server \
      openssl \
      ca-certificates \
      iproute2 \
      iputils-arping \
      iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd /home/ina/download \
    && useradd -m -d /home/ina -s /bin/bash ina \
    && echo 'ina:ina' | chpasswd \
    && chown -R ina:ina /home/ina

WORKDIR /app
RUN pip3 install --break-system-packages --no-cache-dir fastapi uvicorn
COPY make_payload.sh /usr/local/bin/make_payload.sh
COPY influx_publish.py /usr/local/bin/exp4_influx_publish.py
COPY control_api.py connected_clients.py /app/
COPY frontend-console/ /app/frontend-console/
COPY ifaces.sh /app/common/ifaces.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/make_payload.sh /usr/local/bin/exp4_influx_publish.py /entrypoint.sh

EXPOSE 22 80 5201 8080
ENTRYPOINT ["/entrypoint.sh"]
