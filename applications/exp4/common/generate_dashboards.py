#!/usr/bin/env python3
"""Write Grafana dashboard JSON under each Exp4 app's dashboard/ dir.

Server (origin=server): CPU m, RAM MB, GPU %, VRAM MB (absolute)
Client (origin=client): DL throughput, application latency, transmission latency, E2E
"""

from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "influxdb", "uid": "dfufcr74lgwzkf"}
HERE = Path(__file__).resolve().parent
APPS = HERE.parent

APPS_META = (
    ("exp4_s1_iperf_sftp", "exp4-s1", "Exp4 S1 FTP"),
    ("exp4_s2_cctv", "exp4-s2", "Exp4 S2 CCTV / YOLO"),
    ("exp4_s3_ott", "exp4-s3", "Exp4 S3 OTT"),
    ("exp4_s4_cpu_offload", "exp4-s4", "Exp4 S4 CPU offload"),
    ("exp4_s5_iot", "exp4-s5", "Exp4 S5 MQTT"),
)


def flux(app_type: str, origin: str, field: str) -> str:
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" '
        f'and r.app_type == "{app_type}" and r.origin == "{origin}")\n'
        f'  |> filter(fn: (r) => r._field == "{field}")\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        "  |> yield()"
    )


def timeseries(pid: int, title: str, x: int, y: int, w: int, unit: str, query: str) -> dict:
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 12,
                    "lineInterpolation": "smooth",
                    "lineWidth": 2,
                    "showPoints": "auto",
                    "spanNulls": True,
                },
                "min": 0,
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 8, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "legend": {
                "calcs": ["lastNotNull", "mean", "max"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "datasource": DS,
                "query": query,
                "queryType": "flux",
                "refId": "A",
                "resultFormat": "time_series",
            }
        ],
        "title": title,
        "type": "timeseries",
        "interval": "1s",
    }


def dashboard(uid: str, title: str, app_type: str) -> dict:
    panels = [
        timeseries(1, "Server CPU (millicores)", 0, 0, 6, "suffix:m", flux(app_type, "server", "cpu_m")),
        timeseries(2, "Server RAM (MB)", 6, 0, 6, "decmbytes", flux(app_type, "server", "mem_mb")),
        timeseries(3, "Server GPU (%)", 12, 0, 6, "percent", flux(app_type, "server", "gpu_pct")),
        timeseries(4, "Server VRAM (MB)", 18, 0, 6, "decmbytes", flux(app_type, "server", "vram_mb")),
        timeseries(
            5,
            "Client DL throughput",
            0,
            8,
            6,
            "Mbps",
            flux(app_type, "client", "throughput_dl_mbps"),
        ),
        timeseries(
            6,
            "Client application latency",
            6,
            8,
            6,
            "ms",
            flux(app_type, "client", "latency_ms"),
        ),
        timeseries(
            7,
            "Client transmission latency",
            12,
            8,
            6,
            "ms",
            flux(app_type, "client", "tx_latency_ms"),
        ),
        timeseries(
            8,
            "Client E2E latency (app + tx)",
            18,
            8,
            6,
            "ms",
            flux(app_type, "client", "e2e_latency_ms"),
        ),
        timeseries(
            9,
            "Server DL TCP Send-Q / notsent / retrans (TO_CLIENT_IFACE)",
            0,
            16,
            12,
            "decbytes",
            (
                'from(bucket: "default")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                f'  |> filter(fn: (r) => r._measurement == "application_metrics" '
                f'and r.app_type == "{app_type}" and r.origin == "server")\n'
                '  |> filter(fn: (r) => r._field == "tcp_sendq_bytes" or '
                'r._field == "tcp_notsent_bytes" or r._field == "tcp_retrans")\n'
                "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
                '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_sendq_bytes" '
                'then "Send-Q" else if r._field == "tcp_notsent_bytes" then "notsent" '
                'else "retrans" }))\n'
                "  |> yield()"
            ),
        ),
        timeseries(
            10,
            "Server DL TCP cwnd / rwnd (TO_CLIENT_IFACE)",
            12,
            16,
            12,
            "short",
            (
                'from(bucket: "default")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                f'  |> filter(fn: (r) => r._measurement == "application_metrics" '
                f'and r.app_type == "{app_type}" and r.origin == "server")\n'
                '  |> filter(fn: (r) => r._field == "tcp_cwnd" or '
                'r._field == "tcp_rwnd_bytes")\n'
                "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
                '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_cwnd" '
                'then "cwnd" else "rwnd" }))\n'
                "  |> yield()"
            ),
        ),
        timeseries(
            11,
            "Client DL TCP Recv-Q / rwnd (TO_SERVER_IFACE)",
            0,
            24,
            24,
            "decbytes",
            (
                'from(bucket: "default")\n'
                "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
                f'  |> filter(fn: (r) => r._measurement == "application_metrics" '
                f'and r.app_type == "{app_type}" and r.origin == "client")\n'
                '  |> filter(fn: (r) => r._field == "tcp_recvq_bytes" or '
                'r._field == "tcp_rwnd_bytes")\n'
                "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
                '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_recvq_bytes" '
                'then "Recv-Q" else "rwnd" }))\n'
                "  |> yield()"
            ),
        ),
    ]
    return {
        "dashboard": {
            "editable": True,
            "fiscalYearStartMonth": 0,
            "graphTooltip": 1,
            "links": [],
            "panels": panels,
            "refresh": "5s",
            "schemaVersion": 40,
            "tags": ["exp4", app_type, "applications"],
            "templating": {"list": []},
            "time": {"from": "now-15m", "to": "now"},
            "timepicker": {"refresh_intervals": ["1s", "5s", "10s", "30s"]},
            "timezone": "browser",
            "title": title,
            "uid": uid,
            "version": 1,
        },
        "overwrite": True,
        "message": f"Exp4 {app_type}: 4+4 plus Send-Q/notsent and Recv-Q/rwnd",
    }


def main() -> None:
    for dirname, app_type, title in APPS_META:
        dest = APPS / dirname / "dashboard"
        dest.mkdir(parents=True, exist_ok=True)
        payload = dashboard(f"ina-{app_type}", title, app_type)
        path = dest / "grafana-dashboard.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (dest / "README.md").write_text(
            f"# {title} Grafana\n\n"
            f"Influx `application_metrics`, `app_type={app_type}`.\n\n"
            f"**Server** (`origin=server`): CPU m, RAM MB, GPU %, VRAM MB "
            f"(1000m = 1 full CPU; gpu_pct = 0–100%), plus DL TCP Send-Q / "
            f"notsent on `TO_CLIENT_IFACE` (`net1`) only.\n\n"
            f"**Client** (`origin=client`): DL throughput (RX on `TO_SERVER_IFACE`), "
            f"application latency (`t_send` → client), transmission latency "
            f"(ICMP RTT; TCP connect fallback), E2E = application + transmission, "
            f"plus TCP Recv-Q / rwnd on `TO_SERVER_IFACE`. "
            f"s2 application latency is `camera_ms + yolo_ms + rtsp_hls_ms`.\n\n"
            f"Import `{path.name}` into Grafana (`10.1.137.105:3000`).\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
