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


def _latency_source(app_type: str) -> tuple[str, str, str]:
    """origin, extra filter, field predicate for per-UE latency points.

    CCTV e2e, IoT MQTT uplink, and Physical AI HTTP uplink are measured on
    the server (ue_id tag). OTT still uses the UE sidecar (origin=client).
    """
    if app_type in ("cctv", "iot", "physical_ai"):
        return (
            "server",
            '  |> filter(fn: (r) => exists r.ue_id and r.ue_id =~ /client-/)\n',
            'r._field == "latency_ms" or r._field == "e2e_delay_ms"',
        )
    return (
        "client",
            '  |> filter(fn: (r) => exists r.ue_id and r.ue_id =~ /client-/)\n',
        'r._field == "latency_ms"',
    )


def flux_per_ue_latency(app_type: str) -> str:
    """Per-UE latency (legend: sliceN-<app>-client-M)."""
    origin, extra, field = _latency_source(app_type)
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


def flux_per_ue_throughput(app_type: str, direction: str | None = None) -> str:
    """Per-UE 5G PDU on oaitun. direction is 'ul' (TX), 'dl' (RX), or both."""
    if direction == "ul":
        field = 'r._field == "throughput_ul_mbps"'
        suffix = ""
    elif direction == "dl":
        field = 'r._field == "throughput_dl_mbps"'
        suffix = ""
    else:
        field = 'r._field == "throughput_ul_mbps" or r._field == "throughput_dl_mbps"'
        suffix = ' + " " + (if r._field == "throughput_ul_mbps" then "UL" else "DL")'
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        f"  |> filter(fn: (r) => {field})\n"
        '  |> filter(fn: (r) => r.origin == "client")\n'
        '  |> filter(fn: (r) => exists r.ue_id and r.ue_id =~ /client-/)\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        f'  |> map(fn: (r) => ({{ r with _field: (if exists r.app_name and r.app_name != "" then r.app_name else r.ue_id){suffix} }}))\n'
        "  |> yield()"
    )


def flux_avg_latency(app_type: str) -> str:
    """Mean of per-UE latency (equal weight per UE), same layout as throughput.

    Throughput aggregated sums per-UE oaitun rates. Latency averages the same
    per-UE series instead of using the MQTT/server ``app_latency_ms`` gauge
    (message-weighted, no ue_id). Do not fill(usePrevious): a stopped UE's last
    delay would otherwise hold forever while throughput is already 0.
    """
    origin, extra, field = _latency_source(app_type)
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        f"  |> filter(fn: (r) => {field})\n"
        f'  |> filter(fn: (r) => r.origin == "{origin}")\n'
        f"{extra}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if exists r.app_name and r.app_name != "" then r.app_name else r.ue_id }))\n'
        '  |> group(columns: ["_time", "_field"])\n'
        "  |> mean()\n"
        '  |> group(columns: ["_time"])\n'
        "  |> mean()\n"
        "  |> filter(fn: (r) => exists r._value)\n"
        "  |> group()\n"
        '  |> set(key: "_field", value: "Average")\n'
        '  |> set(key: "_measurement", value: "application_metrics")\n'
        "  |> yield()"
    )


def flux_agg_throughput(app_type: str, direction: str | None = None) -> str:
    """Sum of the same per-UE oaitun series shown on the left.

    Do not fill(usePrevious): that holds a stopped UE's last rate until the
    end of the Grafana window, so aggregated stays green while per-UE is empty.
    """
    if direction == "ul":
        field = 'r._field == "throughput_ul_mbps"'
        label = "UL"
        group_cols = '["_time"]'
        set_field = f'  |> set(key: "_field", value: "{label}")\n'
    elif direction == "dl":
        field = 'r._field == "throughput_dl_mbps"'
        label = "DL"
        group_cols = '["_time"]'
        set_field = f'  |> set(key: "_field", value: "{label}")\n'
    else:
        field = 'r._field == "throughput_ul_mbps" or r._field == "throughput_dl_mbps"'
        group_cols = '["_time", "_field"]'
        set_field = (
            '  |> map(fn: (r) => ({ r with _field: if r._field == "throughput_ul_mbps" then "UL" else "DL" }))\n'
        )
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        f'  |> filter(fn: (r) => r._measurement == "application_metrics" and r.app_type == "{app_type}")\n'
        f"  |> filter(fn: (r) => {field})\n"
        '  |> filter(fn: (r) => r.origin == "client")\n'
        '  |> filter(fn: (r) => exists r.ue_id and r.ue_id =~ /client-/)\n'
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        f"  |> group(columns: {group_cols})\n"
        "  |> sum()\n"
        "  |> filter(fn: (r) => exists r._value)\n"
        "  |> group()\n"
        f"{set_field}"
        '  |> set(key: "_measurement", value: "application_metrics")\n'
        "  |> yield()"
    )


def timeseries(
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    unit: str = "short",
    interval: str | None = None,
    span_nulls: bool = True,
    show_points: str = "auto",
    legend_calcs: list[str] | None = None,
) -> dict:
    panel = {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 12,
                    "lineInterpolation": "smooth",
                    "lineWidth": 2,
                    "showPoints": show_points,
                    "spanNulls": span_nulls,
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
                "calcs": legend_calcs or ["lastNotNull", "mean", "max"],
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
    if interval:
        panel["interval"] = interval
    return panel


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
        "time": {"from": "now-5m", "to": "now"},
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
    """DL row, UL row, then latency (per-UE | average of those UEs)."""
    return [
        timeseries(1, "DL per UE (oaitun RX)", 0, 0, 12, 8, flux_per_ue_throughput(app_type, "dl"), "Mbps", interval="1s", span_nulls=False),
        timeseries(2, "DL aggregated (oaitun RX)", 12, 0, 12, 8, flux_agg_throughput(app_type, "dl"), "Mbps", interval="1s", span_nulls=False),
        timeseries(3, "UL per UE (oaitun TX)", 0, 8, 12, 8, flux_per_ue_throughput(app_type, "ul"), "Mbps", interval="1s", span_nulls=False),
        timeseries(4, "UL aggregated (oaitun TX)", 12, 8, 12, 8, flux_agg_throughput(app_type, "ul"), "Mbps", interval="1s", span_nulls=False),
        timeseries(5, "Per-UE latency", 0, 16, 12, 8, flux_per_ue_latency(app_type), "ms", interval="1s", span_nulls=False, show_points="always", legend_calcs=["last", "mean", "max"]),
        timeseries(6, "Average latency", 12, 16, 12, 8, flux_avg_latency(app_type), "ms", interval="1s", span_nulls=False, show_points="always", legend_calcs=["last", "mean", "max"]),
    ]


def overview_panels(app_type: str, extra_fields: list[tuple[str, str, str]]) -> list[dict]:
    panels = [
        stat(1, "UL+DL (Mbps)", 0, 0, flux(app_type, 'r._field == "throughput_ul_mbps" or r._field == "throughput_dl_mbps"', extra='r.origin == "client"'), "Mbps"),
    ]
    for i, (title, pred, unit) in enumerate(extra_fields, start=2):
        panels.append(stat(i, title, (i - 1) * 6, 0, flux(app_type, pred), unit))
    panels.extend(
        [
            timeseries(
                10,
                "Per-UE 5G UL / DL (oaitun)",
                0,
                4,
                12,
                8,
                flux_per_ue_throughput(app_type),
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
