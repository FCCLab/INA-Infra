# Edge InfluxDB + Grafana

Time-series stack on the **edge** cluster (site L2 `10.1.137.0/24`). Same addressing pattern as OpenSpeedTest: `/32` secondary on `edge-0` `enp7s0` + **hostPort** (not MetalLB — UPF N6 macvlan cannot reliably ARP MetalLB VIPs).

| Service | Address | Port | DNS |
|---------|---------|------|-----|
| InfluxDB 2.7 | [http://10.1.137.104:8086](http://10.1.137.104:8086) | 8086 | `influxdb.edge.inainfra` |
| Grafana 11 | [http://10.1.137.105:3000](http://10.1.137.105:3000) | 3000 | `grafana.edge.inainfra` |

IPs: [ip_plan.md](ip_plan.md). Constants: `CLUSTER_INFLUXDB_*` / `CLUSTER_GRAFANA_*` in [scripts/cluster_lib.sh](../scripts/cluster_lib.sh).

## Credentials (lab)

| | Value |
|---|---|
| Username | `inainfra` |
| Password | `inainfra` |
| InfluxDB org | `ina-infra` |
| InfluxDB bucket | `default` |
| InfluxDB token | `ina-infra-influxdb-token` |

Grafana ships with provisioned datasources:

- **InfluxDB** (default) → `http://influxdb.influxdb.svc:8086` (Flux, org `ina-infra`)
- **Prometheus** → `http://prometheus.monitoring.svc:9090`

## GitOps

```bash
./scripts/setup_influxdb_secondary_ips.sh edge   # /32 + mgmt-jump route
./scripts/setup_grafana_secondary_ips.sh edge
./scripts/render_influxdb_gitops.sh edge
./scripts/render_grafana_gitops.sh edge
./bringup/03_push_to_git_repos/push_git_repos.sh edge
./scripts/check-configsync.sh edge
```

Namespaces: `influxdb`, `grafana` under `repos/edge-repo/namespaces/`.

PVC: InfluxDB **1900Gi** on `edge-0`’s 2T `local-path` disk (`INFLUX_PVC_SIZE`; leaves headroom for Grafana/Prometheus).

Re-run the `setup_*_secondary_ips.sh` scripts after `edge-0` reboot (the `/32` is not netplan-persistent).

## Verify

```bash
curl -sS http://10.1.137.104:8086/health
curl -sS -u inainfra:inainfra http://10.1.137.105:3000/api/org
kubectl --context edge@edge -n influxdb get deploy,pods,svc,pvc
kubectl --context edge@edge -n grafana get deploy,pods,svc,pvc
```
