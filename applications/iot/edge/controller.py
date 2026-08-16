"""Slice D edge controller (paho-mqtt), co-located with a Mosquitto broker.

Runs alongside the broker in one container (see entrypoint.sh). It:
  * connects to the broker over loopback (never the OTA path),
  * subscribes ``slice_d/ul/#`` and computes uplink one-way delay per message,
  * tracks the set of live devices (last-seen within a TTL),
  * fans downlink control messages out to ``slice_d/dl/<dev>`` on two cadences
    (fast/slow), and
  * exports mirror-image Prometheus metrics on ens0 + a periodic summary line.

The broker binds its OTA listener to ``OTA_BIND_IP``; the controller here talks
to the loopback listener (``LOCAL_BROKER_HOST:LOCAL_BROKER_PORT``), so control
traffic to/from real devices rides the OTA path while this internal hop does not.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

from common import metrics


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _fail(f"{name}={raw!r} is not an integer")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _fail(f"{name}={raw!r} is not a number")


def _fail(msg: str) -> "None":
    sys.stderr.write(f"[slice-d edge] config error: {msg}\n")
    sys.exit(2)


LOCAL_BROKER_HOST = _env("LOCAL_BROKER_HOST", "127.0.0.1")
LOCAL_BROKER_PORT = _env_int("LOCAL_BROKER_PORT", 1884)
METRICS_BIND_IP = _env("METRICS_BIND_IP", "0.0.0.0")
METRICS_PORT = _env_int("METRICS_PORT", 9105)

DL_FAST_PERIOD_S = _env_float("DL_FAST_PERIOD_S", 300)
DL_SLOW_PERIOD_S = _env_float("DL_SLOW_PERIOD_S", 3600)
DL_PAYLOAD_BYTES = _env_int("DL_PAYLOAD_BYTES", 256)
# Devices unseen for this long are dropped from the downlink fan-out. Default is
# 2x the client's default slow uplink period (2 * 3600 s).
DEVICE_TTL_S = _env_float("DEVICE_TTL_S", 7200)

MQTT_QOS = _env_int("MQTT_QOS", 0)
LOG_INTERVAL_S = _env_float("LOG_INTERVAL_S", 30)
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("slice-d.edge")


def _validate() -> None:
    if MQTT_QOS not in (0, 1):
        _fail(f"MQTT_QOS must be 0 or 1, got {MQTT_QOS}")
    for name, period in (
        ("DL_FAST_PERIOD_S", DL_FAST_PERIOD_S),
        ("DL_SLOW_PERIOD_S", DL_SLOW_PERIOD_S),
    ):
        if period <= 0:
            _fail(f"{name} must be > 0, got {period}")


M = metrics.build_side_metrics("edge")

UL_TOPIC_PREFIX = "slice_d/ul/"


def _fmt_rate(bytes_per_s: float) -> str:
    return f"{bytes_per_s / 1024:.1f}kB/s"


class Controller:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connect_count = 0
        self._reconnects = 0
        self._connected = False
        self._start_time = time.monotonic()

        # device_id -> last-seen monotonic timestamp
        self._devices: dict[str, float] = {}

        self._ul_delays: list[float] = []
        self._ul_bytes = 0
        self._dl_bytes = 0
        self._last_ul_bytes = 0
        self._last_dl_bytes = 0
        self._last_report = time.monotonic()
        self._dl_seq = 0

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="slice-d-controller",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

    # -- MQTT callbacks -------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            log.warning("connect failed: %s", reason_code)
            return
        with self._lock:
            self._connect_count += 1
            is_reconnect = self._connect_count > 1
            self._connected = True
            if is_reconnect:
                self._reconnects += 1
        if is_reconnect:
            M.reconnects.inc()
        M.connected.set(1)
        client.subscribe("slice_d/ul/#", qos=MQTT_QOS)
        log.info("connected to broker %s:%d (reconnect=%s)", LOCAL_BROKER_HOST, LOCAL_BROKER_PORT, is_reconnect)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties):
        with self._lock:
            self._connected = False
        M.connected.set(0)
        log.warning("disconnected: %s (auto-reconnecting)", reason_code)

    def _on_message(self, _client, _userdata, msg):
        recv = time.time()
        if not msg.topic.startswith(UL_TOPIC_PREFIX):
            return
        parsed = metrics.parse_payload(msg.payload)
        if parsed is None:
            return
        device_id = parsed.get("device_id") or msg.topic[len(UL_TOPIC_PREFIX):]
        tier = parsed.get("tier", "unknown")
        delay, skew = metrics.compute_delay(parsed, now=recv)
        if skew:
            M.clock_skew.inc()
        M.delay.labels(tier=tier).observe(delay)
        nbytes = len(msg.payload)
        M.bytes_received.labels(tier=tier).inc(nbytes)
        M.msgs_received.labels(tier=tier).inc()
        with self._lock:
            self._devices[device_id] = time.monotonic()
            self._ul_bytes += nbytes
            self._ul_delays.append(delay)
        log.debug("ul seq=%s tier=%s dev=%s delay=%.1fms", parsed.get("seq"), tier, device_id, delay * 1000)

    # -- Downlink fan-out -----------------------------------------------------
    def _live_devices(self) -> list[str]:
        cutoff = time.monotonic() - DEVICE_TTL_S
        with self._lock:
            live = [d for d, seen in self._devices.items() if seen >= cutoff]
            # Prune expired entries so the dict does not grow unbounded.
            for d in list(self._devices):
                if self._devices[d] < cutoff:
                    del self._devices[d]
        M.devices_active.set(len(live))
        return live

    def _downlink_loop(self, tier: str, period: float) -> None:
        while not self._stop.wait(period):
            for device_id in self._live_devices():
                with self._lock:
                    self._dl_seq += 1
                    seq = self._dl_seq
                payload = metrics.build_payload(device_id, seq, tier, DL_PAYLOAD_BYTES)
                info = self._client.publish(
                    f"slice_d/dl/{device_id}", payload, qos=MQTT_QOS
                )
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    M.bytes_sent.labels(tier=tier).inc(len(payload))
                    M.msgs_sent.labels(tier=tier).inc()
                    with self._lock:
                        self._dl_bytes += len(payload)
                    log.debug("dl seq=%d tier=%s dev=%s bytes=%d", seq, tier, device_id, len(payload))
                else:
                    M.publish_errors.inc()
                    log.debug("dl publish failed dev=%s tier=%s rc=%s", device_id, tier, info.rc)

    # -- Summary logging ------------------------------------------------------
    def _report_loop(self) -> None:
        while not self._stop.wait(LOG_INTERVAL_S):
            now = time.monotonic()
            elapsed = now - self._last_report
            with self._lock:
                ul_delta = self._ul_bytes - self._last_ul_bytes
                dl_delta = self._dl_bytes - self._last_dl_bytes
                self._last_ul_bytes = self._ul_bytes
                self._last_dl_bytes = self._dl_bytes
                delays = self._ul_delays
                self._ul_delays = []
                connected = self._connected
                reconnects = self._reconnects
                ndevices = len(self._devices)
            self._last_report = now
            tp_ul = ul_delta / elapsed if elapsed > 0 else 0.0
            tp_dl = dl_delta / elapsed if elapsed > 0 else 0.0
            avg_delay_ms = (sum(delays) / len(delays) * 1000) if delays else 0.0
            log.info(
                "[slice-d edge] up=%ds conn=%d tp_ul=%s tp_dl=%s "
                "avg_ul_delay=%dms (n=%d) reconnects=%d devices=%d",
                int(now - self._start_time),
                int(connected),
                _fmt_rate(tp_ul),
                _fmt_rate(tp_dl),
                round(avg_delay_ms),
                len(delays),
                reconnects,
                ndevices,
            )

    # -- Lifecycle ------------------------------------------------------------
    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_BIND_IP)
        metrics.start_chrony_offset_updater(M.clock_offset, host=CHRONYC_HOST)

        self._client.connect_async(LOCAL_BROKER_HOST, LOCAL_BROKER_PORT, keepalive=60)
        self._client.loop_start()

        threading.Thread(
            target=self._downlink_loop,
            args=("fast", DL_FAST_PERIOD_S),
            name="dl-fast",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._downlink_loop,
            args=("slow", DL_SLOW_PERIOD_S),
            name="dl-slow",
            daemon=True,
        ).start()
        threading.Thread(target=self._report_loop, name="report", daemon=True).start()

        log.info(
            "started: qos=%d broker=%s:%d dl_fast=%.0fs dl_slow=%.0fs metrics=%s:%d",
            MQTT_QOS,
            LOCAL_BROKER_HOST,
            LOCAL_BROKER_PORT,
            DL_FAST_PERIOD_S,
            DL_SLOW_PERIOD_S,
            METRICS_BIND_IP,
            METRICS_PORT,
        )
        self._stop.wait()

    def shutdown(self, *_args) -> None:
        log.info("shutdown requested; flushing")
        self._stop.set()
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass


def main() -> None:
    _validate()
    controller = Controller()
    signal.signal(signal.SIGTERM, controller.shutdown)
    signal.signal(signal.SIGINT, controller.shutdown)
    controller.run()


if __name__ == "__main__":
    main()
