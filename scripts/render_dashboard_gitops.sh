#!/usr/bin/env bash
# Render Kubernetes Dashboard manifests into repos/ for Config Sync GitOps push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
DASHBOARD_CHART_VERSION="${DASHBOARD_CHART_VERSION:-7.14.0}"
DASHBOARD_CHART_URL="${DASHBOARD_CHART_URL:-https://github.com/kubernetes-retired/dashboard/releases/download/kubernetes-dashboard-${DASHBOARD_CHART_VERSION}/kubernetes-dashboard-${DASHBOARD_CHART_VERSION}.tgz}"

render_chart() {
  local chart_tgz out
  chart_tgz="$(mktemp --suffix=.tgz)"
  out="$(mktemp)"
  curl -fsSL "$DASHBOARD_CHART_URL" -o "$chart_tgz"
  helm template kubernetes-dashboard "$chart_tgz" --namespace kubernetes-dashboard >"$out"
  rm -f "$chart_tgz"
  printf '%s' "$out"
}

write_rbac() {
  local dir="$1"
  cat >"${dir}/rbac-admin-user.yaml" <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: admin-user
  namespace: kubernetes-dashboard
---
# Long-lived token for the lab autologin proxy (no browser login key).
apiVersion: v1
kind: Secret
metadata:
  name: admin-user-token
  namespace: kubernetes-dashboard
  annotations:
    kubernetes.io/service-account.name: admin-user
type: kubernetes.io/service-account-token
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-user
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: admin-user
    namespace: kubernetes-dashboard
EOF
}

# TLS reverse proxy that injects admin-user Bearer token so Dashboard v7 skips login.
write_autologin_proxy() {
  local dir="$1"
  cat >"${dir}/configmap-dashboard-autologin.yaml" <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: dashboard-autologin
  namespace: kubernetes-dashboard
data:
  start.sh: |
    #!/bin/sh
    set -eu
    TOKEN="$(cat /var/run/secrets/dashboard/token)"
    openssl req -x509 -nodes -newkey rsa:2048 \
      -keyout /tmp/tls.key -out /tmp/tls.crt -days 3650 \
      -subj "/CN=kubernetes-dashboard" >/dev/null 2>&1
    mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi
    cat >/tmp/nginx.conf <<NGINX
    worker_processes 1;
    error_log /dev/stderr warn;
    pid /tmp/nginx.pid;
    # Container runs with capability drops; stay root so nginx skips chown on temp dirs.
    user root;
    events { worker_connections 128; }
    http {
      access_log /dev/stdout;
      client_body_temp_path /tmp/client_body;
      proxy_temp_path /tmp/proxy;
      fastcgi_temp_path /tmp/fastcgi;
      uwsgi_temp_path /tmp/uwsgi;
      scgi_temp_path /tmp/scgi;
      server {
        listen 8443 ssl;
        ssl_certificate /tmp/tls.crt;
        ssl_certificate_key /tmp/tls.key;
        location / {
          proxy_http_version 1.1;
          proxy_pass https://kubernetes-dashboard-kong-proxy.kubernetes-dashboard.svc.cluster.local:443;
          proxy_ssl_verify off;
          proxy_set_header Host \$host;
          proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto https;
          proxy_set_header Authorization "Bearer ${TOKEN}";
          proxy_buffering off;
        }
      }
    }
    NGINX
    exec nginx -g "daemon off;" -c /tmp/nginx.conf
EOF

  cat >"${dir}/deployment-dashboard-autologin.yaml" <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dashboard-autologin
  namespace: kubernetes-dashboard
  labels:
    app.kubernetes.io/name: dashboard-autologin
    app.kubernetes.io/part-of: kubernetes-dashboard
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dashboard-autologin
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dashboard-autologin
        app.kubernetes.io/part-of: kubernetes-dashboard
    spec:
      containers:
        - name: nginx
          image: docker.io/library/nginx:1.27
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh", "/config/start.sh"]
          ports:
            - name: https
              containerPort: 8443
              protocol: TCP
          volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
            - name: token
              mountPath: /var/run/secrets/dashboard
              readOnly: true
            - name: tmp
              mountPath: /tmp
          readinessProbe:
            tcpSocket:
              port: https
            initialDelaySeconds: 2
            periodSeconds: 5
          livenessProbe:
            tcpSocket:
              port: https
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
      volumes:
        - name: config
          configMap:
            name: dashboard-autologin
            defaultMode: 0755
        - name: token
          secret:
            secretName: admin-user-token
        - name: tmp
          emptyDir: {}
EOF

  cat >"${dir}/service-dashboard-autologin.yaml" <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: dashboard-autologin
  namespace: kubernetes-dashboard
  labels:
    app.kubernetes.io/name: dashboard-autologin
spec:
  ports:
    - name: https
      port: 443
      protocol: TCP
      targetPort: https
  selector:
    app.kubernetes.io/name: dashboard-autologin
EOF
}

write_nodeport_service() {
  local dir="$1"
  local node_port="$2"
  # Front Kong with the autologin proxy (injects admin token; no browser key).
  cat >"${dir}/service-kubernetes-dashboard-nodeport.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: kubernetes-dashboard-nodeport
  namespace: kubernetes-dashboard
spec:
  type: NodePort
  ports:
    - name: https
      port: 443
      protocol: TCP
      targetPort: 8443
      nodePort: ${node_port}
  selector:
    app.kubernetes.io/name: dashboard-autologin
EOF
}

split_chart_manifests() {
  local src="$1"
  local dest_cluster="$2"
  local dest_ns="$3"

  python3 - "$src" "$dest_cluster" "$dest_ns" <<'PY'
import sys
from pathlib import Path

import yaml

src, dest_cluster, dest_ns = sys.argv[1:4]
cluster_kinds = {"ClusterRole", "ClusterRoleBinding"}
cluster_docs = []
ns_docs = []

def clean_metadata(meta):
    if not isinstance(meta, dict):
        return
    if meta.get("annotations") is None:
        meta.pop("annotations", None)
    labels = meta.get("labels")
    if isinstance(labels, dict) and "spec" in labels:
        labels.pop("spec", None)


for doc in yaml.safe_load_all(Path(src).read_text()):
    if not doc or not doc.get("kind"):
        continue
    kind = doc["kind"]
    clean_metadata(doc.get("metadata"))
    if kind == "Deployment":
        template = doc.get("spec", {}).get("template", {})
        clean_metadata(template.get("metadata"))
        pod_spec = template.get("spec")
        if isinstance(pod_spec, dict):
            pod_spec.pop("automountServiceAccountToken", None)
    if kind in cluster_kinds:
        cluster_docs.append(doc)
    else:
        meta = doc.setdefault("metadata", {})
        if kind != "Namespace" and "namespace" not in meta:
            meta["namespace"] = "kubernetes-dashboard"
        ns_docs.append(doc)

def write_docs(docs, directory, *, purge_all=False, purge_prefix=None):
    """Write one file per resource. Optionally remove stale chart files first.

    For namespaces/kubernetes-dashboard, purge_all=True (dir is dashboard-only).
    For cluster/, only remove files owned by this chart (name contains
    'kubernetes-dashboard') so Flannel/MetalLB/etc. are left intact.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    keep = {
        "rbac-admin-user.yaml",
        "service-kubernetes-dashboard-nodeport.yaml",
        "namespace-kubernetes-dashboard.yaml",
        "configmap-dashboard-autologin.yaml",
        "deployment-dashboard-autologin.yaml",
        "service-dashboard-autologin.yaml",
    }
    for old in directory.glob("*.yaml"):
        if old.name in keep:
            continue
        if purge_all or (purge_prefix and purge_prefix in old.name):
            old.unlink()
    for doc in docs:
        kind = doc["kind"].lower()
        name = doc["metadata"]["name"]
        path = directory / f"{kind}-{name}.yaml"
        path.write_text(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))

write_docs(cluster_docs, dest_cluster, purge_prefix="kubernetes-dashboard")
write_docs(ns_docs, dest_ns, purge_all=True)
print(f"  cluster: {len(cluster_docs)} resources")
print(f"  namespaces/kubernetes-dashboard: {len(ns_docs)} resources")
PY
}

write_namespace() {
  local dir="$1"
  cat >"${dir}/namespace-kubernetes-dashboard.yaml" <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: kubernetes-dashboard
EOF
}

write_cluster_dashboard() {
  local cluster="$1"
  local repo_name node_port host dest_dir chart_yaml dest_cluster
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  node_port="$(dashboard_nodeport)"
  host="$(dashboard_mgmt_ip "$cluster")"
  dest_dir="${REPOS_DIR}/${repo_name}/namespaces/kubernetes-dashboard"
  dest_cluster="${REPOS_DIR}/${repo_name}/cluster"
  chart_yaml="$2"

  mkdir -p "$dest_dir" "$dest_cluster"
  rm -f "${dest_dir}/service-kubernetes-dashboard-lb.yaml" \
        "${dest_dir}/dashboard-${DASHBOARD_CHART_VERSION}.yaml" \
        "${dest_dir}/namespace-kubernetes-dashboard.yaml"

  split_chart_manifests "$chart_yaml" "$dest_cluster" "$dest_dir"
  write_namespace "$dest_dir"
  write_rbac "$dest_dir"
  write_autologin_proxy "$dest_dir"
  write_nodeport_service "$dest_dir" "$node_port"

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (https://${host}:${node_port}, no login key)"
}

main() {
  local clusters=("$@")
  local chart_yaml tmp

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(mgmt "${ALL_CLUSTERS[@]}")
  fi

  if ! command -v helm >/dev/null 2>&1; then
    echo "error: helm not found" >&2
    exit 1
  fi

  tmp="$(mktemp)"
  chart_yaml="$(render_chart)"
  cp "$chart_yaml" "$tmp"
  rm -f "$chart_yaml"

  for cluster in "${clusters[@]}"; do
    write_cluster_dashboard "$cluster" "$tmp"
  done

  rm -f "$tmp"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh"
  echo "Login: none (NodePort fronts dashboard-autologin proxy with admin-user token)."
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write Kubernetes Dashboard manifests to repos/<gitea-repo>/ for Config Sync.
Splits helm output into cluster/ and namespaces/kubernetes-dashboard/ (one file per resource).

Exposes Dashboard via NodePort ${DASHBOARD_NODEPORT:-30443} on the control-plane mgmt IP
(no MetalLB). An nginx autologin proxy injects the admin-user token so no browser key
is required (Dashboard v7 removed skip-login).

Chart: kubernetes-dashboard ${DASHBOARD_CHART_VERSION}
EOF
  exit 0
fi

main "$@"
