# Rendered by entrypoint.sh via envsubst. Slice D is an isolated testbed:
# anonymous access, no TLS, no persistence (see README non-goals).

# OTA data-plane listener: real IoT clients connect here over the 5G path.
# ${OTA_BIND_IP} is forced to 0.0.0.0 by the entrypoint when unset (local test).
listener 1883 ${OTA_BIND_IP}
allow_anonymous true

# Loopback listener for the co-located controller (never touches the OTA path).
listener 1884 127.0.0.1
allow_anonymous true

# Best-effort semantics: standing queue so the UE always has DL bytes
# without buffering tens of seconds when the publisher is faster than 5G.
persistence false
queue_qos0_messages true
max_queued_messages 256
max_queued_bytes 4000000
