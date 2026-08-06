#!/usr/bin/env python3
"""Minimal InfluxDB 2.x line-protocol writer (stdlib only)."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Dict, Mapping, Optional


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class InfluxWriter:
    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        org: Optional[str] = None,
        bucket: Optional[str] = None,
        measurement: str = "iperf3",
    ) -> None:
        # Prefer in-cluster Service (flannel eth0). Multus site IP .104 is not
        # reliably reachable from UPF N6 / UE pods on TCP :8086.
        self.url = (
            url or env("INFLUX_URL", "http://influxdb.influxdb.svc.cluster.local:8086")
        ).rstrip("/")
        self.token = token or env("INFLUX_TOKEN", "ina-infra-influxdb-token")
        self.org = org or env("INFLUX_ORG", "ina-infra")
        self.bucket = bucket or env("INFLUX_BUCKET", "default")
        self.measurement = measurement or env("INFLUX_MEASUREMENT", "iperf3")
        self._write_url = (
            f"{self.url}/api/v2/write?org={self.org}&bucket={self.bucket}&precision=ns"
        )

    @staticmethod
    def _escape_tag(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace(" ", "\\ ")
            .replace(",", "\\,")
            .replace("=", "\\=")
        )

    def write(
        self,
        fields: Mapping[str, float],
        tags: Optional[Mapping[str, str]] = None,
        ts_ns: Optional[int] = None,
    ) -> None:
        if not fields:
            return
        tag_str = ""
        if tags:
            parts = [f"{self._escape_tag(k)}={self._escape_tag(v)}" for k, v in tags.items()]
            tag_str = "," + ",".join(parts)
        field_str = ",".join(f"{k}={float(v)}" for k, v in fields.items())
        ts = ts_ns if ts_ns is not None else time.time_ns()
        line = f"{self.measurement}{tag_str} {field_str} {ts}\n"
        req = urllib.request.Request(
            self._write_url,
            data=line.encode("utf-8"),
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Influx write HTTP {exc.code}: {body}") from exc
        except Exception as exc:  # noqa: BLE001 — log path for sidecar
            raise RuntimeError(f"Influx write failed: {exc}") from exc


def mbps_from_bits(bps: float) -> float:
    return float(bps) / 1_000_000.0
