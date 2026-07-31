# Prometheus metrics stack

Each lab cluster runs its own Prometheus (not a single hub). The dashboard queries that cluster’s Prom for usage series.

## Endpoints (operator network)

| Cluster | Prometheus UI / API | Status |
|---------|---------------------|--------|
| mgmt | `http://10.1.132.200:30909` | NodePort on CP mgmt IP |
| central | `http://10.1.132.210:30909` | |
| regional | `http://10.1.132.220:30909` | |
| edge | `http://10.1.132.230:30909` | Same pattern (needs healthy Flannel — see below) |

In-cluster: `http://prometheus.monitoring.svc:9090`.

```bash
# Manual UI / debug if NodePort is dark:
kubectl --context edge@edge -n monitoring port-forward svc/prometheus 9090
# then http://127.0.0.1:9090
```

Override URL for the dashboard backend:

- `DASHBOARD_<CLUSTER>_PROMETHEUS_URL` or `PROMETHEUS_URL_<CLUSTER>`
- `DASHBOARD_PROMETHEUS_NODEPORT` / `PROM_NODEPORT` (default `30909`)

## What scrapes what

Rendered by GitOps scripts into `repos/<cluster>/`:

| Piece | Script | Clusters | Role |
|-------|--------|----------|------|
| Prometheus | `scripts/render_prometheus_gitops.sh` | all four | TSDB + Kubernetes SD scrape |
| node_exporter | `scripts/render_node_exporter_gitops.sh` | all four | Host CPU/mem/NIC (`:9101`) |
| DCGM Service | `scripts/render_dcgm_scrape_gitops.sh` | central, edge | Annotated Service → `nvidia-dcgm-exporter:9400` |

node_exporter listens on **9101** (not 9100) so it does not collide with a host-installed exporter (seen on `edge-3`).

Prometheus discovers targets via:

- kubelet / cAdvisor (nodes)
- Service endpoints with `prometheus.io/scrape=true` **or** port name containing `metrics`
- Pods with `prometheus.io/scrape=true`

Pod/endpoint relabel sets a `node` label from `__meta_kubernetes_pod_node_name` so PromQL can filter by k8s node name (`usrp`, `edge-0`, …).

## Deploy / refresh

From repo root:

```bash
./scripts/render_prometheus_gitops.sh mgmt central regional edge
./scripts/render_node_exporter_gitops.sh mgmt central regional edge
./scripts/render_dcgm_scrape_gitops.sh central edge
./bringup/03_push_to_git_repos/push_git_repos.sh mgmt central regional edge
./scripts/check-configsync.sh mgmt central regional edge
```

Flannel (CNI) is a dependency for NodePort when the Prom pod is not on the control-plane node:

```bash
./scripts/render_flannel_gitops.sh edge   # or all clusters
./bringup/03_push_to_git_repos/push_git_repos.sh edge
```

## PromQL used by the dashboard (sketch)

| UI | Query idea |
|----|------------|
| CPU cores used | `(1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle",job="kubernetes-pods"}[5m]))) * count by (node) (...)` |
| Memory used | `node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes` (by `node`) |
| NIC Mbps | `rate(node_network_*_bytes_total{node="<name>"}[5m]) * 8 / 1e6` (physical ifaces only in UI) |
| GPU | `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED` / `TOTAL` (with `last_over_time` if scrape is stale) |

Prefer `job="kubernetes-pods"` so dual discovery (pod annotations + headless Service) does not double-count.

## Edge NodePort and Flannel

Operator access uses NodePort on the **control-plane mgmt IP** (`edge-0` → `10.1.132.230:30909`). kube-proxy then forwards to the Prometheus pod (often on `edge-2`). That path needs a working Flannel VXLAN overlay.

**Failure mode (fixed in GitOps):** Flannel advertised the wrong VTEP `public-ip` on physical workers when the site NIC had multiple addresses (e.g. `edge-3` `ens12f0`: `10.5.5.1` before `10.1.137.133`) or when annotations were stale (`edge-2` / `usrp`). Overlay to the Prom pod timed out → NodePort on `edge-0` timed out.

**Mitigation (no netplan change):** `scripts/render_flannel_gitops.sh` wraps `flanneld` with:

- `--iface-regex=…` (site NICs)
- `--iface-can-reach=<cluster site CP IP>`
- `--public-ip=${NODE_IP}` where `NODE_IP` = pod `status.hostIP` (= kubelet **InternalIP** on `.137`)

After rollout, every edge node’s `flannel.alpha.coreos.com/public-ip` should match its InternalIP:

```bash
kubectl --context edge@edge get nodes \
  -o custom-columns='NODE:.metadata.name,INTERNAL:.status.addresses[?(@.type=="InternalIP")].address,FLANNEL:.metadata.annotations.flannel\.alpha\.coreos\.com/public-ip'
```

The dashboard backend still falls back **NodePort → apiserver proxy → kubectl port-forward** if NodePort is unreachable.

## Known lab issues

| Symptom | Cause | Mitigation |
|---------|--------|------------|
| Edge `:30909` times out | Flannel `public-ip` ≠ site InternalIP; cross-node pod path broken | Re-render/push flannel; check annotations above |
| DCGM target `down` (context deadline) | Prom pod cannot scrape GH200 pod IP in time | Stale Prom series and/or dcgm-exporter port-forward for live GPU |
| “Collecting node_exporter samples…” | rate() window empty after exporter restart | 5m window + `sampled` flag; wait one scrape interval |
| node_exporter CrashLoop on `:9100` | Host already binds 9100 | Lab uses **9101** |

## Quick health check

```bash
for ip in 200 210 220 230; do
  echo -n "10.1.132.$ip:30909 "
  curl -s -o /dev/null -w '%{http_code}\n' --connect-timeout 2 \
    "http://10.1.132.$ip:30909/-/ready" || echo fail
done

kubectl --context edge@edge -n monitoring get deploy,ds,pods
# Overlay smoke (from CP to Prom pod IP):
# kubectl --context edge@edge -n monitoring get pod -l app.kubernetes.io/name=prometheus -o wide
```
