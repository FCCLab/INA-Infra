#!/usr/bin/env bash
# Render a minimal Prometheus server into repos/ for Config Sync GitOps push.
# Discovers scrape targets via the Kubernetes API (pod annotations):
#   prometheus.io/scrape: "true"
#   prometheus.io/port:   "<containerPort>"
#   prometheus.io/path:   "/metrics"   (optional)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
PROM_NS="${PROM_NS:-monitoring}"
PROM_NAME="${PROM_NAME:-prometheus}"
PROM_IMAGE="${PROM_IMAGE:-docker.io/prom/prometheus:v2.55.1}"
PROM_RETENTION="${PROM_RETENTION:-7d}"
PROM_PVC_SIZE="${PROM_PVC_SIZE:-10Gi}"
PROM_STORAGE_CLASS="${PROM_STORAGE_CLASS:-local-path}"
PROM_NODEPORT="${PROM_NODEPORT:-30909}"
PROM_SCRAPE_INTERVAL="${PROM_SCRAPE_INTERVAL:-15s}"

purge_prometheus_manifests() {
  local dest_cluster="$1"
  local dest_ns="$2"
  local f
  for f in "${dest_cluster}"/clusterrole-"${PROM_NAME}".yaml \
           "${dest_cluster}"/clusterrolebinding-"${PROM_NAME}".yaml; do
    [[ -f "$f" ]] && rm -f "$f"
  done
  if [[ -d "$dest_ns" ]]; then
    find "$dest_ns" -maxdepth 1 -type f -name '*.yaml' -delete
  fi
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-${PROM_NS}.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
EOF
}

write_serviceaccount() {
  local dir="$1"
  cat >"${dir}/serviceaccount-${PROM_NAME}.yaml" <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${PROM_NAME}
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
EOF
}

write_rbac() {
  local dest_cluster="$1"
  cat >"${dest_cluster}/clusterrole-${PROM_NAME}.yaml" <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ${PROM_NAME}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
rules:
  - apiGroups: [""]
    resources:
      - nodes
      - nodes/metrics
      - nodes/proxy
      - services
      - endpoints
      - pods
      - configmaps
    verbs: ["get", "list", "watch"]
  - apiGroups: ["discovery.k8s.io"]
    resources: ["endpointslices"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "watch"]
  - nonResourceURLs: ["/metrics", "/metrics/cadvisor"]
    verbs: ["get"]
EOF

  cat >"${dest_cluster}/clusterrolebinding-${PROM_NAME}.yaml" <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${PROM_NAME}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ${PROM_NAME}
subjects:
  - kind: ServiceAccount
    name: ${PROM_NAME}
    namespace: ${PROM_NS}
EOF
}

write_configmap() {
  local dir="$1"
  # prometheus.yml is embedded as a literal block; keep indentation stable.
  # Discovery model (cluster-local via the Prometheus ServiceAccount):
  #   - kubernetes-apiservers: default/kubernetes:https
  #   - kubernetes-nodes / cadvisor: kubelet via API proxy
  #   - kubernetes-service-endpoints: ready Endpoints whose Service is annotated
  #     prometheus.io/scrape=true OR whose port name matches *metrics*
  #   - kubernetes-pods: pods annotated prometheus.io/scrape=true
  cat >"${dir}/configmap-${PROM_NAME}.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${PROM_NAME}
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
data:
  prometheus.yml: |
    global:
      scrape_interval: ${PROM_SCRAPE_INTERVAL}
      evaluation_interval: ${PROM_SCRAPE_INTERVAL}

    scrape_configs:
      - job_name: prometheus
        static_configs:
          - targets: ["localhost:9090"]

      # Control-plane API metrics (Service default/kubernetes).
      - job_name: kubernetes-apiservers
        kubernetes_sd_configs:
          - role: endpoints
        scheme: https
        tls_config:
          ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
        relabel_configs:
          - source_labels:
              - __meta_kubernetes_namespace
              - __meta_kubernetes_service_name
              - __meta_kubernetes_endpoint_port_name
            action: keep
            regex: default;kubernetes;https

      # Kubelet /metrics via the API server proxy (works without node HTTPS routes).
      - job_name: kubernetes-nodes
        kubernetes_sd_configs:
          - role: node
        scheme: https
        tls_config:
          ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
          insecure_skip_verify: true
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
        relabel_configs:
          - action: labelmap
            regex: __meta_kubernetes_node_label_(.+)
          - target_label: __address__
            replacement: kubernetes.default.svc:443
          - source_labels: [__meta_kubernetes_node_name]
            regex: (.+)
            target_label: __metrics_path__
            replacement: /api/v1/nodes/\$1/proxy/metrics

      - job_name: kubernetes-cadvisor
        kubernetes_sd_configs:
          - role: node
        scheme: https
        tls_config:
          ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
          insecure_skip_verify: true
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
        relabel_configs:
          - action: labelmap
            regex: __meta_kubernetes_node_label_(.+)
          - target_label: __address__
            replacement: kubernetes.default.svc:443
          - source_labels: [__meta_kubernetes_node_name]
            regex: (.+)
            target_label: __metrics_path__
            replacement: /api/v1/nodes/\$1/proxy/metrics/cadvisor

      # Ready Service Endpoints: annotated scrape=true OR port name contains "metrics"
      # (CoreDNS metrics, DCGM, GPU operator, otel-collector, …).
      - job_name: kubernetes-service-endpoints
        kubernetes_sd_configs:
          - role: endpoints
        relabel_configs:
          # Drop known-not-ready only; missing label must not drop the target.
          - source_labels: [__meta_kubernetes_endpoint_conditions_ready]
            action: drop
            regex: "false"
          - source_labels:
              - __meta_kubernetes_service_annotation_prometheus_io_scrape
              - __meta_kubernetes_endpoint_port_name
            separator: ";"
            regex: "true;.*|.*;.*metrics.*"
            action: keep
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_scheme]
            action: replace
            target_label: __scheme__
            regex: (https?)
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels:
              - __address__
              - __meta_kubernetes_service_annotation_prometheus_io_port
            action: replace
            regex: ([^:]+)(?::\\d+)?;(\\d+)
            replacement: \$1:\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_service_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            action: replace
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_service_name]
            action: replace
            target_label: kubernetes_name
          - source_labels: [__meta_kubernetes_pod_node_name]
            action: replace
            target_label: node
          - source_labels: [__meta_kubernetes_pod_node_name]
            action: replace
            target_label: kubernetes_node

      # Pods annotated for scrape (node_exporter, MetalLB, DCGM, …).
      - job_name: kubernetes-pods
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: "true"
          - source_labels: [__meta_kubernetes_pod_phase]
            action: keep
            regex: Running
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scheme]
            action: replace
            target_label: __scheme__
            regex: (https?)
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\\d+)?;(\\d+)
            replacement: \$1:\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_pod_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            action: replace
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_pod_name]
            action: replace
            target_label: kubernetes_pod_name
          - source_labels: [__meta_kubernetes_pod_node_name]
            action: replace
            target_label: node
          - source_labels: [__meta_kubernetes_pod_node_name]
            action: replace
            target_label: kubernetes_node
EOF
}

write_pvc() {
  local dir="$1"
  cat >"${dir}/persistentvolumeclaim-${PROM_NAME}.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${PROM_NAME}
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${PROM_STORAGE_CLASS}
  resources:
    requests:
      storage: ${PROM_PVC_SIZE}
EOF
}

write_deployment() {
  local dir="$1"
  local config_hash="${2:-none}"
  cat >"${dir}/deployment-${PROM_NAME}.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${PROM_NAME}
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ${PROM_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${PROM_NAME}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9090"
        prometheus.io/path: "/metrics"
        # Bump when prometheus.yml changes so the pod restarts with new config.
        checksum/config: "${config_hash}"
    spec:
      serviceAccountName: ${PROM_NAME}
      securityContext:
        fsGroup: 65534
        runAsUser: 65534
        runAsNonRoot: true
      containers:
        - name: prometheus
          image: ${PROM_IMAGE}
          imagePullPolicy: IfNotPresent
          args:
            - --config.file=/etc/prometheus/prometheus.yml
            - --storage.tsdb.path=/prometheus
            - --storage.tsdb.retention.time=${PROM_RETENTION}
            - --web.enable-lifecycle
            - --web.console.libraries=/usr/share/prometheus/console_libraries
            - --web.console.templates=/usr/share/prometheus/consoles
          ports:
            - name: http
              containerPort: 9090
              protocol: TCP
          readinessProbe:
            httpGet:
              path: /-/ready
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /-/healthy
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 2Gi
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
              readOnly: true
            - name: data
              mountPath: /prometheus
      volumes:
        - name: config
          configMap:
            name: ${PROM_NAME}
        - name: data
          persistentVolumeClaim:
            claimName: ${PROM_NAME}
EOF
}

write_services() {
  local dir="$1"
  cat >"${dir}/service-${PROM_NAME}.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${PROM_NAME}
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 9090
      targetPort: http
      protocol: TCP
  selector:
    app.kubernetes.io/name: ${PROM_NAME}
EOF

  cat >"${dir}/service-${PROM_NAME}-nodeport.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${PROM_NAME}-nodeport
  namespace: ${PROM_NS}
  labels:
    app.kubernetes.io/name: ${PROM_NAME}
spec:
  type: NodePort
  ports:
    - name: http
      port: 9090
      targetPort: http
      protocol: TCP
      nodePort: ${PROM_NODEPORT}
  selector:
    app.kubernetes.io/name: ${PROM_NAME}
EOF
}

write_cluster_prometheus() {
  local cluster="$1"
  local repo_name dest_cluster dest_ns host
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  dest_ns="${REPOS_DIR}/${repo_name}/namespaces/${PROM_NS}"
  host="$(dashboard_mgmt_ip "$cluster")"

  mkdir -p "$dest_cluster" "$dest_ns"
  purge_prometheus_manifests "$dest_cluster" "$dest_ns"

  write_namespace "$dest_ns"
  write_serviceaccount "$dest_ns"
  write_rbac "$dest_cluster"
  write_configmap "$dest_ns"
  write_pvc "$dest_ns"
  local config_hash
  config_hash="$(sha256sum "${dest_ns}/configmap-${PROM_NAME}.yaml" | awk '{print $1}')"
  write_deployment "$dest_ns" "$config_hash"
  write_services "$dest_ns"

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name}/namespaces/${PROM_NS}"
  echo "    UI: http://${host}:${PROM_NODEPORT}  (NodePort on control-plane mgmt IP)"
  echo "    SD: apiserver + nodes/cadvisor + endpoints(*metrics*|scrape=true) + pods(scrape=true)"
}

main() {
  local clusters=("$@")

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(central)
  fi

  for cluster in "${clusters[@]}"; do
    case "$cluster" in
      mgmt|central|regional|edge) ;;
      *)
        echo "error: unknown cluster '${cluster}'" >&2
        exit 1
        ;;
    esac
    write_cluster_prometheus "$cluster"
  done

  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ${clusters[*]}"
  echo "Verify: ./scripts/check-configsync.sh ${clusters[*]}"
  echo "Targets: kubectl --context <ctx> -n ${PROM_NS} port-forward svc/${PROM_NAME} 9090"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write a minimal Prometheus (SA + RBAC + ConfigMap + PVC + Deployment + Services)
into repos/<gitea-repo>/ for Config Sync.

Default cluster: central

Discovers cluster scrape targets via the Kubernetes API:
  - kubernetes-apiservers, kubernetes-nodes, kubernetes-cadvisor
  - Service Endpoints with prometheus.io/scrape=true OR port name *metrics*
  - Pods with prometheus.io/scrape=true (+ optional port/path annotations)

UI via NodePort ${PROM_NODEPORT} on the control-plane mgmt IP (same pattern as Dashboard).

Environment:
  PROM_IMAGE           Image (default: ${PROM_IMAGE})
  PROM_NS              Namespace (default: ${PROM_NS})
  PROM_RETENTION       TSDB retention (default: ${PROM_RETENTION})
  PROM_PVC_SIZE        PVC size (default: ${PROM_PVC_SIZE})
  PROM_STORAGE_CLASS   StorageClass (default: ${PROM_STORAGE_CLASS})
  PROM_NODEPORT        UI NodePort (default: ${PROM_NODEPORT})
  PROM_SCRAPE_INTERVAL Scrape interval (default: ${PROM_SCRAPE_INTERVAL})
EOF
  exit 0
fi

main "$@"
