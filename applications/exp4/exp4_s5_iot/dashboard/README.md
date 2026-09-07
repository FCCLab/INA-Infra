# Exp4 S5 MQTT Grafana

Influx `application_metrics`, `app_type=exp4-s5`.

**Server** (`origin=server`): CPU m, RAM MB, GPU %, VRAM MB (1000m = 1 full CPU; gpu_pct = 0–100%).

**Client** (`origin=client`): DL throughput (RX on `TO_SERVER_IFACE`), latency (ping to server Multus IP via `TO_SERVER_IFACE`).

Import `grafana-dashboard.json` into Grafana (`10.1.137.105:3000`).
