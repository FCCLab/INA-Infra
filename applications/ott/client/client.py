"""hd-stream UE client (rtspsrc PLAY pull).

Pulls an H.264 RTSP stream from the central PLAY server over a single outbound
connection (TCP-interleaved by default), so it works behind the 5G UPF/N6 NAT.
Reconstructs per-frame send wall-clock from GstReferenceTimestampMeta and
computes net_delay = now - send_time. No YOLO / inference.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from common import metrics  # noqa: E402
from prometheus_client import Counter, Gauge, Histogram  # noqa: E402

Gst.init(None)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# --- Configuration ---------------------------------------------------------
RTSP_TARGET_HOST = _env("RTSP_TARGET_HOST", "hd-stream-server")
RTSP_PORT = _env_int("RTSP_PORT", 8556)
STREAM_PATH = _env("STREAM_PATH", "hdstream")
RTSP_PROTOCOL = _env("RTSP_PROTOCOL", "tcp")
RTSP_LATENCY_MS = _env_int("RTSP_LATENCY_MS", 0)
METRICS_PORT = _env_int("METRICS_PORT", 9111)
METRICS_ADDR = _env("METRICS_ADDR", "0.0.0.0")
LOG_INTERVAL_S = float(_env("LOG_INTERVAL_S", "1"))
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None

RTSP_URI = f"rtsp://{RTSP_TARGET_HOST}:{RTSP_PORT}/{STREAM_PATH}"

# --- Metrics ----------------------------------------------------------------
NET_DELAY = Histogram(
    "hdstream_net_delay_seconds",
    "Send-to-appsink delay (encode + network + decode)",
    buckets=metrics.LATENCY_BUCKETS,
)
FRAMES_PROCESSED = Counter(
    "hdstream_frames_processed_total", "Frames pulled from appsink / decoded"
)
FRAMES_WITHOUT_TS = Counter(
    "hdstream_frames_without_ts_total",
    "Frames lacking a reference-timestamp meta (SR not yet received)",
)
FPS_GAUGE = Gauge("hdstream_fps", "Frames per second (client receive)")
CLOCK_OFFSET = Gauge(
    "hdstream_clock_offset_seconds", "Absolute chrony clock offset (client)"
)


class StreamClient:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.pipeline: Gst.Pipeline | None = None
        self._lock = threading.Lock()
        self._decoded_count = 0
        self._last_decoded_count = 0
        self._last_report = time.monotonic()
        self._stop = False
        self._quit_reason: str | None = None
        self._frames_at_run_start = 0
        self._net_samples: list[float] = []

    def _build_pipeline_desc(self) -> str:
        # protocols=tcp keeps a single outbound NAT-safe connection.
        # Keep latency small but avoid buffer-mode=none + drop-on-latency, which
        # can stall the interleaved TCP session after a few frames on this path.
        return (
            f"rtspsrc name=src location={RTSP_URI} protocols={RTSP_PROTOCOL} "
            f"latency={RTSP_LATENCY_MS} ntp-sync=true "
            f"add-reference-timestamp-meta=true ! "
            f"rtph264depay ! avdec_h264 ! videoconvert ! "
            f"appsink name=sink emit-signals=true max-buffers=8 drop=true sync=false"
        )

    def _configure_rtspsrc(self) -> None:
        if self.pipeline is None:
            return
        src = self.pipeline.get_by_name("src")
        if src is None:
            return
        self._set_if_present(src, "ntp-sync", True)
        self._set_if_present(src, "add-reference-timestamp-meta", True)

    def _on_deep_element_added(self, _bin, _sub_bin, element) -> None:
        self._configure_rtp_element(element)

    def _configure_rtp_recurse(self, bin_) -> None:
        if not isinstance(bin_, Gst.Bin):
            return
        it = bin_.iterate_recurse()
        while True:
            res, el = it.next()
            if res == Gst.IteratorResult.OK:
                self._configure_rtp_element(el)
            elif res == Gst.IteratorResult.RESYNC:
                it.resync()
            else:
                break

    def _configure_rtp_element(self, element) -> None:
        factory = element.get_factory()
        name = factory.get_name() if factory is not None else ""
        if name in ("rtpbin", "rtspsrc"):
            self._set_if_present(element, "ntp-sync", True)
            self._set_if_present(element, "add-reference-timestamp-meta", True)
            self._set_if_present(element, "buffer-mode", 0)
        elif name == "rtpjitterbuffer":
            self._set_if_present(element, "add-reference-timestamp-meta", True)
            self._set_if_present(element, "mode", 0)

    @staticmethod
    def _set_if_present(element, prop: str, value) -> None:
        if element.find_property(prop) is not None:
            try:
                element.set_property(prop, value)
            except (TypeError, GLib.Error):
                pass

    _NTP_REF_CAPS = Gst.Caps.from_string("timestamp/x-ntp")

    @classmethod
    def _read_reference_ns(cls, buf: Gst.Buffer) -> Optional[int]:
        meta = None
        try:
            meta = buf.get_reference_timestamp_meta(cls._NTP_REF_CAPS)
            if meta is None:
                meta = buf.get_reference_timestamp_meta(None)
        except (TypeError, AttributeError):
            meta = None
        if meta is None or meta.timestamp == Gst.CLOCK_TIME_NONE:
            return None
        return metrics.normalize_reference_ns(int(meta.timestamp))

    def _on_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        if buf is None:
            return Gst.FlowReturn.OK

        now_ns = time.time_ns()
        FRAMES_PROCESSED.inc()
        with self._lock:
            self._decoded_count += 1

        t_send_ns = self._read_reference_ns(buf)
        if t_send_ns is None:
            FRAMES_WITHOUT_TS.inc()
            return Gst.FlowReturn.OK

        net_delay = (now_ns - t_send_ns) / 1e9
        NET_DELAY.observe(max(net_delay, 0.0))
        with self._lock:
            self._net_samples.append(net_delay)
        return Gst.FlowReturn.OK

    def _on_bus(self, _bus, message):
        mtype = message.type
        if mtype == Gst.MessageType.EOS:
            metrics.log_json("warn", "eos", uri=RTSP_URI)
            self._quit_reason = "eos"
            self.loop.quit()
        elif mtype == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            metrics.log_json("error", "pipeline_error", error=str(err), debug=debug)
            self._quit_reason = "error"
            self.loop.quit()
        return True

    @staticmethod
    def _pctile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
        return ordered[idx]

    def _report_loop(self) -> None:
        while not self._stop:
            time.sleep(LOG_INTERVAL_S)
            now = time.monotonic()
            with self._lock:
                decoded = self._decoded_count
                net = self._net_samples
                self._net_samples = []
            delta = decoded - self._last_decoded_count
            elapsed = now - self._last_report
            fps = delta / elapsed if elapsed > 0 else 0.0
            FPS_GAUGE.set(fps)
            self._last_decoded_count = decoded
            self._last_report = now
            metrics.log_json(
                "info",
                "client_stats",
                fps=round(fps, 2),
                frames_total=decoded,
                net_delay_p50_ms=round(self._pctile(net, 0.50) * 1000, 3),
                net_delay_p99_ms=round(self._pctile(net, 0.99) * 1000, 3),
                path=STREAM_PATH,
            )

    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_ADDR)
        metrics.start_chrony_offset_updater(CLOCK_OFFSET, host=CHRONYC_HOST)
        threading.Thread(target=self._report_loop, daemon=True).start()

        metrics.log_json(
            "info",
            "client_start",
            target=RTSP_URI,
            protocol=RTSP_PROTOCOL,
        )

        backoff = 1.0
        while not self._stop:
            metrics.log_json("info", "starting_pipeline", uri=RTSP_URI)
            self._quit_reason = None
            with self._lock:
                self._frames_at_run_start = self._decoded_count
            try:
                self.pipeline = Gst.parse_launch(self._build_pipeline_desc())
            except GLib.Error as exc:
                metrics.log_json("error", "parse_failed", error=str(exc))
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            self.pipeline.use_clock(Gst.SystemClock.obtain())
            self._configure_rtspsrc()

            sink = self.pipeline.get_by_name("sink")
            if sink is not None:
                sink.connect("new-sample", self._on_sample)

            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus)

            self.pipeline.set_state(Gst.State.PLAYING)
            self.loop = GLib.MainLoop()
            try:
                self.loop.run()
            except KeyboardInterrupt:
                self._stop = True

            self.pipeline.set_state(Gst.State.NULL)

            if self._stop:
                break

            with self._lock:
                frames_this_run = self._decoded_count - self._frames_at_run_start
            if frames_this_run > 0:
                backoff = 1.0

            # Server loop / brief disconnect: reconnect quickly.
            if self._quit_reason == "eos":
                metrics.log_json(
                    "warn",
                    "reconnecting",
                    backoff_s=0.5,
                    reason="eos",
                    frames_this_run=frames_this_run,
                )
                time.sleep(0.5)
                continue

            metrics.log_json(
                "warn",
                "reconnecting",
                backoff_s=backoff,
                reason=self._quit_reason or "unknown",
                frames_this_run=frames_this_run,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def main() -> None:
    StreamClient().run()


if __name__ == "__main__":
    main()
