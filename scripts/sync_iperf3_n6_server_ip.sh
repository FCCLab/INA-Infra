#!/usr/bin/env bash
# Publish UPF N3 IP to the UE iperf3-client (env + ConfigMap).
# iperf3 server binds to UPF n3; N3 is static Multus (unlike N6 DHCP).
#
#   ./scripts/sync_iperf3_n6_server_ip.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config:$HOME/.kube/config-central:$HOME/.kube/config-regional:$HOME/.kube/config-edge}"

CTX="${OAI_BENCH_EDGE_CONTEXT:-edge@edge}"
NS="${BENCH_NS:-oai-benchmark}"
IFACE="${IPERF_BIND_IFACE:-n3}"

SERVER_IP="$(kubectl --context "$CTX" -n "$NS" exec deploy/upf-benchmark -c upf-benchmark -- \
  ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"

if [[ -z "$SERVER_IP" ]]; then
  echo "error: could not read UPF ${IFACE} IPv4" >&2
  exit 1
fi

echo "==> UPF ${IFACE}=${SERVER_IP} → ConfigMap iperf3-n6-endpoint + UE IPERF_SERVER"

kubectl --context "$CTX" -n "$NS" create configmap iperf3-n6-endpoint \
  --from-literal=server_ip="$SERVER_IP" \
  --from-literal=n6_server_ip="$SERVER_IP" \
  -o yaml --dry-run=client | kubectl --context "$CTX" -n "$NS" apply -f -

kubectl --context "$CTX" -n "$NS" set env deployment/oai-ue -c iperf3-client \
  "IPERF_SERVER=${SERVER_IP}"

echo "==> Done. UE iperf3-client will target ${SERVER_IP} (${IFACE})"
