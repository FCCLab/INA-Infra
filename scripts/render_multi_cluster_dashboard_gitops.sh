#!/usr/bin/env bash
# Render Multi-Cluster Resource Dashboard into repos/mgmt/namespaces/dashboard/ for Config Sync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
REGISTRY="${REGISTRY:-10.1.132.30:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
UI_NODEPORT="${UI_NODEPORT:-30574}"
KUBECONFIG_DIR="${KUBECONFIG_DIR:-$HOME/.kube}"

REPO_NAME="$(cluster_gitea_repo_name "mgmt")"
OUT_DIR="${REPOS_DIR}/${REPO_NAME}/namespaces/dashboard"
rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"

BACKEND_IMAGE="${REGISTRY}/dashboard/backend:${IMAGE_TAG}"
FRONTEND_IMAGE="${REGISTRY}/dashboard/frontend:${IMAGE_TAG}"

echo "==> Rendering namespace and secret..."
cat >"${OUT_DIR}/00-namespace.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: dashboard
  labels:
    app.kubernetes.io/name: multi-cluster-dashboard
    app.kubernetes.io/part-of: dashboard
EOF

# Read kubeconfigs and encode to Secret
b64() {
  base64 -w 0 < "$1"
}

cat >"${OUT_DIR}/10-secret-kubeconfigs.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: dashboard-kubeconfigs
  namespace: dashboard
  labels:
    app.kubernetes.io/name: dashboard-backend
    app.kubernetes.io/part-of: dashboard
type: Opaque
data:
  config: $(b64 "${KUBECONFIG_DIR}/config")
  config-central: $(b64 "${KUBECONFIG_DIR}/config-central")
  config-regional: $(b64 "${KUBECONFIG_DIR}/config-regional")
  config-edge: $(b64 "${KUBECONFIG_DIR}/config-edge")
EOF

echo "==> Rendering backend workloads..."
cat >"${OUT_DIR}/20-deployment-backend.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dashboard-backend
  namespace: dashboard
  labels:
    app.kubernetes.io/name: dashboard-backend
    app.kubernetes.io/part-of: dashboard
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dashboard-backend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dashboard-backend
        app.kubernetes.io/part-of: dashboard
    spec:
      containers:
        - name: backend
          image: ${BACKEND_IMAGE}
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 8090
          env:
            - name: HOME
              value: /root
            - name: DASHBOARD_HOST
              value: 0.0.0.0
            - name: DASHBOARD_PORT
              value: "8090"
            - name: DASHBOARD_MGMT_KUBECONFIG
              value: /root/.kube/config
            - name: DASHBOARD_CENTRAL_KUBECONFIG
              value: /root/.kube/config-central
            - name: DASHBOARD_REGIONAL_KUBECONFIG
              value: /root/.kube/config-regional
            - name: DASHBOARD_EDGE_KUBECONFIG
              value: /root/.kube/config-edge
          volumeMounts:
            - name: kubeconfigs
              mountPath: /root/.kube
              readOnly: true
          readinessProbe:
            httpGet:
              path: /api/v1/health
              port: 8090
            initialDelaySeconds: 3
            periodSeconds: 10
            timeoutSeconds: 3
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: kubeconfigs
          secret:
            secretName: dashboard-kubeconfigs
            defaultMode: 0400
EOF

cat >"${OUT_DIR}/21-service-backend.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: dashboard-backend
  namespace: dashboard
  labels:
    app.kubernetes.io/name: dashboard-backend
    app.kubernetes.io/part-of: dashboard
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: dashboard-backend
  ports:
    - name: http
      port: 8090
      targetPort: 8090
EOF

echo "==> Rendering frontend workloads..."
cat >"${OUT_DIR}/30-deployment-frontend.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dashboard-frontend
  namespace: dashboard
  labels:
    app.kubernetes.io/name: dashboard-frontend
    app.kubernetes.io/part-of: dashboard
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dashboard-frontend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dashboard-frontend
        app.kubernetes.io/part-of: dashboard
    spec:
      containers:
        - name: frontend
          image: ${FRONTEND_IMAGE}
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 80
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 3
            periodSeconds: 10
            timeoutSeconds: 2
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
EOF

cat >"${OUT_DIR}/31-service-frontend.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: dashboard-frontend
  namespace: dashboard
  labels:
    app.kubernetes.io/name: dashboard-frontend
    app.kubernetes.io/part-of: dashboard
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: dashboard-frontend
  ports:
    - name: http
      port: 80
      targetPort: 80
      nodePort: ${UI_NODEPORT}
EOF

echo "==> Rendered Multi-Cluster Resource Dashboard GitOps manifests to ${OUT_DIR}"
