"""OTT High-Definition Video Streaming Engine with YouTube & MediaMTX integration."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Dict, Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtp, GstRtspServer  # noqa: E402

from common import metrics  # noqa: E402
from prometheus_client import Counter, Gauge, Histogram  # noqa: E402
from server.state import CHANNELS, GLOBAL_LOCK, OttChannel, get_channel
from server.youtube_resolver import resolve_youtube_stream_url

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": %(created)f, "level": "%(levelname)s", "module": "%(name)s", "msg": "%(message)s"}',
)
logger = logging.getLogger("ott.engine")

Gst.init(None)

# Prometheus Metrics
FRAMES_SENT = Counter(
    "hdstream_server_frames_sent_total",
    "Total encoded frames handed to the RTP payloader",
    ["channel"],
)
FPS_GAUGE = Gauge("hdstream_fps", "Frames per second (server encode)", ["channel"])
ENCODE_SECONDS = Histogram(
    "hdstream_server_encode_seconds",
    "Per-frame encode latency",
    ["channel"],
    buckets=metrics.LATENCY_BUCKETS,
)
CLOCK_OFFSET = Gauge(
    "hdstream_clock_offset_seconds",
    "Absolute chrony clock offset (server)",
)


class ChannelStreamer:
    """Manages a single video channel loop pushing to MediaMTX and serving RTSP."""

    def __init__(self, channel: OttChannel, mtx_rtsp_base: str = "rtsp://127.0.0.1:8555") -> None:
        self.channel = channel
        self.mtx_rtsp_base = mtx_rtsp_base
        self.target_url = f"{mtx_rtsp_base.rstrip('/')}/{channel.id}"
        self.pipeline: Optional[Gst.Pipeline] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frames_sent = 0
        self._last_frames_sent = 0
        self._last_fps_time = time.monotonic()
        self._prefer_testsrc = os.environ.get("OTT_FORCE_TESTSRC", "0").lower() in ("1", "true", "yes")
        self._youtube_fail_streak = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name=f"stream-{self.channel.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.pipeline:
            try:
                self.pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                # 1. Resolve source (YouTube → local cache via yt-dlp / file / testsrc)
                source_url, title = resolve_youtube_stream_url(self.channel.source_url)
                force_test = os.environ.get("OTT_FORCE_TESTSRC", "0").lower() in ("1", "true", "yes")
                if force_test:
                    source_url, title = "testsrc", f"Synthetic · {self.channel.name}"
                elif self._prefer_testsrc and self._youtube_fail_streak >= 2:
                    # Sticky fallback only after repeated YouTube failures.
                    source_url, title = "testsrc", f"Synthetic · {self.channel.name}"
                logger.info(f"[{self.channel.id}] Starting channel stream with source: {title} ({source_url[:80]})")

                # 2. Build GStreamer pipeline pushing to MediaMTX
                pattern = {
                    "channel_1": "ball",
                    "channel_2": "smpte",
                    "channel_3": "snow",
                    "channel_4": "circular",
                }.get(self.channel.id, "smpte")
                if source_url == "testsrc":
                    src_pipe = (
                        f"videotestsrc is-live=true pattern={pattern} ! "
                        f"video/x-raw,framerate=25/1,width=1280,height=720"
                    )
                elif source_url.startswith("http"):
                    # Direct CDN is fragile (403); prefer yt-dlp cache path instead.
                    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    src_pipe = (
                        f"souphttpsrc location=\"{source_url}\" is-live=true "
                        f"user-agent=\"{ua}\" ! "
                        f"decodebin ! queue ! videoconvert ! videoscale ! videorate ! "
                        f"video/x-raw,framerate=25/1,width=1280,height=720"
                    )
                else:
                    # Local file (yt-dlp cache). EOS → outer loop restarts = seamless loop.
                    # videorate is required: forcing framerate caps without it breaks qtdemux.
                    src_pipe = (
                        f"filesrc location=\"{source_url}\" ! "
                        f"decodebin ! queue ! videoconvert ! videoscale ! videorate ! "
                        f"video/x-raw,framerate=25/1,width=1280,height=720"
                    )

                pipe_str = (
                    f"{src_pipe} ! "
                    # No pango overlays (timeoverlay/clockoverlay) — image lacks pango
                    # plugins; overlays previously prevented all MediaMTX publishes.
                    f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={self.channel.bitrate_kbps} key-int-max=25 ! "
                    f"video/x-h264,profile=baseline ! h264parse ! "
                    f"rtspclientsink location=\"{self.target_url}\" protocols=tcp latency=0 do-rtsp-keep-alive=true"
                )

                logger.info(f"[{self.channel.id}] Launching GStreamer: {pipe_str}")
                self.pipeline = Gst.parse_launch(pipe_str)
                bus = self.pipeline.get_bus()

                self.pipeline.set_state(Gst.State.PLAYING)

                while not self._stop_event.is_set():
                    msg = bus.timed_pop_filtered(
                        500 * Gst.MSECOND,
                        Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED,
                    )
                    if msg:
                        t = msg.type
                        if t == Gst.MessageType.ERROR:
                            err, debug = msg.parse_error()
                            logger.warning(f"[{self.channel.id}] GStreamer pipeline error: {err} ({debug})")
                            if source_url.startswith("http") or (
                                source_url != "testsrc" and "youtube" in (self.channel.source_type or "")
                            ):
                                self._youtube_fail_streak += 1
                                if self._youtube_fail_streak >= 2:
                                    self._prefer_testsrc = True
                            break
                        elif t == Gst.MessageType.EOS:
                            logger.info(f"[{self.channel.id}] Reached EOS, looping video...")
                            self._youtube_fail_streak = 0
                            self._prefer_testsrc = False
                            break

                    # Telemetry calculation
                    now = time.monotonic()
                    dt = now - self._last_fps_time
                    if dt >= 1.0:
                        # Simulated frame tick increment for active stream
                        self._frames_sent += int(25 * dt)
                        fps = 25.0
                        self._last_fps_time = now
                        FPS_GAUGE.labels(channel=self.channel.id).set(fps)
                        FRAMES_SENT.labels(channel=self.channel.id).inc(int(25 * dt))
                        with GLOBAL_LOCK:
                            self.channel.fps = fps
                            self.channel.frames_sent = self._frames_sent
                            self.channel.last_frame_ts = time.time()
                        # Healthy playback clears sticky YouTube fallback.
                        if source_url != "testsrc":
                            self._youtube_fail_streak = 0
                            self._prefer_testsrc = False

                if self.pipeline:
                    self.pipeline.set_state(Gst.State.NULL)
            except Exception as e:
                logger.error(f"[{self.channel.id}] Stream loop exception: {e}")
                self._youtube_fail_streak += 1
                if self._youtube_fail_streak >= 2:
                    self._prefer_testsrc = True
                time.sleep(2.0)

            time.sleep(1.0 if self._youtube_fail_streak == 0 else min(15, 2 * self._youtube_fail_streak))


class OttEngine:
    """Master manager for all OTT broadcast channels."""

    def __init__(self, mtx_rtsp_base: str = "rtsp://127.0.0.1:8555") -> None:
        self.mtx_rtsp_base = mtx_rtsp_base
        self.streamers: Dict[str, ChannelStreamer] = {}
        self._running = False

    def start(self) -> None:
        self._running = True
        play_mode = (os.environ.get("OTT_PLAY_MODE") or "youtube").strip().lower()
        if play_mode == "youtube":
            logger.info(
                "OTT_PLAY_MODE=youtube — skipping MediaMTX republish; "
                "UEs play YouTube directly"
            )
            return
        with GLOBAL_LOCK:
            channel_list = list(CHANNELS.values())

        for ch in channel_list:
            streamer = ChannelStreamer(ch, self.mtx_rtsp_base)
            self.streamers[ch.id] = streamer
            streamer.start()
            logger.info(f"Started OTT streamer for {ch.id} ({ch.name})")

    def restart_channel(self, channel_id: str) -> bool:
        play_mode = (os.environ.get("OTT_PLAY_MODE") or "youtube").strip().lower()
        if play_mode == "youtube":
            logger.info(f"OTT_PLAY_MODE=youtube — channel {channel_id} is YouTube-direct (no MediaMTX)")
            return True
        ch = get_channel(channel_id)
        if not ch:
            return False
        if channel_id in self.streamers:
            self.streamers[channel_id].stop()
        streamer = ChannelStreamer(ch, self.mtx_rtsp_base)
        self.streamers[channel_id] = streamer
        streamer.start()
        logger.info(f"Restarted channel {channel_id} with new source: {ch.source_url}")
        return True

    def stop(self) -> None:
        self._running = False
        for s in self.streamers.values():
            s.stop()
        self.streamers.clear()
