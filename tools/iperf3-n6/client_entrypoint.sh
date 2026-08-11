#!/bin/sh
# Resolve IPERF_SERVER, then start client (WS control connects immediately;
# iperf data plane waits for BIND_DEV / PDU inside client.py).
set -eu

IFACE="${BIND_DEV:-oaitun_ue1}"

# Prefer env; else ConfigMap-mounted file from sync_iperf3_n6_server_ip.sh (UPF N3 IP)
SERVER="${IPERF_SERVER:-}"
if [ -z "$SERVER" ] && [ -f /config/server_ip ]; then
  SERVER="$(tr -d '[:space:]' </config/server_ip)"
fi
if [ -z "$SERVER" ] && [ -f /config/n6_server_ip ]; then
  SERVER="$(tr -d '[:space:]' </config/n6_server_ip)"
fi

while [ -z "$SERVER" ]; do
  echo "waiting for IPERF_SERVER or /config/server_ip (UPF N3 address) ..."
  if [ -f /config/server_ip ]; then
    SERVER="$(tr -d '[:space:]' </config/server_ip)"
  elif [ -f /config/n6_server_ip ]; then
    SERVER="$(tr -d '[:space:]' </config/n6_server_ip)"
  fi
  [ -n "$SERVER" ] && break
  sleep 5
done

if ip -4 addr show "$IFACE" 2>/dev/null | grep -q 'inet '; then
  echo "${IFACE} ready: $(ip -4 -br addr show "$IFACE")"
else
  echo "${IFACE} not up yet — WS will declare waiting_pdu; iperf starts after PDU"
fi

echo "starting iperf3 client → ${SERVER} proto=${PROTOCOL:-udp} api=${INA_INFRA_API_URL:-} (influx=${REPORT_INTERVAL:-1}s log=${LOG_INTERVAL:-5}s)"
exec python3 client.py --server "$SERVER" --protocol "${PROTOCOL:-udp}"
