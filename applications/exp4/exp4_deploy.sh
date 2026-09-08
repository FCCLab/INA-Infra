#!/usr/bin/env bash
# Deploy Exp4 apps with two Multus ifaces per pod (no RAN / no oaitun):
#   net1  simulated 5G  10.140.<slice>.1 (server GW + NAT) / .2 (client)
#   net2  console       10.1.137.21N / .22N
# IP plan: applications/exp4/ip_plan.md
#
#   ./applications/exp4/exp4_deploy.sh --undeploy && ./applications/exp4/exp4_deploy.sh
#   ./applications/exp4/exp4_deploy.sh                 # all slices on edge gpu-a40
#   ./applications/exp4/exp4_deploy.sh s1 s4
#   IMAGE_TAG=nws-v0.15-amd64 ./applications/exp4/exp4_deploy.sh
#   CLUSTER=central ./applications/exp4/exp4_deploy.sh s5
#   ./applications/exp4/exp4_deploy.sh --status
#   ./applications/exp4/exp4_deploy.sh --undeploy
#   ./applications/exp4/exp4_deploy.sh --plan
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/cluster_lib.sh"

REGISTRY="${REGISTRY:-10.1.132.30:5000}"
# Newest tags. IMAGE_TAG=... overrides every slice.
DEFAULT_IMAGE_TAG="nws-v0.14-amd64"
DEFAULT_IMAGE_TAG_S2="nws-v0.21-amd64"
DEFAULT_IMAGE_TAG_S4="nws-v0.15-amd64"
CLUSTER="${CLUSTER:-edge}"
NAMESPACE="${NAMESPACE:-exp4-apps}"
GW="${GW:-10.1.137.1}"
# Edge A40 parent NIC is ens12f0; VMs (central/regional/cpu-edge) use enp7s0.
if [[ -n "${MULTUS_MASTER:-}" ]]; then
  MASTER="${MULTUS_MASTER}"
elif [[ "${CLUSTER}" == "edge" ]]; then
  MASTER="ens12f0"
else
  MASTER="enp7s0"
fi
if [[ -n "${NODE_NAME:-}" ]]; then
  PIN_NODE="${NODE_NAME}"
elif [[ "${CLUSTER}" == "edge" ]]; then
  PIN_NODE="gpu-a40"
else
  PIN_NODE=""
fi
CTX="$(kube_context "${CLUSTER}")"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge}"

# slice|name|srv_console_ip|srv_console_mac|cli_console_ip|cli_console_mac|srv_cpu|srv_mem|cli_cpu|cli_mem|image_stem
# Simulated 5G IPs are derived: server 10.140.<s>.1 / client 10.140.<s>.2.
# CCTV client re-encodes 1280x720 H.264 in-process; 200m/256Mi OOMs gst-launch.
SLICES_DEF=(
  "1|iperf-sftp|10.1.137.211|02:0a:89:a0:00:01|10.1.137.221|02:0a:40:01:00:01|500m|512Mi|200m|256Mi|exp4-s1-iperf-sftp"
  "2|cctv|10.1.137.212|02:0a:89:a0:00:02|10.1.137.222|02:0a:40:02:00:01|4|12Gi|1|2Gi|exp4-s2-cctv"
  "3|ott|10.1.137.213|02:0a:89:a0:00:03|10.1.137.223|02:0a:40:03:00:01|1|1Gi|1|1Gi|exp4-s3-ott"
  "4|cpu-offload|10.1.137.214|02:0a:89:a0:00:04|10.1.137.224|02:0a:40:04:00:01|500m|512Mi|200m|256Mi|exp4-s4-cpu-offload"
  "5|iot|10.1.137.215|02:0a:89:a0:00:05|10.1.137.225|02:0a:40:05:00:01|500m|512Mi|500m|512Mi|exp4-s5-iot"
)

ACTION=deploy
WANT=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --undeploy|--delete) ACTION=undeploy; shift ;;
    --status) ACTION=status; shift ;;
    --plan) ACTION=plan; shift ;;
    -h|--help)
      sed -n '2,13p' "$0"
      exit 0
      ;;
    s1|s2|s3|s4|s5) WANT+=("${1#s}"); shift ;;
    1|2|3|4|5) WANT+=("$1"); shift ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

kc() { kubectl --context="${CTX}" -n "${NAMESPACE}" "$@"; }

sim5g_server_ip() { printf '10.140.%s.1' "$1"; }
sim5g_client_ip() { printf '10.140.%s.2' "$1"; }
sim5g_server_mac() { printf '02:0a:8b:a0:00:%02x' "$1"; }
sim5g_client_mac() { printf '02:0a:41:%02x:00:01' "$1"; }

image_tag_for() {
  local sid="$1"
  if [[ -n "${IMAGE_TAG:-}" ]]; then
    printf '%s' "${IMAGE_TAG}"
    return
  fi
  case "${sid}" in
    2) printf '%s' "${DEFAULT_IMAGE_TAG_S2}" ;;
    4) printf '%s' "${DEFAULT_IMAGE_TAG_S4}" ;;
    *) printf '%s' "${DEFAULT_IMAGE_TAG}" ;;
  esac
}

print_plan() {
  local tag_note
  if [[ -n "${IMAGE_TAG:-}" ]]; then
    tag_note="tag=${IMAGE_TAG} (all slices)"
  else
    tag_note="tags s1/s3/s5=${DEFAULT_IMAGE_TAG}  s2=${DEFAULT_IMAGE_TAG_S2}  s4=${DEFAULT_IMAGE_TAG_S4}"
  fi
  cat <<EOF
Exp4 dual-Multus plan  (master ${MASTER}  console gw ${GW})
  net1 simulated 5G  10.140.<s>.1/.2   (client default via .1; server NAT)
  net2 console       10.1.137.21N/.22N
cluster=${CLUSTER}  node=${PIN_NODE:-any}  ns=${NAMESPACE}  ${tag_note}

 Slice  App            Sim5G server      Sim5G client      Consoles
 -----  -------------  ----------------  ----------------  --------
EOF
  local row sid name sip smac cip cmac
  for row in "${SLICES_DEF[@]}"; do
    IFS='|' read -r sid name sip smac cip cmac _ _ _ _ _ <<<"${row}"
    printf '  s%-4s %-13s %-16s %-16s http://%s/  http://%s/\n' \
      "${sid}" "${name}" "$(sim5g_server_ip "${sid}")" "$(sim5g_client_ip "${sid}")" \
      "${sip}" "${cip}"
  done
  echo
  echo "Data plane: net1 10.140 (simulated PDU). Consoles: net2 10.1.137. See ${HERE}/ip_plan.md"
}

row_for() {
  local want="$1" row sid
  for row in "${SLICES_DEF[@]}"; do
    IFS='|' read -r sid _ <<<"${row}"
    if [[ "${sid}" == "${want}" ]]; then
      printf '%s\n' "${row}"
      return 0
    fi
  done
  return 1
}

selected_rows() {
  if [[ ${#WANT[@]} -eq 0 ]]; then
    printf '%s\n' "${SLICES_DEF[@]}"
    return
  fi
  local w
  for w in "${WANT[@]}"; do
    row_for "${w}"
  done
}

ensure_ns() {
  kubectl --context="${CTX}" create namespace "${NAMESPACE}" --dry-run=client -o yaml \
    | kubectl --context="${CTX}" apply -f -
}

apply_nad() {
  local name="$1" ip="$2" gw="${3:-}"
  local addr_json
  if [[ -n "${gw}" ]]; then
    addr_json="{\"address\":\"${ip}/24\",\"gateway\":\"${gw}\"}"
  else
    addr_json="{\"address\":\"${ip}/24\"}"
  fi
  kc apply -f - <<EOF
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ${name}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/part-of: exp4
    ina.lab/scheme: exp4-no5g
spec:
  config: '{"cniVersion":"0.3.1","name":"${name}","plugins":[{"type":"macvlan","capabilities":{"ips":true,"mac":true},"master":"${MASTER}","mode":"bridge","ipam":{"type":"static","addresses":[${addr_json}]}},{"type":"tuning","capabilities":{"mac":true},"ipam":{},"sysctl":{"net.ipv4.conf.IFNAME.arp_ignore":"1","net.ipv4.conf.IFNAME.arp_announce":"2"}}]}'
EOF
}

extra_server_env() {
  local sid="$1"
  case "${sid}" in
    1)
      cat <<'Y'
        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
Y
      ;;
    2)
      cat <<'Y'
        - name: HTTP_PORT
          value: "8080"
        - name: RTSP_PORT
          value: "8554"
        - name: START_MEDIAMTX
          value: "true"
        - name: YOLO_BACKEND
          value: "deepstream"
        - name: YOLO_ENABLED
          value: "true"
        - name: DS_NUM_STREAMS
          value: "4"
        - name: YOLO_DEVICE
          value: "cuda:0"
Y
      ;;
    3)
      cat <<'Y'
        - name: HTTP_PORT
          value: "8080"
        - name: START_MEDIAMTX
          value: "true"
Y
      ;;
    4)
      cat <<'Y'
        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
        - name: SFTP_AUTOSTART
          value: "1"
        - name: EXP4_PBKDF2_ITERS
          value: "80000"
Y
      ;;
    5)
      cat <<'Y'
        - name: LOCAL_BROKER_HOST
          value: "127.0.0.1"
        - name: LOCAL_BROKER_PORT
          value: "1884"
        - name: DL_FAST_PERIOD_S
          value: "0.002"
        - name: DL_PAYLOAD_BYTES
          value: "5000"
        - name: DL_DEVICE_IDS
          value: "ue1"
Y
      ;;
  esac
}

ott_chromium_sidecar() {
  # linuxserver/chromium: Selkies :3000 + CDP :9222 (same pod netns as the app).
  cat <<'Y'
      - name: chromium
        image: 10.1.132.30:5000/linuxserver-chromium:latest
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
          seccompProfile:
            type: Unconfined
        env:
        - name: PUID
          value: "1000"
        - name: PGID
          value: "1000"
        - name: TZ
          value: "UTC"
        - name: TITLE
          value: "OTT UE exp4-s3"
        - name: DISABLE_IPV6
          value: "true"
        - name: PIXELFLUX_WAYLAND
          value: "false"
        - name: CHROME_CLI
          value: "--proxy-server=socks5://127.0.0.1:1080 --remote-debugging-port=9222 --remote-allow-origins=* --no-first-run --no-default-browser-check --disable-features=TranslateUI --autoplay-policy=no-user-gesture-required --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling --disable-gpu --no-sandbox --window-size=1920,1080 https://www.youtube.com"
        - name: CUSTOM_PORT
          value: "3000"
        - name: CUSTOM_HTTPS_PORT
          value: "3001"
        - name: SUBFOLDER
          value: "/chrome/"
        ports:
        - name: chrome-http
          containerPort: 3000
        - name: chrome-https
          containerPort: 3001
        - name: cdp
          containerPort: 9222
        volumeMounts:
        - name: chromium-config
          mountPath: /config
        - name: dshm
          mountPath: /dev/shm
        resources:
          requests:
            cpu: "2"
            memory: 4Gi
          limits:
            cpu: "4"
            memory: 8Gi
Y
}

ott_chromium_volumes() {
  cat <<'Y'
      volumes:
      - name: chromium-config
        emptyDir: {}
      - name: dshm
        emptyDir:
          medium: Memory
          sizeLimit: 1Gi
Y
}

cctv_server_mounts() {
  cat <<'Y'
        volumeMounts:
        - name: ds-models
          mountPath: /models
Y
}

cctv_server_volumes() {
  cat <<'Y'
      volumes:
      - name: ds-models
        hostPath:
          path: /var/lib/ina-infra/exp4-s2-models
          type: DirectoryOrCreate
Y
}

extra_client_env() {
  local sid="$1" sip="$2" cip="${3:-}"
  case "${sid}" in
    1)
      cat <<Y
        - name: SFTP_HOST
          value: "${sip}"
        - name: IPERF_HOST
          value: "${sip}"
        - name: BIND_DEV
          value: "net1"
        - name: IPERF_PARALLEL
          value: "5"
        - name: IPERF_BANDWIDTH
          value: "10M"
        - name: IPERF_TIME
          value: "0"
        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
Y
      ;;
    2)
      cat <<Y
        - name: RTSP_TARGET_HOST
          value: "${sip}"
        - name: SERVER_URL
          value: "http://${sip}:8080"
        - name: STREAM_PATH
          value: "cctv/ue2"
        - name: CLIENT_ID
          value: "ue2"
        - name: STREAMING_ENABLED
          value: "0"
        - name: DS_NUM_STREAMS
          value: "4"
        - name: MTX_SOURCE_HOST
          value: "${sip}"
        - name: MTX_SOURCE_RTSP_PORT
          value: "8555"
        - name: PDU_WAIT_TIMEOUT
          value: "5"
        - name: EXP4_LATENCY_HTTP
          value: "0"
Y
      ;;
    3)
      cat <<Y
        - name: SERVER_HOST
          value: "${sip}"
        - name: SERVER_URL
          value: "http://${sip}"
        - name: SERVER_RTSP_PORT
          value: "8555"
        - name: PDU_WAIT_TIMEOUT
          value: "5"
        - name: PDU_SOCKS_PORT
          value: "1080"
        - name: OTT_PLAY_MODE
          value: "chromium_5g"
        - name: OTT_PLAY_QUALITY
          value: "4k"
        - name: OTT_MOSAIC
          value: "1"
        - name: OTT_MOSAIC_COUNT
          value: "4"
        - name: OTT_MOSAIC_WIDTH
          value: "1920"
        - name: OTT_MOSAIC_HEIGHT
          value: "1080"
        - name: CHROME_CDP_HOST
          value: "127.0.0.1"
        - name: CHROME_CDP_PORT
          value: "9222"
        - name: CHROME_CDP_WAIT
          value: "90"
        - name: CHROME_UPSTREAM
          value: "http://127.0.0.1:3000"
        - name: CHROME_HTTP_URL
          value: "https://${cip}/chrome/"
        - name: HTTPS_PORT
          value: "443"
Y
      ;;
    4)
      cat <<Y
        - name: SFTP_HOST
          value: "${sip}"
        - name: IPERF_HOST
          value: "${sip}"
        - name: BIND_DEV
          value: "net1"
        - name: IPERF_PARALLEL
          value: "5"
        - name: IPERF_BANDWIDTH
          value: "10M"
        - name: IPERF_TIME
          value: "0"
        - name: IPERF_AUTOSTART
          value: "1"
        - name: IPERF_PORT_COUNT
          value: "8"
Y
      ;;
    5)
      cat <<Y
        - name: BROKER_HOST
          value: "${sip}"
        - name: BROKER_PORT
          value: "1883"
        - name: PDU_WAIT_TIMEOUT
          value: "5"
        - name: TARGET_MBPS
          value: "20"
        - name: DEVICE_ID
          value: "ue1"
        - name: SEND_ENABLED
          value: "0"
        - name: LATENCY_PROBE_ENABLED
          value: "0"
Y
      ;;
  esac
}

apply_workload() {
  local role="$1" sid="$2" name="$3"
  local console_ip="$4" console_mac="$5"
  local sim5g_ip="$6" sim5g_mac="$7"
  local peer_sim5g="$8"
  local cpu="$9" mem="${10}" stem="${11}"
  local deploy="exp4-s${sid}-${name}-${role}"
  local nad_sim="exp4-s${sid}-${role}-sim5g"
  local nad_con="exp4-s${sid}-${role}-console"
  local img="${REGISTRY}/${stem}-${role}:$(image_tag_for "${sid}")"
  local to_env probe app_name
  # net1 = simulated 5G (10.140), net2 = console (10.1.137).
  if [[ "${role}" == "server" ]]; then
    to_env="TO_CLIENT_IFACE"
    probe="${peer_sim5g}"
    app_name="exp4-s${sid}"
  else
    to_env="TO_SERVER_IFACE"
    probe=""
    app_name="exp4-s${sid}-client"
  fi
  # Drop the pre-split single NAD if it is still around.
  kc delete nad "exp4-s${sid}-${role}" --ignore-not-found >/dev/null 2>&1 || true
  apply_nad "${nad_sim}" "${sim5g_ip}" ""
  apply_nad "${nad_con}" "${console_ip}" "${GW}"
  local extras="" gpu_res="" node_extra="" runtime_extra=""
  if [[ "${role}" == "server" ]]; then
    extras="$(extra_server_env "${sid}")"
  else
    extras="$(extra_client_env "${sid}" "${peer_sim5g}" "${console_ip}")"
  fi
  local extra_containers="" extra_volumes="" extra_mounts=""
  if [[ "${role}" == "client" && "${sid}" == "3" ]]; then
    extra_containers="$(ott_chromium_sidecar)"
    extra_volumes="$(ott_chromium_volumes)"
  fi
  if [[ "${role}" == "server" && "${sid}" == "2" ]]; then
    extra_mounts="$(cctv_server_mounts)"
    extra_volumes="$(cctv_server_volumes)"
  fi
  if [[ "${role}" == "server" && "${sid}" == "2" ]]; then
    gpu_res=$'\n            nvidia.com/gpu: "1"'
    runtime_extra=$'\n      runtimeClassName: nvidia'
  fi
  if [[ -n "${PIN_NODE}" ]]; then
    node_extra=$'\n        kubernetes.io/hostname: '"${PIN_NODE}"
  fi
  # Simulated 5G: client uses server .1 as DNN GW (needed for OTT SOCKS→YouTube).
  # Server NATs 10.140.<s>.0/24 out eth0. Privileged init is required for iptables.
  local init_sc sim5g_extra
  if [[ "${role}" == "server" ]]; then
    init_sc=$'          privileged: true\n          capabilities:\n            add: ["NET_ADMIN", "NET_RAW"]'
    sim5g_extra=$(cat <<E
          echo 1 > /proc/sys/net/ipv4/ip_forward || true
          echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter || true
          echo 0 > /proc/sys/net/ipv4/conf/net1/rp_filter || true
          iptables -t nat -C POSTROUTING -s 10.140.${sid}.0/24 ! -d 10.140.${sid}.0/24 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.140.${sid}.0/24 ! -d 10.140.${sid}.0/24 -j MASQUERADE
          iptables -C FORWARD -s 10.140.${sid}.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -s 10.140.${sid}.0/24 -j ACCEPT
          iptables -C FORWARD -d 10.140.${sid}.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -d 10.140.${sid}.0/24 -j ACCEPT
E
)
  else
    init_sc=$'          capabilities:\n            add: ["NET_ADMIN"]'
    sim5g_extra=$(cat <<E
          ip route replace default via ${peer_sim5g} dev net1 table 140 || true
E
)
  fi
  kc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${deploy}
  namespace: ${NAMESPACE}
  labels: &labels
    app.kubernetes.io/name: ${deploy}
    app.kubernetes.io/part-of: exp4
    ina.lab/scheme: exp4-no5g
    ina.lab/slice: "${sid}"
    ina.lab/role: ${role}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels: *labels
  template:
    metadata:
      labels: *labels
      annotations:
        k8s.v1.cni.cncf.io/networks: '[{"name":"${nad_sim}","interface":"net1","ips":["${sim5g_ip}/24"],"mac":"${sim5g_mac}"},{"name":"${nad_con}","interface":"net2","ips":["${console_ip}/24"],"gateways":["${GW}"],"mac":"${console_mac}"}]'
    spec:${runtime_extra}
      nodeSelector:
        kubernetes.io/arch: amd64
        ina-infra.nephio.lab/multus-master: ${MASTER}${node_extra}
      initContainers:
      - name: multus-route-profile
        image: docker.io/nicolaka/netshoot
        imagePullPolicy: IfNotPresent
        securityContext:
${init_sc}
        command:
        - bash
        - -c
        - |
          set -euo pipefail
          wait_iface() {
            local IFACE=\$1
            for _ in \$(seq 1 40); do
              ip link show "\$IFACE" >/dev/null 2>&1 && break
              sleep 1
            done
            ip link show "\$IFACE"
          }
          wait_iface net1
          wait_iface net2
          # Simulated 5G (net1): on-link 10.140.<s>.0/24, table 140.
          ip route replace 10.140.${sid}.0/24 dev net1 || true
          ip route replace 10.140.${sid}.0/24 dev net1 table 140 || true
          ip rule del from ${sim5g_ip}/32 table 140 2>/dev/null || true
          ip rule add from ${sim5g_ip}/32 table 140 priority 90
${sim5g_extra}
          # Console (net2): site 137, table 137, default via GW.
          ip route replace 10.1.137.0/24 dev net2 || true
          ip route replace 10.1.137.0/24 dev net2 table 137 || true
          ip route replace default via ${GW} dev net2 table 137 || true
          ip rule del from ${console_ip}/32 table 137 2>/dev/null || true
          ip rule add from ${console_ip}/32 table 137 priority 100
          ip route show
          ip route show table 137
          ip route show table 140
          ip rule show
        resources:
          requests:
            cpu: 10m
            memory: 16Mi
          limits:
            cpu: 100m
            memory: 64Mi
      containers:
      - name: app
        image: ${img}
        imagePullPolicy: Always
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        env:
        - name: ${to_env}
          value: "net1"
        - name: PDU_IFACE
          value: "net1"
        - name: CONSOLE_IFACE
          value: "net2"
        - name: SLICE_ID
          value: "${sid}"
        - name: EXP4_APP_TYPE
          value: "exp4-s${sid}"
        - name: APP_NAME
          value: "${app_name}"
        - name: EXP4_METRICS_ORIGIN
          value: "${role}"
        - name: SCHEME_ID
          value: "exp4-no5g"
        - name: TARGET_CLUSTER
          value: "${CLUSTER}"
        - name: MULTUS_IP
          value: "${sim5g_ip}"
        - name: SIM5G_IP
          value: "${sim5g_ip}"
        - name: SIM5G_MAC
          value: "${sim5g_mac}"
        - name: CONSOLE_IP
          value: "${console_ip}"
        - name: CONSOLE_MAC
          value: "${console_mac}"
        - name: MULTUS_GW
          value: "${GW}"
        - name: PUBLIC_BASE_URL
          value: "http://${console_ip}"
        - name: MTX_RTSP_PUBLIC
          value: "rtsp://${sim5g_ip}:8555"
        - name: TARGET_SERVER_IP
          value: "${peer_sim5g}"
        - name: E2E_PROBE_HOST
          value: "${probe:-${peer_sim5g}}"
        - name: INFLUXDB_URL
          value: "http://influxdb.influxdb.svc:8086"
        - name: INFLUXDB_TOKEN
          value: "ina-infra-influxdb-token"
        - name: INFLUXDB_ORG
          value: "ina-infra"
        - name: INFLUXDB_BUCKET
          value: "default"
${extras}
        ports:
        - containerPort: 80
        - containerPort: 443
        - containerPort: 8080
        - containerPort: 8090
${extra_mounts}
        resources:
          requests:
            cpu: "${cpu}"
            memory: ${mem}${gpu_res}
          limits:
            cpu: "${cpu}"
            memory: ${mem}${gpu_res}
${extra_containers}
${extra_volumes}
EOF
  echo "  ${role} ${deploy}  sim5g=${sim5g_ip}  console=${console_ip}  ${img}"
}

deploy_all() {
  print_plan
  echo "Deploying on ${CTX} / ${NAMESPACE}"
  ensure_ns
  local row sid name sip smac cip cmac cpu mem cli_cpu cli_mem stem
  local sim_s sim_c sim_s_mac sim_c_mac
  while IFS='|' read -r sid name sip smac cip cmac cpu mem cli_cpu cli_mem stem; do
    [[ -z "${sid}" ]] && continue
    sim_s="$(sim5g_server_ip "${sid}")"
    sim_c="$(sim5g_client_ip "${sid}")"
    sim_s_mac="$(sim5g_server_mac "${sid}")"
    sim_c_mac="$(sim5g_client_mac "${sid}")"
    echo "==> slice ${sid} ${name}"
    apply_workload server "${sid}" "${name}" "${sip}" "${smac}" "${sim_s}" "${sim_s_mac}" "${sim_c}" "${cpu}" "${mem}" "${stem}"
    apply_workload client "${sid}" "${name}" "${cip}" "${cmac}" "${sim_c}" "${sim_c_mac}" "${sim_s}" "${cli_cpu}" "${cli_mem}" "${stem}"
  done < <(selected_rows)
  echo
  echo "Wait:  kubectl --context=${CTX} -n ${NAMESPACE} get pods -o wide -w"
  echo "Consoles: net2 10.1.137 addresses in the table (port 80). Data: net1 10.140."
}

undeploy_all() {
  echo "Deleting namespace ${NAMESPACE} on ${CTX}"
  kubectl --context="${CTX}" delete namespace "${NAMESPACE}" --ignore-not-found
}

show_status() {
  print_plan
  kubectl --context="${CTX}" get ns "${NAMESPACE}" >/dev/null 2>&1 || {
    echo "namespace ${NAMESPACE} not found"
    return 0
  }
  kc get nad,deploy,pods -o wide
}

case "${ACTION}" in
  plan) print_plan ;;
  status) show_status ;;
  undeploy) undeploy_all ;;
  deploy) deploy_all ;;
esac
