# Dashboard REST API

Base URL (local): `http://127.0.0.1:8090`

- Swagger UI: [/docs](http://127.0.0.1:8090/docs)
- OpenAPI: [/openapi.json](http://127.0.0.1:8090/openapi.json)

Vite frontend proxies `/api`, `/docs`, and `/openapi.json` to the backend.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/clusters` | Summaries for mgmt/central/regional/edge |
| GET | `/api/v1/clusters/{name}` | One cluster summary |
| GET | `/api/v1/clusters/{name}/nodes` | Nodes, Ready, capacity/allocatable, GPU count |
| GET | `/api/v1/clusters/{name}/pods` | Pods (`?namespace=`) |
| GET | `/api/v1/clusters/{name}/workloads` | Deployments + StatefulSets |
| GET | `/api/v1/clusters/{name}/metrics` | Chart aggregates; usage from Prometheus |
| GET | `/api/v1/clusters/{name}/nodes/{node}/interfaces` | Physical NIC rates + Prom history |
| GET | `/api/v1/topology` | React Flow nodes/edges |
| GET | `/api/v1/topology/layout` | Saved positions + viewport |
| PUT | `/api/v1/topology/layout` | Persist positions and/or viewport |

`{name}` must be one of: `mgmt`, `central`, `regional`, `edge`.

## Metrics payload (high level)

`GET /api/v1/clusters/{name}/metrics` → `resources`:

```json
{
  "source": "prometheus",
  "cpu_usage_cores": 12.3,
  "memory_usage_bytes": 1234567890,
  "cpu_allocatable_cores": 64,
  "memory_allocatable_bytes": ...,
  "nodes": [
    {
      "name": "usrp",
      "cpu_cores": 11.1,
      "memory_bytes": 21962240000,
      "sampled": true
    }
  ],
  "gpus": {
    "source": "prometheus",
    "nodes": [ ... ]
  }
}
```

- `sampled: false` — k8s node is known, but node_exporter has not produced a usable series yet.
- GPU `source` may be `prometheus` or `dcgm-exporter` (port-forward fallback).

## Interfaces payload

`GET /api/v1/clusters/{name}/nodes/{node}/interfaces`:

- `source`: `"prometheus"`
- `interfaces[]`: physical NICs only (`kind: physical`), with `rx_mbps` / `tx_mbps` (nullable)
- `history`: `{ labels: [...], series: { "<iface>": { rx_mbps: [], tx_mbps: [] } } }` for charts (`query_range`)

Non-finite rates are omitted / null (never JSON `NaN`).
