# Dashboard operations

## Prerequisites

Kubeconfigs on the operator host:

| Cluster | Path | Context |
|---------|------|---------|
| mgmt | `~/.kube/config` | `mgmt@mgmt` |
| central | `~/.kube/config-central` | `central@central` |
| regional | `~/.kube/config-regional` | `regional@regional` |
| edge | `~/.kube/config-edge` | `edge@edge` |

Overrides (first match wins): `DASHBOARD_<CLUSTER>_KUBECONFIG`, `KUBECONFIG_<CLUSTER>`, `INA_<CLUSTER>_KUBECONFIG`.

Per-cluster Prometheus + node_exporter must be deployed (see [prometheus.md](prometheus.md)). Edge NodePort also needs correct Flannel `public-ip` (= kube InternalIP).

## Run (dev)

```bash
# Backend — Swagger at http://127.0.0.1:8090/docs
cd dashboard/backend
pip3 install --user -r requirements.txt
./run.sh
# optional: DASHBOARD_PORT=8090 DASHBOARD_HOST=0.0.0.0 ./run.sh

# Frontend — http://127.0.0.1:5174
cd dashboard/frontend
npm install
npm run dev
```

Vite proxies `/api` → `http://127.0.0.1:8090`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `DASHBOARD_PORT` / `DASHBOARD_HOST` | Backend bind (default `8090` / `0.0.0.0`) |
| `DASHBOARD_<CLUSTER>_KUBECONFIG` | Kubeconfig path override |
| `DASHBOARD_<CLUSTER>_PROMETHEUS_URL` | Full Prom base URL override |
| `PROMETHEUS_URL_<CLUSTER>` | Same |
| `DASHBOARD_PROMETHEUS_NODEPORT` / `PROM_NODEPORT` | Default `30909` |
| `DASHBOARD_<CLUSTER>_MGMT_IP` | Override CP mgmt IP used for NodePort URL |

## Layout persistence

Topology cluster positions and viewport are stored under `dashboard/data/topology_layout.json` (local to the backend process host).

## Troubleshooting

### Backend cannot reach a cluster

```bash
kubectl --kubeconfig ~/.kube/config-edge --context edge@edge get nodes
```

Check kubeconfig path/context and that the API (`:6443` on site IP) is reachable from the operator host.

### Metrics empty / “Collecting node_exporter samples”

1. Confirm node_exporter pods:  
   `kubectl --context <ctx> -n monitoring get ds,pods -l app.kubernetes.io/name=node-exporter`
2. Confirm Prom has series:  
   `count by (node) (node_cpu_seconds_total{mode="idle"})`
3. Wait ~1–2 scrape intervals after a DaemonSet rollout (`5m` rate window).

### Prometheus NodePort times out (especially edge)

1. Confirm Prom pod is Ready:  
   `kubectl --context edge@edge -n monitoring get pods -o wide`
2. Hit NodePort from the operator host:  
   `curl -sS --connect-timeout 3 http://10.1.132.230:30909/-/ready`
3. If the pod is on a worker and NodePort on the CP fails, check Flannel public IPs match InternalIPs (`.137`):

```bash
kubectl --context edge@edge get nodes \
  -o custom-columns='NODE:.metadata.name,INTERNAL:.status.addresses[?(@.type=="InternalIP")].address,FLANNEL:.metadata.annotations.flannel\.alpha\.coreos\.com/public-ip'
```

Mismatch → re-render/push Flannel (`./scripts/render_flannel_gitops.sh edge`) and restart `kube-flannel-ds` pods. See [prometheus.md](prometheus.md#edge-nodeport-and-flannel).

4. Dashboard fallback (automatic): apiserver proxy, then `kubectl port-forward svc/prometheus`.

Manual port-forward:

```bash
kubectl --context edge@edge -n monitoring port-forward svc/prometheus 9090
```

### GPU gauges stuck / no DCGM

```bash
# Prom UI → targets → kubernetes-service-endpoints (dcgm), or:
curl -sG 'http://10.1.132.210:30909/api/v1/query' \
  --data-urlencode 'query=DCGM_FI_DEV_GPU_UTIL'
```

If the DCGM target is `down`, the backend may still show live values via dcgm-exporter port-forward.

### Slow `/metrics` on edge

GPU dcgm port-forward and (when used) Prom port-forward add latency. Prefer a long-lived backend process during demos.

## Related lab docs

- Cluster IPs / contexts: [docs/testbed.md](../../docs/testbed.md), [docs/ip_plan.md](../../docs/ip_plan.md)
- Config Sync push: [docs/config_sync.md](../../docs/config_sync.md)
- Flannel render: `scripts/render_flannel_gitops.sh`
- In-cluster K8s Dashboard (different product): `scripts/render_dashboard_gitops.sh`
