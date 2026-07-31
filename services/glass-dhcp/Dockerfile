# Glass UI for ISC DHCP (Akkadius/glass-isc-dhcp)
FROM node:20-bookworm-slim

WORKDIR /opt/glass-isc-dhcp

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Bake application source into the image
ARG GLASS_REPO=https://github.com/Akkadius/glass-isc-dhcp.git
ARG GLASS_REF=master
RUN git clone --depth 1 --branch "${GLASS_REF}" "${GLASS_REPO}" . \
    && mkdir -p logs \
    && chmod u+x bin -R \
    && npm install --omit=dev \
    && npm cache clean --force

# Default paths match ConfigMap mounts in the K8s pod
COPY glass_config.json /opt/glass-isc-dhcp/config/glass_config.json

ENV NODE_ENV=production
EXPOSE 3000
CMD ["node", "./bin/www"]
