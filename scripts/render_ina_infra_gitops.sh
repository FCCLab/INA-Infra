#!/usr/bin/env bash
# Render INA-Infra frontend (+ backend Service→host) into repos/<cluster>/ for Config Sync.
#
# Gurobi academic licenses refuse container environments, so the API runs as a
# host systemd unit on mgmt-0; Kubernetes only runs the UI and a Service/Endpoints
# that point at the host API.
#
#   ./ina-infra/scripts/install-host-backend.sh     # once on mgmt-0
#   ./ina-infra/scripts/build-and-push-images.sh    # frontend image
#   ./scripts/render_ina_infra_gitops.sh mgmt
#   ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'ina-infra' mgmt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
UI_NODEPORT="${UI_NODEPORT:-30518}"
# Host where systemd backend listens (mgmt-0 mgmt IP)
BACKEND_HOST_IP="${INA_BACKEND_HOST_IP:-10.1.132.200}"
BACKEND_PORT="${INA_BACKEND_PORT:-8082}"

CLUSTER="${1:-mgmt}"
case "$CLUSTER" in
  mgmt|central) ;;
  *)
    echo "error: ina-infra GitOps target should be mgmt (or central); got '${CLUSTER}'" >&2
    exit 1
    ;;
esac

REPO_NAME="$(cluster_gitea_repo_name "$CLUSTER")"
OUT_DIR="${REPOS_DIR}/${REPO_NAME}/namespaces/ina-infra"
rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"

FRONTEND_IMAGE="${REGISTRY}/ina-infra/frontend:${IMAGE_TAG}"

cat >"${OUT_DIR}/00-namespace.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra
    app.kubernetes.io/part-of: ina-infra
EOF

# Backend: Service without selector + Endpoints → host process (Gurobi)
cat >"${OUT_DIR}/10-backend-host-service.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ina-infra-backend
  namespace: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra-backend
    app.kubernetes.io/part-of: ina-infra
  annotations:
    ina-infra.nephio.lab/backend: host-systemd
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 8082
      targetPort: ${BACKEND_PORT}
---
apiVersion: v1
kind: Endpoints
metadata:
  name: ina-infra-backend
  namespace: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra-backend
    app.kubernetes.io/part-of: ina-infra
subsets:
  - addresses:
      - ip: ${BACKEND_HOST_IP}
    ports:
      - name: http
        port: ${BACKEND_PORT}
EOF

python3 - "$OUT_DIR" "$FRONTEND_IMAGE" "$UI_NODEPORT" <<'PY'
import pathlib, sys
out, frontend_img, node_port = sys.argv[1:4]
out = pathlib.Path(out)
frontend = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: ina-infra-nginx
  namespace: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra-frontend
    app.kubernetes.io/part-of: ina-infra
data:
  default.conf: |
    # Re-resolve ClusterIP when Service is recreated (avoid stale nginx DNS cache)
    resolver kube-dns.kube-system.svc.cluster.local valid=10s ipv6=off;

    server {{
        listen 80;
        server_name _;
        root /usr/share/nginx/html;
        index index.html;

        set $ina_backend "ina-infra-backend.ina-infra.svc.cluster.local";

        # With variables, do NOT put a URI path on proxy_pass — nginx would
        # drop /api and FastAPI would see /v1/... → 404.
        location /api/ {{
            proxy_pass http://$ina_backend:8082;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 900s;
            proxy_buffering off;
            proxy_cache off;
            chunked_transfer_encoding on;
        }}

        location /docs {{
            proxy_pass http://$ina_backend:8082;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }}

        location /docs/ {{
            proxy_pass http://$ina_backend:8082;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }}

        location /openapi.json {{
            proxy_pass http://$ina_backend:8082;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }}

        location /redoc {{
            proxy_pass http://$ina_backend:8082;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }}

        location / {{
            try_files $uri $uri/ /index.html;
        }}
    }}
---
apiVersion: v1
kind: Service
metadata:
  name: ina-infra-frontend
  namespace: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra-frontend
    app.kubernetes.io/part-of: ina-infra
spec:
  type: NodePort
  ports:
    - name: http
      port: 80
      targetPort: 80
      nodePort: {node_port}
  selector:
    app.kubernetes.io/name: ina-infra-frontend
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ina-infra-frontend
  namespace: ina-infra
  labels:
    app.kubernetes.io/name: ina-infra-frontend
    app.kubernetes.io/part-of: ina-infra
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: ina-infra-frontend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ina-infra-frontend
        app.kubernetes.io/part-of: ina-infra
    spec:
      containers:
        - name: frontend
          image: {frontend_img}
          imagePullPolicy: Always
          ports:
            - containerPort: 80
              name: http
          volumeMounts:
            - name: nginx-conf
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: default.conf
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 3
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 256Mi
      volumes:
        - name: nginx-conf
          configMap:
            name: ina-infra-nginx
"""
(out / "20-frontend.yaml").write_text(frontend)
print(f"Wrote frontend under {out}")
PY

cat >"${OUT_DIR}/README.txt" <<EOF
INA-Infra on ${CLUSTER}

API runs on the host (Gurobi forbids containers):
  sudo ./ina-infra/scripts/install-host-backend.sh

UI: NodePort ${UI_NODEPORT} → http://${BACKEND_HOST_IP}:${UI_NODEPORT}
API: http://${BACKEND_HOST_IP}:${BACKEND_PORT}/docs
EOF

echo
echo "Rendered ina-infra → ${OUT_DIR}"
echo "Next:"
echo "  sudo ./ina-infra/scripts/install-host-backend.sh"
echo "  ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'ina-infra' ${CLUSTER}"
echo "  # or kubectl --context ${CLUSTER}@${CLUSTER} apply -f ${OUT_DIR}"
echo "  UI: http://${BACKEND_HOST_IP}:${UI_NODEPORT}"
