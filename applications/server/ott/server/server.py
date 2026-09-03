"""hd-stream central server (gst-rtsp-server, PLAY mode).

Hosts a looped MP4 on a public DN address and serves it over RTSP PLAY so a UE
behind UPF NAT can open a single OUTBOUND connection and pull the stream.

Per-frame send wall-clock travels in-band via RTP/RTCP (RTCP SR + RFC 6051
NTP-64 header extension). The client reconstructs it from
GstReferenceTimestampMeta and computes net_delay = now - send_time.
"""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtp, GstRtspServer  # noqa: E402

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
VIDEO_SOURCE = _env("VIDEO_SOURCE", "/data/source.mp4")
BIND_ADDRESS = _env("BIND_ADDRESS", "0.0.0.0")
RTSP_PORT = _env_int("RTSP_PORT", 8556)
STREAM_PATH = _env("STREAM_PATH", "hdstream")
FPS = _env_int("FPS", 25)
WIDTH = _env_int("WIDTH", 1280)
HEIGHT = _env_int("HEIGHT", 720)
BITRATE_KBPS = _env_int("BITRATE_KBPS", 4000)
METRICS_PORT = _env_int("METRICS_PORT", 9112)
METRICS_ADDR = _env("METRICS_ADDR", "0.0.0.0")
LOG_INTERVAL_S = float(_env("LOG_INTERVAL_S", "1"))
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None
VIDEO_WAIT_TIMEOUT_S = _env_int("VIDEO_WAIT_TIMEOUT_S", 3600)

RTSP_URI = f"rtsp://{BIND_ADDRESS}:{RTSP_PORT}/{STREAM_PATH}"

# --- Metrics ----------------------------------------------------------------
FRAMES_SENT = Counter(
    "hdstream_server_frames_sent_total",
    "Total encoded frames handed to the RTP payloader",
)
FPS_GAUGE = Gauge("hdstream_fps", "Frames per second (server encode)")
ENCODE_SECONDS = Histogram(
    "hdstream_server_encode_seconds",
    "Per-frame encode latency",
    buckets=metrics.LATENCY_BUCKETS,
)
CLOCK_OFFSET = Gauge(
    "hdstream_clock_offset_seconds",
    "Absolute chrony clock offset (server)",
)


class StreamServer:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.server: GstRtspServer.RTSPServer | None = None
        self._source_path = VIDEO_SOURCE
        self._enc_sink_times: dict[int, float] = {}
        self._lock = threading.Lock()
        self._frame_count = 0
        self._last_frame_count = 0
        self._last_report = time.monotonic()
        self._stop = False

    def _wait_for_source(self) -> None:
        elapsed = 0
        while not os.path.isfile(self._source_path) or os.path.getsize(self._source_path) == 0:
            if elapsed >= VIDEO_WAIT_TIMEOUT_S:
                raise FileNotFoundError(
                    f"VIDEO_SOURCE {self._source_path} missing after {VIDEO_WAIT_TIMEOUT_S}s"
                )
            if elapsed % 10 == 0:
                metrics.log_json(
                    "info",
                    "waiting_for_video",
                    path=self._source_path,
                    elapsed_s=elapsed,
                )
            time.sleep(2)
            elapsed += 2

    def _prepare_source(self) -> None:
        """Optionally remux to faststart via ffmpeg (video-only).

        Disabled by default: the archive.org BBB sample is already streamable and
        a bad remux previously produced Invalid-NAL stalls. Set
        REMUX_FASTSTART=true to force a remux.
        """
        if os.environ.get("REMUX_FASTSTART", "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            metrics.log_json("info", "remux_skipped", source=self._source_path)
            return
        if not self._source_path.lower().endswith((".mp4", ".mov", ".m4v", ".qt")):
            return
        out = "/tmp/source_faststart.mp4"
        import subprocess

        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    self._source_path,
                    "-an",
                    "-c:v",
                    "copy",
                    "-movflags",
                    "+faststart",
                    out,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            metrics.log_json("warn", "remux_ffmpeg_skip", error=str(exc))
            return
        if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            metrics.log_json(
                "warn",
                "remux_ffmpeg_failed",
                error=(proc.stderr or "")[-400:],
            )
            return
        metrics.log_json("info", "remux_done", source=self._source_path, faststart=out)
        self._source_path = out

    def _build_launch(self) -> str:
        scale = f"video/x-raw,width={WIDTH},height={HEIGHT}"
        enc = (
            f"x264enc name=enc tune=zerolatency speed-preset=ultrafast "
            f"key-int-max={FPS} bitrate={BITRATE_KBPS}"
        )
        # pay0 is required by gst-rtsp-server PLAY factories.
        # Video-only qtdemux path: decodebin stalls when the BBB audio pad is
        # left unlinked under a shared RTSP media.
        return (
            f"( filesrc name=src location={self._source_path} ! "
            f"qtdemux ! h264parse ! avdec_h264 ! videoconvert ! "
            f"videoscale ! {scale} ! {enc} ! "
            f"video/x-h264,profile=baseline ! h264parse ! "
            f"rtph264pay name=pay0 pt=96 config-interval=1 )"
        )

    def _add_ntp64_extension(self, payloader) -> None:
        try:
            ext = GstRtp.RTPHeaderExtension.create_from_uri(
                "urn:ietf:params:rtp-hdrext:ntp-64"
            )
            if ext is None:
                metrics.log_json("warn", "ntp64_ext_unavailable")
                return
            ext.set_id(1)
            payloader.emit("add-extension", ext)
            metrics.log_json("info", "ntp64_ext_added")
        except Exception as exc:  # noqa: BLE001
            metrics.log_json("warn", "ntp64_ext_failed", error=str(exc))

    def _attach_enc_probes(self, element: Gst.Element) -> None:
        enc = element.get_by_name("enc")
        if enc is None:
            return
        sink_pad = enc.get_static_pad("sink")
        src_pad = enc.get_static_pad("src")
        if sink_pad is not None:
            sink_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_enc_sink)
        if src_pad is not None:
            src_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_enc_src)

    def _on_enc_sink(self, _pad, info):
        buf = info.get_buffer()
        if buf is not None and buf.pts != Gst.CLOCK_TIME_NONE:
            with self._lock:
                if len(self._enc_sink_times) < 512:
                    self._enc_sink_times[buf.pts] = time.monotonic()
        return Gst.PadProbeReturn.OK

    def _on_enc_src(self, _pad, info):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        FRAMES_SENT.inc()
        with self._lock:
            self._frame_count += 1
            start = self._enc_sink_times.pop(buf.pts, None)
        if start is not None:
            ENCODE_SECONDS.observe(time.monotonic() - start)
        return Gst.PadProbeReturn.OK

    def _on_media_configure(self, _factory, media) -> None:
        element = media.get_element()
        pay = element.get_by_name("pay0") if element is not None else None
        if pay is not None:
            self._add_ntp64_extension(pay)
        if element is not None:
            self._attach_enc_probes(element)
        try:
            media.set_clock(Gst.SystemClock.obtain())
        except (AttributeError, TypeError, GLib.Error):
            pass
        metrics.log_json("info", "media_configured", path=STREAM_PATH)

    def _report_loop(self) -> None:
        while not self._stop:
            time.sleep(LOG_INTERVAL_S)
            now = time.monotonic()
            with self._lock:
                count = self._frame_count
            delta = count - self._last_frame_count
            elapsed = now - self._last_report
            fps = delta / elapsed if elapsed > 0 else 0.0
            FPS_GAUGE.set(fps)
            self._last_frame_count = count
            self._last_report = now
            metrics.log_json(
                "info",
                "server_stats",
                fps=round(fps, 2),
                frames_total=count,
                target_bitrate_kbps=BITRATE_KBPS,
                path=STREAM_PATH,
            )

    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_ADDR)
        metrics.start_chrony_offset_updater(CLOCK_OFFSET, host=CHRONYC_HOST)
        self._wait_for_source()
        self._prepare_source()
        threading.Thread(target=self._report_loop, daemon=True).start()

        self.server = GstRtspServer.RTSPServer()
        self.server.set_address(BIND_ADDRESS)
        self.server.set_service(str(RTSP_PORT))

        factory = GstRtspServer.RTSPMediaFactory()
        # Default transport mode is PLAY (clients pull).
        factory.set_launch(self._build_launch())
        # Fresh media per PLAY: client reconnect on EOS loops the file without
        # fragile bus watches / seeks on the RTSP media pipeline.
        factory.set_shared(False)
        factory.set_eos_shutdown(True)
        factory.connect("media-configure", self._on_media_configure)

        mounts = self.server.get_mount_points()
        mounts.add_factory(f"/{STREAM_PATH}", factory)
        self.server.attach(None)

        metrics.log_json(
            "info",
            "server_serving",
            uri=RTSP_URI,
            video_source=self._source_path,
            fps=FPS,
            resolution=f"{WIDTH}x{HEIGHT}",
            bitrate_kbps=BITRATE_KBPS,
        )
        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._stop = True


def main() -> None:
    metrics.log_json(
        "info",
        "server_boot",
        uri=RTSP_URI,
        video_source=VIDEO_SOURCE,
    )
    StreamServer().run()


if __name__ == "__main__":
    main()
