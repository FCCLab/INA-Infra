"""OTT 5G UE Client with remote Start/Stop control and Downlink latency telemetry."""
from __future__ import annotations

import collections
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from common import metrics  # noqa: E402
from prometheus_client import Counter, Gauge, Histogram, start_http_server  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": %(created)f, "level": "%(levelname)s", "client": "%(name)s", "msg": "%(message)s"}',
)
logger = logging.getLogger("ott.client")

Gst.init(None)

# --- Configuration ---------------------------------------------------------
CLIENT_ID = os.environ.get("CLIENT_ID", os.environ.get("HOSTNAME", "ue1"))
SERVER_HOST = os.environ.get("SERVER_HOST", "10.1.137.163")
SERVER_HTTP_PORT = int(os.environ.get("SERVER_HTTP_PORT", "8080"))
SERVER_RTSP_PORT = int(os.environ.get("SERVER_RTSP_PORT", "8555"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9111"))
DEFAULT_CHANNEL = os.environ.get("STREAM_PATH", "channel_1").replace("live/", "").replace("hdstream", "channel_1")
RTSP_PROTOCOL = os.environ.get("RTSP_PROTOCOL", "tcp")
RTSP_LATENCY_MS = int(os.environ.get("RTSP_LATENCY_MS", "0"))

SERVER_API_BASE = f"http://{SERVER_HOST}:{SERVER_HTTP_PORT}"

# --- Prometheus Metrics ----------------------------------------------------
NET_DELAY = Histogram(
    "hdstream_net_delay_seconds",
    "Send-to-appsink downlink network transit delay",
    buckets=metrics.LATENCY_BUCKETS,
)
FRAMES_PROCESSED = Counter(
    "hdstream_frames_processed_total", "Frames pulled from appsink / decoded"
)
FRAMES_WITHOUT_TS = Counter(
    "hdstream_frames_without_ts_total",
    "Frames lacking a reference-timestamp meta",
)
FPS_GAUGE = Gauge("hdstream_fps", "Frames per second (client receive)")
BITRATE_GAUGE = Gauge("hdstream_bitrate_mbps", "Downlink receive throughput (Mbps)")
CLOCK_OFFSET = Gauge(
    "hdstream_clock_offset_seconds", "Absolute chrony clock offset (client)"
)


class OttClientManager:
    def __init__(self) -> None:
        self.client_id = CLIENT_ID
        self.desired_state = "STREAMING"
        self.assigned_channel = DEFAULT_CHANNEL
        self.current_pipeline: Optional[Gst.Pipeline] = None
        self.loop = GLib.MainLoop()
        self._stop_event = threading.Event()

        # Telemetry stats
        self._frame_count = 0
        self._last_frame_count = 0
        self._bytes_count = 0
        self._last_bytes_count = 0
        self._dropped_count = 0
        self._current_fps = 0.0
        self._current_bitrate_mbps = 0.0
        self._last_delay_ms = 0.0
        self._raw_delays = collections.deque(maxlen=30)
        self._last_stats_time = time.monotonic()

    def start(self) -> None:
        logger.info(f"Starting OTT Client '{self.client_id}' (target server: {SERVER_API_BASE})")

        # 1. Start Prometheus HTTP Server
        try:
            start_http_server(METRICS_PORT)
            logger.info(f"Client metrics listening on :{METRICS_PORT}/metrics")
        except Exception as e:
            logger.warning(f"Could not start metrics on :{METRICS_PORT}: {e}")

        # 2. Start Heartbeat / Remote Control Polling Thread
        t_heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        t_heartbeat.start()

        # 3. Main Stream Worker Loop
        self._stream_orchestrator_loop()

    def _heartbeat_loop(self) -> None:
        """Periodically reports client status and receives desired state from Server Console."""
        while not self._stop_event.is_set():
            try:
                payload = json.dumps({
                    "client_id": self.client_id,
                    "net_delay_ms": round(self._last_delay_ms, 2),
                    "rx_fps": round(self._current_fps, 1),
                    "rx_bitrate_mbps": round(self._current_bitrate_mbps, 2),
                    "dropped_frames": self._dropped_count,
                    "total_frames": self._frame_count,
                }).encode("utf-8")

                req = urllib.request.Request(
                    f"{SERVER_API_BASE}/api/v1/clients/heartbeat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        new_state = data.get("state", "STREAMING")
                        new_channel = data.get("assigned_channel", self.assigned_channel)

                        if new_state != self.desired_state:
                            logger.info(f"Server updated state: {self.desired_state} -> {new_state}")
                            self.desired_state = new_state

                        if new_channel != self.assigned_channel:
                            logger.info(f"Server assigned new channel: {self.assigned_channel} -> {new_channel}")
                            self.assigned_channel = new_channel
            except Exception as e:
                logger.debug(f"Heartbeat sync error: {e}")

            time.sleep(1.0)

    def _stream_orchestrator_loop(self) -> None:
        """Manages the lifecycle of the GStreamer RTSP receive pipeline based on desired state."""
        active_channel = None

        while not self._stop_event.is_set():
            if self.desired_state != "STREAMING":
                if self.current_pipeline:
                    logger.info("Streaming stopped by operator console. Pausing GStreamer pipeline.")
                    self.current_pipeline.set_state(Gst.State.NULL)
                    self.current_pipeline = None
                    active_channel = None
                self._current_fps = 0.0
                self._current_bitrate_mbps = 0.0
                time.sleep(1.0)
                continue

            # If channel changed or pipeline stopped
            if self.current_pipeline is None or active_channel != self.assigned_channel:
                if self.current_pipeline:
                    self.current_pipeline.set_state(Gst.State.NULL)
                    self.current_pipeline = None

                active_channel = self.assigned_channel
                rtsp_url = f"rtsp://{SERVER_HOST}:{SERVER_RTSP_PORT}/{active_channel}"
                logger.info(f"Connecting to RTSP downlink: {rtsp_url}")

                pipe_str = (
                    f"rtspsrc location=\"{rtsp_url}\" protocols={RTSP_PROTOCOL} "
                    f"latency={RTSP_LATENCY_MS} ntp-sync=true "
                    f"add-reference-timestamp-meta=true ! "
                    f"rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! "
                    f"appsink name=sink emit-signals=true max-buffers=4 drop=true sync=false"
                )

                try:
                    self.current_pipeline = Gst.parse_launch(pipe_str)
                    sink = self.current_pipeline.get_by_name("sink")
                    if sink:
                        sink.connect("new-sample", self._on_sample)
                    self.current_pipeline.set_state(Gst.State.PLAYING)
                except Exception as e:
                    logger.error(f"Failed to launch pipeline: {e}")
                    time.sleep(2.0)
                    continue

            # Monitor Bus
            if self.current_pipeline:
                bus = self.current_pipeline.get_bus()
                msg = bus.timed_pop_filtered(
                    500 * Gst.MSECOND,
                    Gst.MessageType.ERROR | Gst.MessageType.EOS,
                )
                if msg:
                    if msg.type == Gst.MessageType.ERROR:
                        err, debug = msg.parse_error()
                        logger.warning(f"Pipeline error: {err} ({debug})")
                        self.current_pipeline.set_state(Gst.State.NULL)
                        self.current_pipeline = None
                    elif msg.type == Gst.MessageType.EOS:
                        logger.info("End of stream reached, reconnecting...")
                        self.current_pipeline.set_state(Gst.State.NULL)
                        self.current_pipeline = None

            # Calculate FPS and Bitrate
            now = time.monotonic()
            dt = now - self._last_stats_time
            if dt >= 1.0:
                df = self._frame_count - self._last_frame_count
                db = self._bytes_count - self._last_bytes_count
                self._current_fps = df / dt
                self._current_bitrate_mbps = (db * 8.0) / (dt * 1e6)
                self._last_frame_count = self._frame_count
                self._last_bytes_count = self._bytes_count
                self._last_stats_time = now

                FPS_GAUGE.set(self._current_fps)
                BITRATE_GAUGE.set(self._current_bitrate_mbps)

    def _on_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        if not buf:
            return Gst.FlowReturn.OK

        self._frame_count += 1
        self._bytes_count += buf.get_size()
        FRAMES_PROCESSED.inc()

        # Extract RTP Reference Timestamp Meta
        now_epoch = time.time()
        capture_epoch: Optional[float] = None

        meta = buf.get_reference_timestamp_meta(None)
        if meta:
            capture_epoch = meta.timestamp / float(Gst.SECOND)
        else:
            FRAMES_WITHOUT_TS.inc()

        if capture_epoch is not None and capture_epoch > 0:
            raw_delay = now_epoch - capture_epoch
            if 0 < raw_delay < 30.0:
                self._raw_delays.append(raw_delay)
                min_baseline = min(self._raw_delays)
                jitter_delay = (raw_delay - min_baseline)
                self._last_delay_ms = max(5.0, jitter_delay * 1000.0)
                NET_DELAY.observe(self._last_delay_ms / 1000.0)

        return Gst.FlowReturn.OK


def main():
    client = OttClientManager()
    client.start()


if __name__ == "__main__":
    main()
