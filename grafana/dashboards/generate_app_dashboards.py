#!/usr/bin/env python3
"""Write per-application Grafana dashboards (InfluxDB Flux)."""
from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "influxdb", "uid": "dfufcr74lgwzkf"}
OUT = Path(__file__).resolve().parent


def flux(app_type: str, field_pred: str, extra: str = "") -> str:
    extra_f = f"\n  |> filter(fn: (r) => {extra})" if extra else ""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        f"  |> filter(fn: (r) => {field_pred})"
        f"{extra_f}\n"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        "  |> yield()"
    )


def flux_per_ue_latency(app_type: str) -> str:
    """Per-UE latency (legend: sliceN-<app>-client-M).

    IoT/OTT/Physical AI measure RTT on the UE sidecar (origin=client).
    CCTV e2e (capture → YOLO) is measured on the analyzer and scraped as
    origin=server with a ue_id tag — the UE only exports encode time.
    """
    if app_type == "cctv":
        origin = "server"
        extra = '  |> filter(fn: (r) => exists r.ue_id and r.ue_id != "")\n'
        field = 'r._field == "latency_ms" or r._field == "e2e_delay_ms"'
    else:
        origin = "client"
        extra = ""
        field = 'r._field == "latency_ms"'
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        f"  |> filter(fn: (r) => {field})\n"
        f'  |> filter(fn: (r) => r.origin == "{origin}")\n'
        f"{extra}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if exists r.app_name and r.app_name != "" then r.app_name else r.ue_id }))\n'
        "  |> yield()"
    )


def flux_per_ue_throughput(app_type: str) -> str:
    """5G PDU (oaitun) rate reported by each UE client sidecar."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        '  |> filter(fn: (r) => r._field == "throughput_mbps")\n'
        '  |> filter(fn: (r) => r.origin == "client")\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if exists r.app_name and r.app_name != "" then r.app_name else r.ue_id }))\n'
        "  |> yield()"
    )


def flux_agg_latency(app_type: str) -> str:
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        '  |> filter(fn: (r) => r._field == "latency_ms")\n'
        '  |> filter(fn: (r) => r.origin == "server")\n'
        '  |> filter(fn: (r) => not exists r.ue_id or r.ue_id == "")\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        "  |> yield()"
    )


def flux_agg_throughput(app_type: str) -> str:
    """Sum of per-UE 5G interface throughput as a single Grafana series."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        '  |> filter(fn: (r) => r._field == "throughput_mbps")\n'
        '  |> filter(fn: (r) => r.origin == "client")\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> group(columns: ["_time"])\n'
        "  |> sum()\n"
        "  |> group()\n"
        '  |> set(key: "_field", value: "aggregated")\n'
        '  |> set(key: "_measurement", value: "application_metrics")\n'
        "  |> yield()"
    )


def timeseries(pid: int, title: str, x: int, y: int, w: int, h: int, query: str, unit: str = "short") -> dict:
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
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
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
    }


def stat(pid: int, title: str, x: int, y: int, query: str, unit: str = "short") -> dict:
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
            "overrides": [],
        },
        "gridPos": {"h": 4, "w": 6, "x": x, "y": y},
        "id": pid,
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "textMode": "auto",
        },
        "targets": [{"datasource": DS, "query": query, "refId": "A"}],
        "title": title,
        "type": "stat",
    }


def dashboard(uid: str, title: str, tags: list[str], panels: list[dict], refresh: str = "5s") -> dict:
    return {
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "panels": panels,
        "refresh": refresh,
        "schemaVersion": 40,
        "tags": tags,
        "templating": {"list": []},
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {"refresh_intervals": ["1s", "2s", "5s", "10s", "30s", "1m"]},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
    }


def wrap(dash: dict) -> dict:
    return {"dashboard": dash, "overwrite": True, "message": "INA-Infra per-application dashboard"}


def write(name: str, payload: dict) -> None:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def metrics_panels(app_type: str) -> list[dict]:
    return [
        timeseries(1, "Per-UE latency", 0, 0, 12, 8, flux_per_ue_latency(app_type), "ms"),
        timeseries(2, "Per-UE throughput (5G)", 12, 0, 12, 8, flux_per_ue_throughput(app_type), "Mbps"),
        timeseries(3, "Aggregated latency", 0, 8, 12, 8, flux_agg_latency(app_type), "ms"),
        timeseries(4, "Aggregated throughput (5G)", 12, 8, 12, 8, flux_agg_throughput(app_type), "Mbps"),
    ]


def overview_panels(app_type: str, extra_fields: list[tuple[str, str, str]]) -> list[dict]:
    panels = [
        stat(1, "Throughput (Mbps)", 0, 0, flux(app_type, 'r._field == "throughput_mbps"', extra='r.origin == "client"'), "Mbps"),
    ]
    for i, (title, pred, unit) in enumerate(extra_fields, start=2):
        panels.append(stat(i, title, (i - 1) * 6, 0, flux(app_type, pred), unit))
    panels.extend(
        [
            timeseries(
                10,
                "Throughput",
                0,
                4,
                12,
                8,
                flux(app_type, 'r._field =~ /throughput/'),
                "Mbps",
            ),
            timeseries(
                11,
                "Key series",
                12,
                4,
                12,
                8,
                flux(app_type, extra_fields[0][1] if extra_fields else "true"),
            ),
        ]
    )
    return panels


def main() -> None:
    write(
        "cctv-metrics.json",
        wrap(
            dashboard(
                "ina-cctv-metrics",
                "CCTV Metrics",
                ["cctv", "slice1", "metrics", "applications"],
                metrics_panels("cctv"),
            )
        ),
    )
    write(
        "physical-ai-metrics.json",
        wrap(
            dashboard(
                "ina-physical-ai",
                "Physical AI Metrics",
                ["physical_ai", "slice2", "vllm", "applications"],
                metrics_panels("physical_ai"),
            )
        ),
    )
    write(
        "ott-dashboard.json",
        wrap(
            dashboard(
                "ina-ott",
                "OTT Dashboard",
                ["ott", "slice3", "applications"],
                overview_panels(
                    "ott",
                    [
                        ("FPS", 'r._field =~ /hdstream_fps/ or r._field == "hdstream_fps"', "fps"),
                        ("Encode / net delay", 'r._field =~ /hdstream_.*delay/ or r._field =~ /encode/', "s"),
                        ("Frames", 'r._field =~ /frames/', "short"),
                    ],
                ),
            )
        ),
    )
    write(
        "ott-metrics.json",
        wrap(
            dashboard(
                "ina-ott-metrics",
                "OTT Metrics",
                ["ott", "slice3", "metrics", "applications"],
                metrics_panels("ott"),
            )
        ),
    )
    write(
        "iot-dashboard.json",
        wrap(
            dashboard(
                "ina-iot",
                "IoT Dashboard",
                ["iot", "slice4", "applications"],
                overview_panels(
                    "iot",
                    [
                        ("MQTT connected", 'r._field =~ /sliced_mqtt_connected/', "short"),
                        ("UL/DL delay", 'r._field =~ /sliced_.*delay/', "s"),
                        ("Messages", 'r._field =~ /sliced_.*messages/', "short"),
                    ],
                ),
            )
        ),
    )
    write(
        "iot-metrics.json",
        wrap(
            dashboard(
                "ina-iot-metrics",
                "IoT Metrics",
                ["iot", "slice4", "metrics", "applications"],
                metrics_panels("iot"),
            )
        ),
    )


if __name__ == "__main__":
    main()
