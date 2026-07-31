# Multi-Cluster Dashboard

Standalone ops console for the Nephio lab: **React Flow** topology + **Chart.js** resource charts, backed by a **FastAPI** REST API with Swagger.

Queries **mgmt / central / regional / edge** via local kubeconfigs. **Usage** (CPU, memory, GPU, NIC) comes from each cluster’s **Prometheus**; inventory still comes from the Kubernetes API.

## Documentation

Full system docs live under **[docs/](docs/)**:

| Doc | Topic |
|-----|--------|
| [docs/readme.md](docs/readme.md) | Index |
| [docs/architecture.md](docs/architecture.md) | Components and data flow |
| [docs/prometheus.md](docs/prometheus.md) | Per-cluster Prom, node_exporter, DCGM, GitOps |
| [docs/api.md](docs/api.md) | REST API |
| [docs/operations.md](docs/operations.md) | Run, env vars, troubleshooting |

## Quick start

```bash
# Backend → http://127.0.0.1:8090/docs
cd dashboard/backend
pip3 install --user -r requirements.txt
./run.sh

# Frontend → http://127.0.0.1:5174
cd dashboard/frontend
npm install
npm run dev
```

Kubeconfigs and Prometheus URLs: see [docs/operations.md](docs/operations.md) and [docs/prometheus.md](docs/prometheus.md).

Vite proxies `/api`, `/docs`, and `/openapi.json` to `http://127.0.0.1:8090`.
