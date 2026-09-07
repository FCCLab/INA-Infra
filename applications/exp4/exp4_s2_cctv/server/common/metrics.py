"""Shared Prometheus and observability helpers for the network-slice pipeline.

This module is intentionally slice-agnostic: it defines the latency bucket
layout, a metrics HTTP server bootstrap, a chrony clock-offset reader, and a
structured JSON logger. Individual slices (A/B/C/D) declare their own metric
objects and reuse the helpers here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from typing import Optional

from prometheus_client import Gauge, start_http_server

# Latency histogram buckets (seconds), tightly packed around the 20 ms UL SLO.
# Kept here so every slice measures against the same edges and dashboards line
# up across services.
LATENCY_BUCKETS = (
    0.005,
    0.010,
    0.015,
    0.0175,
    0.020,
    0.0225,
    0.025,
    0.030,
    0.040,
    0.060,
    0.100,
    0.250,
    1.0,
)

# NTP epoch (1900) to Unix epoch (1970) offset in nanoseconds. Used to normalize
# reference timestamps that arrive in the NTP epoch.
NTP_UNIX_OFFSET_NS = 2208988800 * 1_000_000_000


def start_metrics_server(port: int, addr: str = "0.0.0.0") -> None:
    """Expose the default Prometheus registry over HTTP.

    The bind address defaults to all interfaces; in deployment the container is
    attached to the metrics network (ens0 exposure) so scraping stays off the
    OTA interface.
    """
    start_http_server(port, addr=addr)


def log_json(level: str, event: str, **fields) -> None:
    """Emit a single structured JSON log line to stdout."""
    record = {
        "ts": time.time(),
        "level": level,
        "event": event,
    }
    record.update(fields)
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def get_chrony_offset_seconds(host: Optional[str] = None) -> Optional[float]:
    """Return the current chrony 'Last offset' in seconds, or None if unavailable.

    Never raises: a missing chronyd, missing chronyc binary, or parse failure
    all return None so callers can skip updating the gauge instead of crashing.
    """
    cmd = ["chronyc"]
    if host:
        cmd += ["-h", host]
    cmd += ["tracking"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.strip().startswith("Last offset"):
            try:
                value = line.split(":", 1)[1].strip().split()[0]
                return float(value)
            except (IndexError, ValueError):
                return None
    return None


def start_chrony_offset_updater(
    gauge: Gauge,
    interval_s: float = 10.0,
    host: Optional[str] = None,
) -> threading.Thread:
    """Start a daemon thread that periodically refreshes a clock-offset gauge.

    The gauge is set to the absolute offset in seconds (accuracy of the e2e
    measurement is bounded by |offset|). If chrony cannot be reached the gauge
    is left unchanged.
    """

    def _run() -> None:
        while True:
            offset = get_chrony_offset_seconds(host)
            if offset is not None:
                gauge.set(abs(offset))
            time.sleep(interval_s)

    thread = threading.Thread(target=_run, name="chrony-offset", daemon=True)
    thread.start()
    return thread


def normalize_reference_ns(reference_ns: int) -> int:
    """Coerce a reference timestamp to the Unix epoch (nanoseconds).

    rtspsrc reference-timestamp metadata for ``timestamp/x-ntp`` arrives in the
    NTP epoch (1900). If the value looks like an NTP-epoch timestamp, shift it to
    the Unix epoch so it can be compared against ``time.time_ns()``.
    """
    now_ns = time.time_ns()
    # If the value is well ahead of "now" by roughly the NTP-Unix offset, it is
    # an NTP-epoch timestamp; shift it back to the Unix epoch.
    if reference_ns - now_ns > NTP_UNIX_OFFSET_NS // 2:
        return reference_ns - NTP_UNIX_OFFSET_NS
    return reference_ns
