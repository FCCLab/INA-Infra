# Exp4 S4 CPU offload Grafana

Influx `application_metrics`, `app_type=exp4-s4`.

**Server** (`origin=server`): CPU m, RAM MB, GPU %, VRAM MB (1000m = 1 full CPU; gpu_pct = 0–100%).

**Client** (`origin=client`): DL throughput (RX on `TO_SERVER_IFACE`), latency (application E2E). s2 is `camera_ms + yolo_ms + rtsp_hls_ms`; other slices use `t_send` before app work then `t_recv - t_send`.

Import `grafana-dashboard.json` into Grafana (`10.1.137.105:3000`).
