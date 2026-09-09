#!/usr/bin/env python3
"""Write the Exp4 all-applications Grafana dashboard.

Influx measurement ``application_metrics`` (profile_name=exp4).

Server (origin=server): cpu_m, mem_mb, gpu_pct, vram_mb,
  tcp_sendq_bytes / tcp_notsent_bytes / tcp_retrans / tcp_cwnd / tcp_rwnd_bytes
  (TO_CLIENT_IFACE only)
Client (origin=client): throughput_dl_mbps, latency_ms, tx_latency_ms, e2e_latency_ms,
  tcp_recvq_bytes / tcp_rwnd_bytes (TO_SERVER_IFACE only)

``app_type`` is exp4-s1 … exp4-s5. ``scheme`` is exp4-s0 … exp4-s3 or exp4-no5g.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DS = {"type": "influxdb", "uid": "dfufcr74lgwzkf"}
HERE = Path(__file__).resolve().parent
GRAFANA_USER = os.environ.get("GRAFANA_USER", "inainfra")
GRAFANA_PASS = os.environ.get("GRAFANA_PASS", "inainfra")
# Multus VIP is the public URL; NodePort works when .105 is not routed here.
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://10.1.137.105:3000")
GRAFANA_PUSH_URLS = (
    os.environ.get("GRAFANA_URL", ""),
    "http://10.1.137.105:3000",
    "http://10.1.137.130:30300",
)

APPS = (
    # app_type, legend, color, t_bar Mbps, d_bar ms, console
    ("exp4-s1", "S1 FTP", "#73BF69", 20.0, 250.0, "http://10.1.137.211/"),
    ("exp4-s2", "S2 CCTV/YOLO", "#F2CC0C", 12.0, 45.0, "http://10.1.137.212/"),
    ("exp4-s3", "S3 OTT", "#5794F2", 22.0, 58.0, "http://10.1.137.213/"),
    ("exp4-s4", "S4 CPU offload", "#B877D9", 8.0, 400.0, "http://10.1.137.214/"),
    ("exp4-s5", "S5 MQTT", "#FF9830", 2.0, 80.0, "http://10.1.137.215/"),
)

SCHEMES = (
    ("S0 static", "exp4-s0"),
    ("S1 +PL", "exp4-s1"),
    ("S2 +PL+PM", "exp4-s2"),
    ("S3 +PL+PM+PS", "exp4-s3"),
    ("no-5G", "exp4-no5g"),
)


def _scheme_filter() -> str:
    # allValue is .* so do not use ${scheme:regex} (that would escape the dots).
    return "  |> filter(fn: (r) => r.scheme =~ /^${scheme}$/)\n"


def _rename_app() -> str:
    chain = " else ".join(
        f'if r.app_type == "{app_type}" then "{label}"' for app_type, label, *_ in APPS
    )
    return f"{chain} else r.app_type"


def flux_all(origin: str, field: str) -> str:
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "application_metrics" '
        'and r.profile_name == "exp4")\n'
        f'  |> filter(fn: (r) => r.origin == "{origin}")\n'
        f'  |> filter(fn: (r) => r._field == "{field}")\n'
        f"{_scheme_filter()}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        f'  |> map(fn: (r) => ({{ r with _field: {_rename_app()} }}))\n'
        "  |> yield()"
    )


def flux_one(app_type: str, origin: str, field: str) -> str:
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "application_metrics" '
        f'and r.profile_name == "exp4" and r.app_type == "{app_type}" '
        f'and r.origin == "{origin}")\n'
        f'  |> filter(fn: (r) => r._field == "{field}")\n'
        f"{_scheme_filter()}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        "  |> yield()"
    )


def flux_tcp_dl(app_type: str) -> str:
    """Server DL TCP Send-Q + notsent + outstanding retrans on TO_CLIENT_IFACE."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "application_metrics" '
        f'and r.profile_name == "exp4" and r.app_type == "{app_type}" '
        'and r.origin == "server")\n'
        '  |> filter(fn: (r) => r._field == "tcp_sendq_bytes" or '
        'r._field == "tcp_notsent_bytes" or r._field == "tcp_retrans")\n'
        f"{_scheme_filter()}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_sendq_bytes" '
        'then "Send-Q" else if r._field == "tcp_notsent_bytes" then "notsent" '
        'else "retrans" }))\n'
        "  |> yield()"
    )


def flux_tcp_recv(app_type: str) -> str:
    """Client DL TCP Recv-Q + rwnd on TO_SERVER_IFACE (oaitun / net1)."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "application_metrics" '
        f'and r.profile_name == "exp4" and r.app_type == "{app_type}" '
        'and r.origin == "client")\n'
        '  |> filter(fn: (r) => r._field == "tcp_recvq_bytes" or '
        'r._field == "tcp_rwnd_bytes")\n'
        f"{_scheme_filter()}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_recvq_bytes" '
        'then "Recv-Q" else "rwnd" }))\n'
        "  |> yield()"
    )


def flux_tcp_wnd(app_type: str) -> str:
    """Server DL TCP cwnd + peer rwnd on TO_CLIENT_IFACE."""
    return (
        'from(bucket: "default")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "application_metrics" '
        f'and r.profile_name == "exp4" and r.app_type == "{app_type}" '
        'and r.origin == "server")\n'
        '  |> filter(fn: (r) => r._field == "tcp_cwnd" or '
        'r._field == "tcp_rwnd_bytes")\n'
        f"{_scheme_filter()}"
        "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n"
        '  |> map(fn: (r) => ({ r with _field: if r._field == "tcp_cwnd" '
        'then "cwnd" else "rwnd" }))\n'
        "  |> yield()"
    )


def tcp_overrides(color: str) -> list[dict]:
    return [
        {
            "matcher": {"id": "byName", "options": "Send-Q"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
            ],
        },
        {
            "matcher": {"id": "byName", "options": "notsent"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 4]}},
            ],
        },
        {
            "matcher": {"id": "byName", "options": "retrans"},
            "properties": [
                {"id": "color", "value": {"fixedColor": "#F2495C", "mode": "fixed"}},
                {"id": "custom.lineStyle", "value": {"fill": "dot", "dash": [2, 4]}},
                {"id": "unit", "value": "short"},
                {"id": "custom.axisPlacement", "value": "right"},
            ],
        },
    ]


def tcp_wnd_overrides(color: str) -> list[dict]:
    return [
        {
            "matcher": {"id": "byName", "options": "cwnd"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
                {"id": "unit", "value": "short"},
                {"id": "custom.axisPlacement", "value": "left"},
            ],
        },
        {
            "matcher": {"id": "byName", "options": "rwnd"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 4]}},
                {"id": "unit", "value": "decbytes"},
                {"id": "custom.axisPlacement", "value": "right"},
            ],
        },
    ]


def tcp_recv_overrides(color: str) -> list[dict]:
    return [
        {
            "matcher": {"id": "byName", "options": "Recv-Q"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
            ],
        },
        {
            "matcher": {"id": "byName", "options": "rwnd"},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 4]}},
            ],
        },
    ]


def color_overrides() -> list[dict]:
    return [
        {
            "matcher": {"id": "byName", "options": label},
            "properties": [
                {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
            ],
        }
        for _, label, color, *_ in APPS
    ]


def timeseries(
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    unit: str,
    query: str,
    *,
    h: int = 8,
    overrides: list[dict] | None = None,
) -> dict:
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
            "overrides": overrides or [],
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
        "interval": "1s",
    }


def row(pid: int, title: str, y: int, panels: list[dict], *, collapsed: bool = True) -> dict:
    return {
        "collapsed": collapsed,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "id": pid,
        "panels": panels if collapsed else [],
        "title": title,
        "type": "row",
    }


def scheme_variable() -> dict:
    options = [{"selected": True, "text": "All", "value": ".*"}]
    options.extend(
        {"selected": False, "text": text, "value": value} for text, value in SCHEMES
    )
    return {
        "allValue": ".*",
        "current": {"selected": True, "text": "All", "value": ".*"},
        "hide": 0,
        "includeAll": True,
        "label": "Scheme",
        "multi": False,
        "name": "scheme",
        "options": options,
        "query": ",".join(value for _, value in SCHEMES),
        "skipUrlSync": False,
        "type": "custom",
    }


def slice_detail_panels(base_id: int, app_type: str, y: int) -> list[dict]:
    return [
        timeseries(base_id + 1, "Server CPU (millicores)", 0, y, 6, "suffix:m", flux_one(app_type, "server", "cpu_m")),
        timeseries(base_id + 2, "Server RAM (MB)", 6, y, 6, "decmbytes", flux_one(app_type, "server", "mem_mb")),
        timeseries(base_id + 3, "Server GPU (%)", 12, y, 6, "percent", flux_one(app_type, "server", "gpu_pct")),
        timeseries(base_id + 4, "Server VRAM (MB)", 18, y, 6, "decmbytes", flux_one(app_type, "server", "vram_mb")),
        timeseries(
            base_id + 5,
            "Client DL throughput",
            0,
            y + 8,
            6,
            "Mbps",
            flux_one(app_type, "client", "throughput_dl_mbps"),
        ),
        timeseries(
            base_id + 6,
            "Client application latency",
            6,
            y + 8,
            6,
            "ms",
            flux_one(app_type, "client", "latency_ms"),
        ),
        timeseries(
            base_id + 7,
            "Client transmission latency",
            12,
            y + 8,
            6,
            "ms",
            flux_one(app_type, "client", "tx_latency_ms"),
        ),
        timeseries(
            base_id + 8,
            "Client E2E latency (app + tx)",
            18,
            y + 8,
            6,
            "ms",
            flux_one(app_type, "client", "e2e_latency_ms"),
        ),
    ]


def dashboard() -> dict:
    colors = color_overrides()
    # Same 4+4 layout as the per-app boards; each panel overlays all slices.
    panels: list[dict] = [
        timeseries(1, "Server CPU (millicores)", 0, 0, 6, "suffix:m", flux_all("server", "cpu_m"), overrides=colors),
        timeseries(2, "Server RAM (MB)", 6, 0, 6, "decmbytes", flux_all("server", "mem_mb"), overrides=colors),
        timeseries(3, "Server GPU (%)", 12, 0, 6, "percent", flux_all("server", "gpu_pct"), overrides=colors),
        timeseries(4, "Server VRAM (MB)", 18, 0, 6, "decmbytes", flux_all("server", "vram_mb"), overrides=colors),
        timeseries(
            5,
            "Client DL throughput",
            0,
            8,
            6,
            "Mbps",
            flux_all("client", "throughput_dl_mbps"),
            overrides=colors,
        ),
        timeseries(
            6,
            "Client application latency",
            6,
            8,
            6,
            "ms",
            flux_all("client", "latency_ms"),
            overrides=colors,
        ),
        timeseries(
            7,
            "Client transmission latency",
            12,
            8,
            6,
            "ms",
            flux_all("client", "tx_latency_ms"),
            overrides=colors,
        ),
        timeseries(
            8,
            "Client E2E latency (app + tx)",
            18,
            8,
            6,
            "ms",
            flux_all("client", "e2e_latency_ms"),
            overrides=colors,
        ),
    ]

    panels.append(row(9, "Server DL TCP Send-Q / notsent / retrans (TO_CLIENT_IFACE)", 16, [], collapsed=False))
    for i, (app_type, label, color, *_rest) in enumerate(APPS):
        panels.append(
            timeseries(
                10 + i,
                label,
                i * 5,
                17,
                4,
                "decbytes",
                flux_tcp_dl(app_type),
                h=8,
                overrides=tcp_overrides(color),
            )
        )

    panels.append(row(30, "Server DL TCP cwnd / rwnd (TO_CLIENT_IFACE)", 25, [], collapsed=False))
    for i, (app_type, label, color, *_rest) in enumerate(APPS):
        panels.append(
            timeseries(
                31 + i,
                label,
                i * 5,
                26,
                4,
                "short",
                flux_tcp_wnd(app_type),
                h=8,
                overrides=tcp_wnd_overrides(color),
            )
        )

    panels.append(row(20, "Client DL TCP Recv-Q / rwnd (TO_SERVER_IFACE)", 34, [], collapsed=False))
    for i, (app_type, label, color, *_rest) in enumerate(APPS):
        panels.append(
            timeseries(
                21 + i,
                label,
                i * 5,
                35,
                4,
                "decbytes",
                flux_tcp_recv(app_type),
                h=8,
                overrides=tcp_recv_overrides(color),
            )
        )

    row_y = 43
    for i, (app_type, label, _color, t_bar, d_bar, url) in enumerate(APPS):
        inner_y = row_y + 1
        panels.append(
            row(
                100 + i,
                f"{label}  ·  {t_bar:g} Mbps / {d_bar:g} ms  ·  {url}",
                row_y,
                slice_detail_panels(200 + i * 10, app_type, inner_y),
                collapsed=True,
            )
        )
        row_y += 1

    return {
        "dashboard": {
            "editable": True,
            "fiscalYearStartMonth": 0,
            "graphTooltip": 1,
            "links": [
                {
                    "asDropdown": False,
                    "icon": "dashboard",
                    "includeVars": True,
                    "keepTime": True,
                    "tags": ["exp4"],
                    "title": "Exp4 dashboards",
                    "type": "dashboards",
                },
                *[
                    {
                        "icon": "external link",
                        "targetBlank": True,
                        "title": label,
                        "tooltip": url,
                        "type": "link",
                        "url": url,
                    }
                    for _app, label, _c, _t, _d, url in APPS
                ],
            ],
            "panels": panels,
            "refresh": "5s",
            "schemaVersion": 40,
            "tags": ["exp4", "applications", "all-slices"],
            "templating": {"list": [scheme_variable()]},
            "time": {"from": "now-15m", "to": "now"},
            "timepicker": {"refresh_intervals": ["1s", "5s", "10s", "30s"]},
            "timezone": "browser",
            "title": "Exp4 All Applications",
            "uid": "ina-exp4-apps",
            "version": 1,
        },
        "overwrite": True,
        "message": "Exp4: Send-Q/notsent/retrans, cwnd/rwnd, Recv-Q/rwnd",
    }


def write_json() -> Path:
    path = HERE / "grafana-dashboard.json"
    path.write_text(json.dumps(dashboard(), indent=2) + "\n", encoding="utf-8")
    (HERE / "README.md").write_text(
        "# Exp4 All Applications Grafana\n\n"
        "One board for all five DL slices. Influx `application_metrics`, "
        "`profile_name=exp4`.\n\n"
        "| Slice | `app_type` | Server | Client |\n"
        "| :---: | :--- | :--- | :--- |\n"
        "| 1 | `exp4-s1` | CPU / RAM / GPU / VRAM; DL Send-Q / notsent on `TO_CLIENT_IFACE` | DL throughput, latencies, Recv-Q / rwnd on `TO_SERVER_IFACE` |\n"
        "| 2 | `exp4-s2` | same | same |\n"
        "| 3 | `exp4-s3` | same | same |\n"
        "| 4 | `exp4-s4` | same | same |\n"
        "| 5 | `exp4-s5` | same | same |\n\n"
        "**Scheme** dropdown filters `scheme` (`exp4-s0` … `exp4-s3`, `exp4-no5g`). "
        "One scheme is live at a time; All overlays history.\n\n"
        "Top group: same 4+4 layout as the per-app boards, all five slices "
        "overlaid on each graph. Next rows: one pane per application for server "
        "DL TCP **Send-Q** / **notsent** on `TO_CLIENT_IFACE` (`net1`), then client "
        "DL TCP **Recv-Q** / **rwnd** on `TO_SERVER_IFACE` (`oaitun*`). "
        "Expand a slice row for that app only. "
        "Application latency is app work (`t_send` → client). Transmission latency is "
        "ICMP RTT on `TO_SERVER_IFACE` (TCP connect fallback). E2E is application + "
        "transmission.\n\n"
        "Regenerate:\n\n"
        "```bash\n"
        "python3 paper/exp4/dashboard/generate.py\n"
        "python3 paper/exp4/dashboard/generate.py --push\n"
        "```\n\n"
        f"Grafana: `{GRAFANA_URL}` (`{GRAFANA_USER}` / `{GRAFANA_PASS}`). "
        "UID `ina-exp4-apps`.\n",
        encoding="utf-8",
    )
    print(f"wrote {path}")
    return path


def _grafana_urls() -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for raw in GRAFANA_PUSH_URLS:
        url = (raw or "").rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def push(path: Path) -> None:
    import base64

    data = json.loads(path.read_text(encoding="utf-8"))
    dash = data.get("dashboard", data)
    payload = json.dumps(
        {"dashboard": dash, "overwrite": True, "message": data.get("message", "Exp4 all apps")}
    ).encode()
    auth = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASS}".encode()).decode("ascii")
    errors: list[str] = []
    for base in _grafana_urls():
        req = urllib.request.Request(
            f"{base}/api/dashboards/db",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{base}: {exc}")
            continue
        print(f"pushed uid={res.get('uid')} {base}{res.get('url')}")
        return
    raise SystemExit(
        "Grafana push failed:\n  " + "\n  ".join(errors) + "\n"
        "JSON is written; import grafana-dashboard.json or retry --push."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Exp4 all-apps Grafana dashboard")
    parser.add_argument("--push", action="store_true", help=f"POST to {GRAFANA_URL}")
    args = parser.parse_args()
    path = write_json()
    if args.push:
        push(path)


if __name__ == "__main__":
    main()
