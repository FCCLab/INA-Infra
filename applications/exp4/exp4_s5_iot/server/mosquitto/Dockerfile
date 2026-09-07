# Dedicated Eclipse Mosquitto broker for IoT (Slice D) — linux/amd64.
FROM eclipse-mosquitto:2.0.21

USER root
RUN apk add --no-cache mosquitto-clients
COPY mosquitto.conf /mosquitto/config/mosquitto.conf
USER mosquitto

EXPOSE 1883 1884
