# INA-Infra — Planning Layer GUI

Web UI + Swagger REST API for **PlanningLayer (PL)**: edit slice SLAs, solve placement + Multus IP plan, apply profile manifests to lab Gitea.

PM / PS are stubbed (HTTP 501).

## Profiles

| Field | Default | Meaning |
|-------|---------|---------|
| `name` | `ina-infra` | K8s **namespace** on central/regional/edge |
| `subnet` | `10.1.140.0/24` | Multus macvlan CIDR (parallel to OAI `.139`) |
| `max_slices` | `16` | Cap for Add slice |
| `dnn_prefix` | `10.140` | DNN pool `10.140.<n>.0/24` |

**IP formula:** slice-scoped `host = base[role] + n` (n = 1..N). Shared AMF/SMF/CU-CP/DU/FlexRIC use fixed hosts. See `backend/app/services/ip_allocator.py`.

**Apply** purge+rewrites `repos/{central,regional,edge}-repo/namespaces/<profile>/` from [`templates/`](templates/) (NADs + IP ConfigMaps). On **central**, also emits a dedicated control-plane stack in that same profile namespace: MySQL + NRF/AUSF/UDM/UDR/AMF/SMF (`include_core=true` by default; shared `oai-cn` is untouched). Mgmt UI stays in `repos/mgmt/namespaces/ina-infra/` (UI only).

N6 GWs on `.140`: `./ina-infra/scripts/setup_ina_n6_gw.sh`

## Deploy (hybrid — current Named-User Gurobi)

```bash
cd /path/to/nephio-network-slicing

# Host API (Gurobi) — prefer systemd
sudo bash ina-infra/scripts/install-host-backend.sh
# or: ./ina-infra/run-backend.sh

# K8s UI
./ina-infra/scripts/build-and-push-images.sh
./scripts/render_ina_infra_gitops.sh mgmt
kubectl --context mgmt@mgmt -n ina-infra rollout restart deploy/ina-infra-frontend
```

| Service | URL |
|---------|-----|
| UI | http://10.1.132.200:30518 |
| API | http://10.1.132.200:8082/docs |

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/profiles` | List saved profiles |
| GET | `/api/v1/profiles/{name}` | Load profile + slices + last PL result |
| POST | `/api/v1/profiles` | Create profile |
| PUT | `/api/v1/profiles/{name}` | Save / upsert |
| DELETE | `/api/v1/profiles/{name}` | Remove (re-seeds `ina-infra` if empty) |
| GET | `/api/v1/profiles/default` | Builtin defaults (seed) |
| GET | `/api/v1/slices/defaults` | Default 4-slice SLAs (CCTV / Physical AI / OTT / IoT) |
| GET | `/api/v1/network` | Substrate settings |
| POST | `/api/v1/pl/solve` | PL + IP plan; persists on profile + JSON in `backend/results/` |
| POST | `/api/v1/pl/apply` | Render templates → repos/ (+ push) |

SQLite DB: `ina-infra/data/profiles.db` (`INA_DB_PATH`). PL run dumps: `ina-infra/backend/results/` (`INA_PL_RESULTS_DIR`). Gitea defaults: `http://10.1.132.200:3000` user `nephio` / `secret`.
