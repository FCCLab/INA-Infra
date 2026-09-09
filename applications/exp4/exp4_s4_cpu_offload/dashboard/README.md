# Exp4 S4 CPU offload Grafana

Influx `application_metrics`, `app_type=exp4-s4`.

**Server** (`origin=server`): CPU m, RAM MB, GPU %, VRAM MB (1000m = 1 full CPU; gpu_pct = 0–100%), plus DL TCP Send-Q / notsent on `TO_CLIENT_IFACE` (`net1`) only.

**Client** (`origin=client`): DL throughput (RX on `TO_SERVER_IFACE`), application latency (`t_send` → client), transmission latency (ICMP RTT; TCP connect fallback), E2E = application + transmission, plus TCP Recv-Q / rwnd on `TO_SERVER_IFACE`. s2 application latency is `camera_ms + yolo_ms + rtsp_hls_ms`.

Import `grafana-dashboard.json` into Grafana (`10.1.137.105:3000`).
