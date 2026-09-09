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
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server

APP_UE_LATENCY_MS = Gauge(
    "app_ue_latency_ms",
    "Per-UE application latency (milliseconds)",
    ["ue_id"],
)
APP_UE_THROUGHPUT_MBPS = Gauge(
    "app_ue_throughput_mbps",
    "Per-UE application throughput (Mbps)",
    ["ue_id"],
)
APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
APP_THROUGHPUT_MBPS = Gauge(
    "app_throughput_mbps", "Aggregated application throughput (Mbps)"
)


def set_ue_latency(ue_id: str, latency_ms: float) -> None:
    APP_UE_LATENCY_MS.labels(ue_id=str(ue_id or "ue")).set(float(latency_ms or 0.0))


def clear_ue_latency(ue_id: str) -> None:
    try:
        APP_UE_LATENCY_MS.remove(str(ue_id or "ue"))
    except KeyError:
        pass


def set_agg_latency(latency_ms: float) -> None:
    APP_LATENCY_MS.set(float(latency_ms or 0.0))


def clear_agg_latency() -> None:
    APP_LATENCY_MS.set(float("nan"))


def set_agg_latency(latency_ms: float) -> None:
    APP_LATENCY_MS.set(float(latency_ms or 0.0))


def set_ue_slo(ue_id: str, latency_ms: float, throughput_mbps: float) -> None:
    uid = str(ue_id or "ue")
    set_ue_latency(uid, latency_ms)
    APP_UE_THROUGHPUT_MBPS.labels(ue_id=uid).set(float(throughput_mbps or 0.0))


def set_agg_slo(latency_ms: float, throughput_mbps: float) -> None:
    set_agg_latency(latency_ms)
    APP_THROUGHPUT_MBPS.set(float(throughput_mbps or 0.0))

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

# Fixed-width fields so the hot path only overwrites t_send (and seq) in place.
# Space-pad (not zero-pad): JSON forbids leading zeros in numbers, but leading
# whitespace before a number is valid and keeps the byte offsets stable.
_SEQ_WIDTH = 10
_TS_WIDTH = 17  # e.g. 1757347200.123456 (10+1+6) for current unix time


@dataclass
class PayloadTemplate:
    """Prebuilt MQTT body; stamp() only rewrites fixed-width seq + t_send."""

    buf: bytearray
    seq_off: int
    ts_off: int
    nbytes: int
    device_id: str
    tier: str
    size_bytes: int

    def stamp(self, seq: int, t_send: Optional[float] = None) -> bytes:
        if t_send is None:
            t_send = time.time()
        seq_s = format(seq % (10 ** _SEQ_WIDTH), f"{_SEQ_WIDTH}d").encode("ascii")
        ts_s = format(t_send, f"{_TS_WIDTH}.6f").encode("ascii")
        if len(ts_s) != _TS_WIDTH:
            # Extreme clock values — fall back to regex stamp on a copy.
            raw = bytes(self.buf)
            raw = raw[: self.seq_off] + seq_s + raw[self.seq_off + _SEQ_WIDTH :]
            return stamp_at_publish(raw)[0]
        self.buf[self.seq_off : self.seq_off + _SEQ_WIDTH] = seq_s
        self.buf[self.ts_off : self.ts_off + _TS_WIDTH] = ts_s
        return bytes(self.buf)


def make_payload_template(
    device_id: str,
    tier: str,
    size_bytes: int,
    sensor: Optional[dict] = None,
) -> PayloadTemplate:
    """Build a padded JSON body once; later publishes only rewrite seq + t_send."""
    seq_ph = format(0, f"{_SEQ_WIDTH}d")
    ts_ph = format(0.0, f"{_TS_WIDTH}.6f")
    head = (
        f'{{"device_id":{json.dumps(device_id, separators=(",", ":"))},'
        f'"seq":{seq_ph},'
        f'"t_send":{ts_ph},'
        f'"tier":{json.dumps(tier, separators=(",", ":"))}'
    )
    if sensor is not None:
        head += f',"sensor":{json.dumps(sensor, separators=(",", ":"))}'
    head += ',"pad":"'
    head_b = head.encode("utf-8")
    tail_b = b'"}'
    pad_len = max(0, int(size_bytes) - len(head_b) - len(tail_b))
    raw = head_b + (b"x" * pad_len) + tail_b
    seq_off = raw.find(seq_ph.encode("ascii"))
    ts_off = raw.find(ts_ph.encode("ascii"))
    if seq_off < 0 or ts_off < 0:
        raise RuntimeError("payload template placeholders missing")
    return PayloadTemplate(
        buf=bytearray(raw),
        seq_off=seq_off,
        ts_off=ts_off,
        nbytes=len(raw),
        device_id=device_id,
        tier=tier,
        size_bytes=int(size_bytes),
    )


def build_payload(
    device_id: str,
    seq: int,
    tier: str,
    size_bytes: int,
    sensor: Optional[dict] = None,
) -> bytes:
    """Build a JSON payload padded to exactly ``size_bytes`` (when it fits).

    ``t_send`` is a placeholder; the controller restamps it at ``publish()``.
    Prefer ``make_payload_template`` + ``PayloadTemplate.stamp`` on the hot path.
    """
    tmpl = make_payload_template(device_id, tier, size_bytes, sensor=sensor)
    return tmpl.stamp(seq)


_T_SEND_RE = re.compile(rb'"t_send":\s*[0-9.]+')


def stamp_at_publish(raw: bytes) -> tuple[bytes, float]:
    """Overwrite ``t_send`` with wall clock at MQTT publish (not generate)."""
    t_send = time.time()
    stamped = _T_SEND_RE.sub(f'"t_send":{t_send:.6f}'.encode(), raw, count=1)
    return stamped, t_send


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
