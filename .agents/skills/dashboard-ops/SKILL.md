---
name: dashboard-ops
description: >-
  Procedures for building, deploying, configuring caching, and troubleshooting
  the NeuroRAN Multi-Cluster Resource Dashboard (FastAPI :8090, React/Nginx :80 / NodePort :30574).
---

# NeuroRAN Resource Dashboard Operations Skill

Architecture, build pipelines, caching policies, and troubleshooting for the multi-cluster resource dashboard running under `dashboard/` on `mgmt@mgmt`.

---

## 1. Architecture & Services

- **`dashboard-backend`** (FastAPI `:8090` on `mgmt@mgmt`):
  - Queries `mgmt`, `central`, `regional`, and `edge` via local kubeconfig secrets.
  - Multi-cluster inventory summary (`/api/v1/clusters`), topology graph (`/api/v1/topology`), GPU metrics, and Prometheus time-series.
  - **Thread-safe TTL Caching** in `dashboard/backend/app/services/inventory.py` (5-second cache) prevents API connection stampedes.
- **`dashboard-frontend`** (React + Nginx `:80` / NodePort `:30574`):
  - Accessible at `http://10.1.132.200:30574/`.
  - Nginx proxies `/api/*` to `dashboard-backend:8090` with 120s timeout and buffering disabled for real-time streams.

---

## 2. Build & Deploy Workflow

```bash
cd /home/fcp/INA-Infra

# 1. Build backend and frontend container images:
./dashboard/scripts/build-and-push.sh

# 2. If registry push requires mgmt host docker:
docker save 10.1.132.30:5000/dashboard/backend:latest 10.1.132.30:5000/dashboard/frontend:latest | ssh -F utils/ssh_config/config mgmt-0 "docker load && docker push 10.1.132.30:5000/dashboard/backend:latest && docker push 10.1.132.30:5000/dashboard/frontend:latest"

# 3. Restart dashboard deployments on mgmt cluster:
kubectl --context=mgmt@mgmt rollout restart deployment/dashboard-backend deployment/dashboard-frontend -n dashboard

# 4. Verify rollout:
kubectl --context=mgmt@mgmt rollout status deployment/dashboard-backend deployment/dashboard-frontend -n dashboard --timeout=45s
```

---

## 3. Diagnostics & Troubleshooting

```bash
# Check Dashboard pods:
kubectl --context=mgmt@mgmt get pods -n dashboard -o wide

# Test Health Endpoint:
curl -i http://10.1.132.200:30574/api/v1/health

# Test Topology API:
curl -s http://10.1.132.200:30574/api/v1/topology | jq .

# Backend logs:
kubectl --context=mgmt@mgmt logs -n dashboard -l app.kubernetes.io/name=dashboard-backend --tail=30
```
