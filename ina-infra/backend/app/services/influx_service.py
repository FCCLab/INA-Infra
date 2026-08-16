"""InfluxDB 2.x telemetry service for application workloads."""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://10.1.137.104:8086").rstrip("/")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "ina-infra-influxdb-token")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "ina-infra")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "default")
INFLUX_MEASUREMENT = os.environ.get("INFLUXDB_MEASUREMENT", "application_metrics")


def _escape_tag(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def check_health() -> Dict[str, Any]:
    """Check connectivity and health of InfluxDB."""
    url = f"{INFLUX_URL}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            status = resp.status
            return {
                "ok": status == 200,
                "url": INFLUX_URL,
                "org": INFLUX_ORG,
                "bucket": INFLUX_BUCKET,
                "status_code": status,
                "error": None,
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": INFLUX_URL,
            "org": INFLUX_ORG,
            "bucket": INFLUX_BUCKET,
            "status_code": None,
            "error": str(exc),
        }


def write_point(
    fields: Mapping[str, float],
    tags: Optional[Mapping[str, str]] = None,
    measurement: Optional[str] = None,
    ts_ns: Optional[int] = None,
) -> bool:
    """Write line protocol point into InfluxDB."""
    if not fields:
        return False
    meas = measurement or INFLUX_MEASUREMENT
    tag_str = ""
    if tags:
        parts = [f"{_escape_tag(k)}={_escape_tag(v)}" for k, v in tags.items()]
        tag_str = "," + ",".join(parts)
    field_str = ",".join(f"{k}={float(v)}" for k, v in fields.items())
    ts = ts_ns if ts_ns is not None else time.time_ns()
    line = f"{meas}{tag_str} {field_str} {ts}\n"

    write_url = (
        f"{INFLUX_URL}/api/v2/write?org={urllib.parse.quote(INFLUX_ORG)}"
        f"&bucket={urllib.parse.quote(INFLUX_BUCKET)}&precision=ns"
    )
    req = urllib.request.Request(
        write_url,
        data=line.encode("utf-8"),
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 204)
    except Exception as exc:
        logger.warning("Failed to write point to InfluxDB: %s", exc)
        return False


def query_application_metrics(
    profile_name: str,
    slice_id: Optional[int] = None,
    range_s: int = 300,
) -> List[Dict[str, Any]]:
    """Query recent application metrics from InfluxDB using Flux."""
    slice_filter = f' and r["slice"] == "{slice_id}"' if slice_id is not None else ""
    flux = f"""
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{range_s}s)
      |> filter(fn: (r) => r["_measurement"] == "{INFLUX_MEASUREMENT}")
      |> filter(fn: (r) => r["profile"] == "{profile_name}"{slice_filter})
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: false)
    """

    query_url = f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}"
    req = urllib.request.Request(
        query_url,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )

    records: List[Dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            csv_text = resp.read().decode("utf-8", errors="replace")
            lines = csv_text.strip().splitlines()
            if len(lines) < 2:
                return []
            header = None
            for line in lines:
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if header is None:
                    header = parts
                    continue
                row_dict: Dict[str, Any] = {}
                for idx, col in enumerate(header):
                    if idx < len(parts) and col:
                        val = parts[idx]
                        try:
                            val = float(val)
                        except (ValueError, TypeError):
                            pass
                        row_dict[col] = val
                if row_dict:
                    records.append(row_dict)
    except Exception as exc:
        logger.warning("Failed to query InfluxDB: %s", exc)

    return records
