# ina-infra operator agent

Side-car client inside **`oai-ran-operator`** that opens a **WebSocket** to the ina-infra API, declares NFs + controllable compute kinds, receives pushed **desired** targets, and applies **CPU** in place on live pods. Used by the ina-infra **Operators** UI for live CU-CP / CU-UP / DU sizing on the lab testbed (typically `oai-benchmark` on **edge**).

Source: [`internal/inainfra/`](../../ina-infra-oai-ran-controller/internal/inainfra/) in the RAN controller submodule. Wired from `cmd/main.go` when not disabled.

## Control model

The **controller connects to the ina-infra backend over WebSocket**, declares what it has, and lets the backend drive control:

1. **Connect** — agent dials `ws://…/api/v1/operators/ws` (from `INA_INFRA_API_URL`) and keeps the session open (reconnect with backoff on drop).
2. **Declare** — agent sends `type=declare` with discovered NFs, live resource quantities, and per-NF **`controllable`**. Capabilities come from the controller, not from the UI.
3. **Control** — UI / planner write desired targets via HTTP; the backend accepts only kinds listed in that NF’s `controllable`, then **pushes** `type=desired` on the WebSocket. The agent applies and replies with `type=apply_report`.

The frontend does **not** invent limits: it renders rows from `nf.controllable` returned by the HTTP list API (which mirrors the last agent declare).

### Transport (who uses what)

| Link | Transport | Role |
|------|-----------|------|
| **Operator agent ↔ ina-infra backend** | **WebSocket** `WS /api/v1/operators/ws` | Agent connects, declares NFs + `controllable`, receives desired, sends apply-report |
| **UI / planner / tooling ↔ backend** | **REST (HTTP)** `/api/v1/operators…` | **Control** connected operators: list, get, set desired resources, forget |

WebSocket is the agent’s live channel. **Controlling** what a connected operator should do (CPU/RAM targets, etc.) stays on the **REST API** — e.g. `PUT /operators/{id}/nfs/{nf}/resources`. The backend then pushes `desired` on that agent’s WebSocket.

Agent↔backend is **not** HTTP poll anymore. Deprecated HTTP `register` / `desired` / `apply-report` remain only for tests and manual tooling.

## Role

| Piece | Role |
|-------|------|
| Operator agent (this) | WebSocket client — connect, declare, apply, report |
| ina-infra API (`:8082`) | Server — WS for agents; **REST to control** connected operators |
| ina-infra **Operators** tab | REST client — edit Limit/Request for kinds the controller advertised |

The Kubernetes RAN reconciler still **create-once** `NFDeployment`s (see [architecture.md](architecture.md)). Online CPU changes go through the agent, **not** CR recreate.

```mermaid
sequenceDiagram
  participant UI as ina-infra UI
  participant API as ina-infra API
  participant Ag as operator agent
  participant Pod as RAN pod

  Ag->>API: WebSocket /operators/ws
  Ag->>API: declare (NFs + live + controllable)
  Note over API: Backend stores what controller declared
  API-->>Ag: welcome + desired (snapshot)
  UI->>API: PUT .../nfs/{nf}/resources (REST control)
  Note over API: REST sets intent; WS delivers it to agent
  API-->>Ag: desired (push)
  Ag->>Pod: patch CPU (in-place) if changed
  Note over Ag: RAM: log hook only (still controllable)
  Ag->>API: apply_report
```

## WebSocket protocol

Endpoint: **`WS /api/v1/operators/ws`**

### Agent → server

| `type` | Purpose |
|--------|---------|
| `declare` (or `hello`) | Register / refresh: `id`, `cluster`, `namespace`, `version`, `nfs[]`, `message` |
| `apply_report` | Apply result: `nf`, `generation`, `ok`, resource fields, `message` |
| `ping` | Optional keepalive → server replies `pong` |

### Server → agent

| `type` | Purpose |
|--------|---------|
| `welcome` | First successful declare for this socket (`id`) |
| `desired` | Full target map (`id`, `targets`) — after declare and after each UI set |
| `error` | Validation / protocol error (`message`) |
| `pong` | Reply to `ping` |

Agent version: **0.3.0+** (WebSocket). Older agents used HTTP poll (`register` / `desired` / `apply-report`); those HTTP routes remain as **deprecated** back-compat for tests/tooling.

## Enable / disable

| Env | Default | Meaning |
|-----|---------|---------|
| `INA_INFRA_API_URL` | `http://10.1.132.200:8082` | HTTP base; agent derives `ws://…/api/v1/operators/ws`. Set to `-` or `disabled` to turn the agent off. |
| `INA_OPERATOR_ID` | `{cluster}-{namespace}` | Stable agent id (e.g. `edge-oai-benchmark`) |
| `INA_OPERATOR_CLUSTER` | `edge` | Cluster label reported to ina-infra |
| `INA_OPERATOR_NAMESPACE` | `oai-benchmark` | Namespace whose Deployments are discovered |
| `INA_OPERATOR_POLL_SEC` | `5` | Interval to re-`declare` live NF inventory (not used for desired polling) |

Benchmark GitOps (`scripts/render_oai_benchmark_gitops.sh`) sets these on `deploy/oai-ran-operator` in `oai-benchmark`. Lab image tag often `10.1.132.30:5000/oai-ran-controller:cpuagent` (see [build.md](build.md)).

```bash
# Rebuild / push agent image
export OAI_RAN_CONTROLLER_TAG=cpuagent
./third_party/ran_operator/scripts/build_ran_operator_image.sh
kubectl --context edge@edge -n oai-benchmark rollout restart deploy/oai-ran-operator
```

## Discovery

On each declare the agent lists Deployments in `INA_OPERATOR_NAMESPACE` and keeps names classified as:

| Kind | Name match |
|------|------------|
| `cuup` | contains `cu-up` / `cuup` |
| `cucp` | contains `cu-cp` / `cucp` |
| `du` | `oai-du`, suffix `-du`, or `du` |

**Live resources** are read from the first ready (or any) matching **pod** `spec.containers[0].resources` (in-place resize updates the pod, not the Deployment template). Fallback: Deployment pod template.

Each NF also reports **`controllable`**: which compute kinds the agent accepts for that NF. Today every discovered NF advertises `["cpu", "memory"]`. GPU/VRAM are reported as live quantities when present but are **not** controllable until the agent adds them to the list.

Reported quantities:

- CPU / memory — standard `cpu`, `memory`
- GPU — `nvidia.com/gpu`
- VRAM — `nvidia.com/gpumem`

## API (ina-infra) — REST control + agent WebSocket

Base path: `/api/v1/operators`.

**REST** is how clients control connected operators (UI, scripts, planners). **WebSocket** is how the operator agent stays connected and receives those controls.

| Method | Path | Who | Purpose |
|--------|------|-----|---------|
| `WS` | `/operators/ws` | Agent | Connect / declare / receive desired / apply-report |
| `GET` | `/operators` | UI / REST | List connected agents |
| `GET` | `/operators/{id}` | UI / REST | One agent |
| `PUT` | `/operators/{id}/nfs/{nf}/resources` | UI / REST | **Set desired** compute (partial OK; must be controllable; pushes WS `desired`) |
| `PUT` | `/operators/{id}/nfs/{nf}/cpu` | UI / REST | Deprecated alias of `/resources` |
| `DELETE` | `/operators/{id}` | UI / REST | Forget registration |
| `POST` | `/operators/register` | — | Deprecated HTTP declare |
| `GET` | `/operators/{id}/desired` | — | Deprecated HTTP poll |
| `POST` | `/operators/{id}/apply-report` | — | Deprecated HTTP report |

### Desired target

Per NF the API stores limit/request for controllable kinds (CPU, RAM today), plus:

- `generation` — increments on each UI set
- `changed_fields` — keys present in that set (partial apply)
- `updated_at`

Setting a non-controllable kind (e.g. `gpu_limit` while `controllable` is only cpu/memory) returns **400**.

Agents are **online** while the WebSocket is connected (also briefly via `last_seen` after declare if the socket just dropped).

### Partial update

UI / API may send only changed fields, e.g. `{"cpu_limit":"180m"}`. The registry merges into the previous target and records `changed_fields: ["cpu_limit"]`. The agent applies **only** those fields (so a prior bad `cpu_request` is not re-patched).

Quantities: use Kubernetes forms (`200m`, `1`, `512Mi`, `8Gi`). A bare `50` for CPU means **50 cores**, not `50m`.

## Apply semantics

| Resource | Controllable now | Behavior |
|----------|------------------|----------|
| **CPU** | yes | Patch live pod `resources` + `resizePolicy` (`NotRequired`) when `InPlacePodVerticalScaling` is on. Does **not** update Deployment template (avoids Recreate). |
| **RAM** | yes | Accepted by API/UI; **logged** as `resource hook (not applied)` only |
| **GPU** | no | Hidden in UI until agent advertises `gpu` |
| **VRAM** | no | Hidden in UI until agent advertises `vram` |

After each attempt (ok or error) the agent advances its last-applied generation and sends `apply_report`, so a failed generation cannot block a newer UI apply. Failures show as `apply_status: error` with `apply_message` (ina-infra Operators status rail).

Requires cluster feature gate **`InPlacePodVerticalScaling`** on the workload cluster (edge lab: apiserver / controller-manager / kubelet). Without it, in-place CPU patches fail.

## UI

ina-infra **Operators** tab (`http://10.1.132.200:30518` or local Vite `:5180`):

- Left: per-NF allocator — rows follow `nf.controllable` from the agent (via HTTP list API), **edit / current**, per-row **Apply** (HTTP `PUT …/resources` → backend pushes WS `desired`)
- Right: **Status** rail — online/offline, last seen, cluster/namespace/version, agent **Message** (e.g. `websocket declare`), NF apply status, error box

Dev API is usually the host systemd/uvicorn backend on `:8082` (Gurobi); the k8s frontend proxies to that host Service/Endpoints.

## Logs

```bash
export KUBECONFIG=~/.kube/config-edge
kubectl --context edge@edge -n oai-benchmark logs -l app.kubernetes.io/name=oai-ran-operator -f \
  | grep -E 'operator-agent|websocket|resource hook|applied|apply resources|patch pod'
```

Useful messages:

- `starting ina-infra operator agent` — agent running (shows `ws` URL)
- `websocket welcome` — session accepted
- `applied resources` / `in-place pod CPU patched` — CPU ok
- `resource hook (not applied)` — RAM/GPU/VRAM stub
- `websocket session ended; reconnecting` — backend down or network blip
- `patch pod CPU` / `apply resources` **ERROR** — see message (e.g. request > limit)

## Verify

```bash
# Agent online via WebSocket?
curl -sS http://10.1.132.200:8082/api/v1/operators | jq .

# Set CPU limit only (backend pushes desired on WS)
curl -sS -X PUT \
  http://10.1.132.200:8082/api/v1/operators/edge-oai-benchmark/nfs/oai-cu-up/resources \
  -H 'Content-Type: application/json' \
  -d '{"cpu_limit":"200m"}'

# Live pod resources
kubectl --context edge@edge -n oai-benchmark get pod -l app.kubernetes.io/name=oai-cu-up \
  -o jsonpath='{.items[0].spec.containers[0].resources}{"\n"}'
```

## Limitations

1. **CPU only** for real apply; RAM/GPU/VRAM are hooks until implemented.
2. **Create-once NFDeployment** still owns initial resources; agent patches pods after create.
3. In-memory API registry resets when the ina-infra backend process restarts (agents reconnect and re-declare).
4. Extended resources (GPU/VRAM) typically need a device plugin and often a pod restart when apply is enabled later.
