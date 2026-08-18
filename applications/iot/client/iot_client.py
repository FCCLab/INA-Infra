"""Slice D client: simulated best-effort IoT fleet (paho-mqtt).

Simulates ``NUM_DEVICES`` devices. Each device runs three uplink publishers
(fast/med/slow) that publish synthetic sensor reports to ``slice_d/ul/<dev>``
and subscribes to ``slice_d/dl/<dev>`` for the edge controller's downlink
control messages, computing one-way DL delay per message.

Slice D has no SLO: its only job is to generate representative background MQTT
load over the OTA (5G) path so slices A-C can be shown to stay protected. All
timing/throughput is exported on ens0 via Prometheus and summarised to stdout.

MQTT sockets bind to the OTA interface IP (``OTA_BIND_IP``); the metrics HTTP
server binds to the metrics interface (``METRICS_BIND_IP``, i.e. ens0).
"""

from __future__ import annotations

import logging
import os
import random
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

from common import metrics


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #
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
    sys.stderr.write(f"[slice-d client] config error: {msg}\n")
    sys.exit(2)


BROKER_HOST = _env("BROKER_HOST", "")
BROKER_PORT = _env_int("BROKER_PORT", 1883)
OTA_BIND_IP = _env("OTA_BIND_IP", "")
METRICS_BIND_IP = _env("METRICS_BIND_IP", "0.0.0.0")
METRICS_PORT = _env_int("METRICS_PORT", 9104)

NUM_DEVICES = _env_int("NUM_DEVICES", 5)
FAST_PERIOD_S = _env_float("FAST_PERIOD_S", 60)
MED_PERIOD_S = _env_float("MED_PERIOD_S", 1800)
SLOW_PERIOD_S = _env_float("SLOW_PERIOD_S", 3600)
PAYLOAD_BYTES = {
    "fast": _env_int("PAYLOAD_BYTES_FAST", 256),
    "med": _env_int("PAYLOAD_BYTES_MED", 1024),
    "slow": _env_int("PAYLOAD_BYTES_SLOW", 4096),
}
PERIODS = {"fast": FAST_PERIOD_S, "med": MED_PERIOD_S, "slow": SLOW_PERIOD_S}

MQTT_QOS = _env_int("MQTT_QOS", 0)
LOG_INTERVAL_S = _env_float("LOG_INTERVAL_S", 30)
JITTER_FRAC = _env_float("JITTER_FRAC", 0.1)
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None
UE_ID = os.environ.get("APP_NAME") or os.environ.get("CLIENT_ID") or "iot-ue"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("slice-d.client")


def _validate() -> None:
    if not BROKER_HOST:
        _fail("BROKER_HOST is required")
    if MQTT_QOS not in (0, 1):
        _fail(f"MQTT_QOS must be 0 or 1, got {MQTT_QOS}")
    if NUM_DEVICES < 1:
        _fail(f"NUM_DEVICES must be >= 1, got {NUM_DEVICES}")
    for tier, period in PERIODS.items():
        if period <= 0:
            _fail(f"{tier} period must be > 0, got {period}")
    if not 0 <= JITTER_FRAC < 1:
        _fail(f"JITTER_FRAC must be in [0, 1), got {JITTER_FRAC}")


M = metrics.build_side_metrics("client")


def _fmt_rate(bytes_per_s: float) -> str:
    return f"{bytes_per_s / 1024:.1f}kB/s"


class IoTClient:
    def __init__(self) -> None:
        self._devices = [f"dev-{i:03d}" for i in range(NUM_DEVICES)]
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connect_count = 0
        self._reconnects = 0
        self._connected = False
        self._start_time = time.monotonic()

        # Rolling window state for the summary line (reset each interval).
        self._dl_delays: list[float] = []
        # Cumulative byte counters (mirror the Prometheus counters) for rates.
        self._ul_bytes = 0
        self._dl_bytes = 0
        self._last_ul_bytes = 0
        self._last_dl_bytes = 0
        self._last_report = time.monotonic()

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="slice-d-client",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

    # -- MQTT callbacks (paho v2 signatures) ---------------------------------
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
        for dev in self._devices:
            client.subscribe(f"slice_d/dl/{dev}", qos=MQTT_QOS)
        log.info(
            "connected to %s:%d (reconnect=%s), subscribed %d downlink topics",
            BROKER_HOST,
            BROKER_PORT,
            is_reconnect,
            len(self._devices),
        )

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties):
        with self._lock:
            self._connected = False
        M.connected.set(0)
        log.warning("disconnected: %s (auto-reconnecting)", reason_code)

    def _on_message(self, _client, _userdata, msg):
        recv = time.time()
        parsed = metrics.parse_payload(msg.payload)
        if parsed is None:
            return
        tier = parsed.get("tier", "unknown")
        delay, skew = metrics.compute_delay(parsed, now=recv)
        if skew:
            M.clock_skew.inc()
        M.delay.labels(tier=tier).observe(delay)
        nbytes = len(msg.payload)
        M.bytes_received.labels(tier=tier).inc(nbytes)
        M.msgs_received.labels(tier=tier).inc()
        with self._lock:
            self._dl_bytes += nbytes
            self._dl_delays.append(delay)
        log.debug("dl seq=%s tier=%s delay=%.1fms", parsed.get("seq"), tier, delay * 1000)

    # -- Publisher threads ----------------------------------------------------
    def _publish_loop(self, device_id: str, tier: str) -> None:
        period = PERIODS[tier]
        size = PAYLOAD_BYTES[tier]
        seq = 0
        # Stagger the initial fire so N devices don't publish in lockstep.
        self._stop.wait(random.uniform(0, period) * JITTER_FRAC)
        while not self._stop.is_set():
            seq += 1
            sensor = {
                "temp": round(random.uniform(15.0, 35.0), 2),
                "hum": round(random.uniform(20.0, 80.0), 2),
            }
            payload = metrics.build_payload(device_id, seq, tier, size, sensor=sensor)
            info = self._client.publish(
                f"slice_d/ul/{device_id}", payload, qos=MQTT_QOS
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                M.bytes_sent.labels(tier=tier).inc(len(payload))
                M.msgs_sent.labels(tier=tier).inc()
                with self._lock:
                    self._ul_bytes += len(payload)
                log.debug("ul seq=%d tier=%s dev=%s bytes=%d", seq, tier, device_id, len(payload))
            else:
                M.publish_errors.inc()
                log.debug("ul publish failed dev=%s tier=%s rc=%s", device_id, tier, info.rc)
            jittered = period * random.uniform(1 - JITTER_FRAC, 1 + JITTER_FRAC)
            self._stop.wait(jittered)

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
                delays = self._dl_delays
                self._dl_delays = []
                connected = self._connected
                reconnects = self._reconnects
            self._last_report = now
            tp_ul = ul_delta / elapsed if elapsed > 0 else 0.0
            tp_dl = dl_delta / elapsed if elapsed > 0 else 0.0
            avg_delay_ms = (sum(delays) / len(delays) * 1000) if delays else 0.0
            tp_mbps = ((ul_delta + dl_delta) * 8.0) / (elapsed * 1e6) if elapsed > 0 else 0.0
            metrics.set_ue_slo(UE_ID, avg_delay_ms, tp_mbps)
            metrics.set_agg_slo(avg_delay_ms, tp_mbps)
            log.info(
                "[slice-d client] up=%ds conn=%d tp_ul=%s tp_dl=%s "
                "avg_dl_delay=%dms (n=%d) reconnects=%d",
                int(now - self._start_time),
                int(connected),
                _fmt_rate(tp_ul),
                _fmt_rate(tp_dl),
                round(avg_delay_ms),
                len(delays),
                reconnects,
            )

    # -- Lifecycle ------------------------------------------------------------
    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_BIND_IP)
        metrics.start_chrony_offset_updater(M.clock_offset, host=CHRONYC_HOST)

        # connect_async + loop_start retries the initial connection and handles
        # reconnects with the configured exponential backoff.
        self._client.connect_async(
            BROKER_HOST, BROKER_PORT, keepalive=60, bind_address=OTA_BIND_IP or ""
        )
        self._client.loop_start()

        for dev in self._devices:
            for tier in metrics.UL_TIERS:
                threading.Thread(
                    target=self._publish_loop,
                    args=(dev, tier),
                    name=f"pub-{dev}-{tier}",
                    daemon=True,
                ).start()

        threading.Thread(target=self._report_loop, name="report", daemon=True).start()

        log.info(
            "started: devices=%d qos=%d broker=%s:%d ota_bind=%s metrics=%s:%d",
            NUM_DEVICES,
            MQTT_QOS,
            BROKER_HOST,
            BROKER_PORT,
            OTA_BIND_IP or "(any)",
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
    client = IoTClient()
    signal.signal(signal.SIGTERM, client.shutdown)
    signal.signal(signal.SIGINT, client.shutdown)
    client.run()


if __name__ == "__main__":
    main()
