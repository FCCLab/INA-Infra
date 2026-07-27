# INA-Infra — Planning Layer GUI

Web UI + Swagger REST API for **PlanningLayer (PL)**: edit slice SLAs, solve placement, push planning intent to lab Gitea.

PM / PS are stubbed (HTTP 501).

**Apply note:** Push writes `namespaces/ina-planning/` into GitOps repos. Config Sync applies ConfigMaps; it does **not** yet relocate OAI NFs from PL placement.

## Deploy (hybrid — current Named-User Gurobi)

Gurobi Named-User licenses cannot run in containers. Run the API on the host; UI in Kubernetes.

```bash
cd /path/to/nephio-network-slicing

# Terminal A — host API (Gurobi)
./ina-infra/run-backend.sh

# Terminal B — K8s UI
./ina-infra/scripts/build-and-push-images.sh
./scripts/render_ina_infra_gitops.sh mgmt
kubectl --context mgmt@mgmt -n ina-infra delete deploy ina-infra-backend --ignore-not-found
kubectl --context mgmt@mgmt apply -f repos/mgmt/namespaces/ina-infra/
# optional GitOps: ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'ina-infra' mgmt
```

| Service | URL |
|---------|-----|
| UI | http://10.1.132.200:30518 |
| API | http://10.1.132.200:8082/docs |

Details: [k8s/README.md](k8s/README.md).

Persistent host API (optional):

```bash
sudo ./ina-infra/scripts/install-host-backend.sh
```

## Local Docker Compose (dev UI only)

```bash
./run-backend.sh
docker compose up -d --build   # UI :5180 → host :8082
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/network` | Substrate settings |
| GET | `/api/v1/slices/defaults` | Default 8-slice SLAs |
| POST | `/api/v1/pl/solve` | Run PlanningLayer |
| POST | `/api/v1/pl/apply` | Render intent + push Gitea (`dry_run` supported) |
| POST | `/api/v1/pm/solve` | 501 stub |
| POST | `/api/v1/ps/solve` | 501 stub |

Gitea defaults: `http://10.1.132.200:3000` user `nephio` / `secret`.
