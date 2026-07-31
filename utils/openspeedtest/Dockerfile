# OpenSpeedTest CLI client for throughput tests against lab OST servers.
# Build/push: ./build_push.sh
#
# Examples:
#   docker run --rm --network host IMAGE --server http://10.1.132.11/ -d 10 -t 1
#   docker run --rm --network host IMAGE --dir download -d 0
#   kubectl run ost-client --rm -it --restart=Never --image=IMAGE -- \
#     --server http://10.1.132.11/ -d 10 --threads 1
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 \
      iputils-ping \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY speedtest.py /app/speedtest.py

ENV PYTHONUNBUFFERED=1 \
    OST_SERVER=http://10.1.132.11/

# Default: mgmt MetalLB OpenSpeedTest VIP (override with args or OST_SERVER).
ENTRYPOINT ["python3", "-u", "/app/speedtest.py"]
CMD ["--server", "http://10.1.132.11/", "--duration", "10", "--threads", "1", "--direction", "both"]
