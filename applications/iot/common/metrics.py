"""Shared observability + MQTT payload helpers for Slice D.

Slice D is the best-effort background load generator (no SLO). Both the client
(``iot_client.py``) and the edge controller (``controller.py``) import this
module so the delay histogram buckets, Prometheus metric names, JSON payload
layout, and the chrony clock-offset reader stay identical on both ends.

Naming convention (mirror image across the two processes):
  * UL = uplink   = client -> edge      (topic ``slice_d/ul/<dev>``)
  * DL = downlink = edge   -> client    (topic ``slice_d/dl/<dev>``)

The client observes DL delay and sends UL bytes; the edge observes UL delay and
sends DL bytes. ``build_side_metrics()`` wires the right subset for each side so
a process only exports the series it actually populates.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Delay histogram buckets (seconds). Best-effort semantics -> a wide range from
# a few ms up to 10 s, unlike the tight ~20 ms SLO buckets of slices A/B.
DELAY_BUCKETS = (
    0.005,
    0.010,
    0.025,
    0.050,
    0.100,
    0.250,
    0.500,
    1.0,
    2.5,
    5.0,
    10.0,
)

# Uplink report tiers (client) and downlink control tiers (edge).
UL_TIERS = ("fast", "med", "slow")
DL_TIERS = ("fast", "slow")


# --------------------------------------------------------------------------- #
# Payload build / parse                                                       #
# --------------------------------------------------------------------------- #
def build_payload(
    device_id: str,
    seq: int,
    tier: str,
    size_bytes: int,
    sensor: Optional[dict] = None,
) -> bytes:
    """Build a JSON payload padded to exactly ``size_bytes`` (when it fits).

    ``t_send`` is stamped just before serialization so it reflects the wall
    clock as close to publish as possible. ``pad`` is sized last to hit the
    requested byte count; if the fixed fields already exceed ``size_bytes`` the
    payload is emitted unpadded (never truncated -- correctness over size).
    """
    payload: dict = {
        "device_id": device_id,
        "seq": seq,
        "t_send": 0.0,
        "tier": tier,
    }
    if sensor is not None:
        payload["sensor"] = sensor
    payload["pad"] = ""

    payload["t_send"] = time.time()
    without_pad = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pad_len = max(0, size_bytes - len(without_pad))
    payload["pad"] = "x" * pad_len
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def parse_payload(raw: bytes) -> Optional[dict]:
    """Decode a JSON payload; return None on malformed input (never raises)."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def compute_delay(msg: dict, now: Optional[float] = None) -> tuple[float, bool]:
    """One-way delay ``now - t_send`` in seconds.

    Returns ``(delay, skew)``. A negative raw delay (receiver clock behind
    sender) is clamped to 0 and flagged via ``skew=True`` so the caller can bump
    the clock-skew counter. Valid because both ends are chrony-synced over ens0.
    """
    if now is None:
        now = time.time()
    try:
        t_send = float(msg.get("t_send"))
    except (TypeError, ValueError):
        return 0.0, False
    delay = now - t_send
    if delay < 0:
        return 0.0, True
    return delay, False


# --------------------------------------------------------------------------- #
# Metric bundles                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class SideMetrics:
    """The Prometheus series a single Slice D process exports."""

    delay: Histogram
    bytes_sent: Counter
    bytes_received: Counter
    msgs_sent: Counter
    msgs_received: Counter
    connected: Gauge
    reconnects: Counter
    publish_errors: Counter
    clock_skew: Counter
    clock_offset: Gauge
    devices_active: Gauge


def build_side_metrics(side: str) -> SideMetrics:
    """Instantiate the metric objects for ``side`` in {"client", "edge"}.

    Metric names are shared across sides where the semantics line up (e.g. the
    client's ``sliced_ul_bytes_sent_total`` pairs with the edge's
    ``sliced_ul_bytes_received_total``) so PromQL/dashboards read symmetrically.
    """
    if side == "client":
        delay = Histogram(
            "sliced_dl_delay_seconds",
            "Downlink one-way delay (edge -> client)",
            ["tier"],
            buckets=DELAY_BUCKETS,
        )
        bytes_sent = Counter(
            "sliced_ul_bytes_sent",
            "Application-layer uplink payload bytes published",
            ["tier"],
        )
        bytes_received = Counter(
            "sliced_dl_bytes_received",
            "Application-layer downlink payload bytes received",
            ["tier"],
        )
        msgs_sent = Counter(
            "sliced_ul_messages_sent", "Uplink messages published", ["tier"]
        )
        msgs_received = Counter(
            "sliced_dl_messages_received", "Downlink messages received", ["tier"]
        )
    elif side == "edge":
        delay = Histogram(
            "sliced_ul_delay_seconds",
            "Uplink one-way delay (client -> edge)",
            ["tier"],
            buckets=DELAY_BUCKETS,
        )
        bytes_sent = Counter(
            "sliced_dl_bytes_sent",
            "Application-layer downlink payload bytes published",
            ["tier"],
        )
        bytes_received = Counter(
            "sliced_ul_bytes_received",
            "Application-layer uplink payload bytes received",
            ["tier"],
        )
        msgs_sent = Counter(
            "sliced_dl_messages_sent", "Downlink messages published", ["tier"]
        )
        msgs_received = Counter(
            "sliced_ul_messages_received", "Uplink messages received", ["tier"]
        )
    else:
        raise ValueError(f"unknown side {side!r} (expected 'client' or 'edge')")

    return SideMetrics(
        delay=delay,
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        msgs_sent=msgs_sent,
        msgs_received=msgs_received,
        connected=Gauge("sliced_mqtt_connected", "MQTT broker connection state (1/0)"),
        reconnects=Counter(
            "sliced_mqtt_reconnects", "MQTT reconnects since process start"
        ),
        publish_errors=Counter("sliced_publish_errors", "Failed MQTT publish calls"),
        clock_skew=Counter(
            "sliced_clock_skew_events",
            "Messages whose one-way delay was negative (clock skew)",
        ),
        clock_offset=Gauge(
            "sliced_clock_offset_seconds", "Absolute chrony clock offset (seconds)"
        ),
        devices_active=Gauge(
            "sliced_devices_active", "Distinct devices seen within the TTL window"
        ),
    )


# --------------------------------------------------------------------------- #
# Metrics server + chrony                                                     #
# --------------------------------------------------------------------------- #
def start_metrics_server(port: int, addr: str = "0.0.0.0") -> None:
    """Expose the default Prometheus registry over HTTP (bind to ens0 in prod)."""
    start_http_server(port, addr=addr)


def get_chrony_offset_seconds(host: Optional[str] = None) -> Optional[float]:
    """Return chrony's 'Last offset' in seconds, or None if unavailable.

    Never raises: a missing chronyd/chronyc or a parse failure returns None so
    callers skip updating the gauge instead of crashing.
    """
    cmd = ["chronyc"]
    if host:
        cmd += ["-h", host]
    cmd += ["tracking"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.strip().startswith("Last offset"):
            try:
                return float(line.split(":", 1)[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def start_chrony_offset_updater(
    gauge: Gauge,
    interval_s: float = 10.0,
    host: Optional[str] = None,
) -> threading.Thread:
    """Daemon thread that refreshes ``gauge`` with the absolute clock offset."""

    def _run() -> None:
        while True:
            offset = get_chrony_offset_seconds(host)
            if offset is not None:
                gauge.set(abs(offset))
            time.sleep(interval_s)

    thread = threading.Thread(target=_run, name="chrony-offset", daemon=True)
    thread.start()
    return thread
