"""Slice A client publisher (rtspclientsink, RECORD mode).

Pushes the encoded H.264 stream to the edge over RTSP RECORD. The UE opens a
single OUTBOUND connection to the edge (TCP-interleaved by default), so this
works when the UE sits behind the 5G UPF/N6 NAT and is not reachable inbound --
the earlier design ran a gst-rtsp-server on the UE and had the edge connect back
to it, which is impractical over a real 5G bearer.

Only the signaling direction changed: the media (and its wall-clock carriage)
still flows uplink UE -> edge, so the absolute-timestamp mechanism is preserved:
  * the RTP payloader carries the RFC 6051 NTP-64 header extension (per packet);
  * rtspclientsink's internal rtpbin emits RTCP SR mapping RTP time to NTP wall
    clock.
Both are sent by the UE outbound, which is exactly the direction we need.
"""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtp", "1.0")
from gi.repository import GLib, Gst, GstRtp  # noqa: E402

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
VIDEO_SOURCE = _env("VIDEO_SOURCE", "/data/sample.mp4")
# Edge RTSP endpoint the UE pushes to (the edge runs the RTSP RECORD server).
RTSP_TARGET_HOST = _env("RTSP_TARGET_HOST", "analyzer")
RTSP_PORT = _env_int("RTSP_PORT", 8554)
STREAM_PATH = _env("STREAM_PATH", "slicea")
# tcp keeps a single outbound (NAT/firewall-robust) connection; udp is optional.
RTSP_PROTOCOL = _env("RTSP_PROTOCOL", "tcp")
FPS = _env_int("FPS", 25)
WIDTH = _env_int("WIDTH", 1280)
HEIGHT = _env_int("HEIGHT", 720)
BITRATE_KBPS = _env_int("BITRATE_KBPS", 4000)
METRICS_PORT = _env_int("METRICS_PORT", 9101)
METRICS_ADDR = _env("METRICS_ADDR", "0.0.0.0")
LOG_INTERVAL_S = float(_env("LOG_INTERVAL_S", "1"))
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None

RTSP_URI = f"rtsp://{RTSP_TARGET_HOST}:{RTSP_PORT}/{STREAM_PATH}"

# --- Metrics ----------------------------------------------------------------
FRAMES_SENT = Counter(
    "cctv_ue_frames_sent_total",
    "Total encoded frames handed to the RTP payloader",
)
FPS_GAUGE = Gauge("cctv_ue_fps", "Frames per second (UE publisher)")
ENCODE_SECONDS = Histogram(
    "cctv_ue_encode_seconds",
    "Per-frame encode latency",
    buckets=metrics.LATENCY_BUCKETS,
)
CLOCK_OFFSET = Gauge(
    "cctv_ue_clock_offset_seconds",
    "Absolute chrony clock offset (client)",
)


class Publisher:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.pipeline: Gst.Pipeline | None = None
        self._source_path = VIDEO_SOURCE
        self._loop_file = not VIDEO_SOURCE.startswith("/dev/video")
        self._enc_sink_times: dict[int, float] = {}
        self._lock = threading.Lock()
        self._frame_count = 0
        self._last_frame_count = 0
        self._last_report = time.monotonic()
        self._stop = False
        self._segment_seek_started = False
        # Why the current GLib loop quit: "eos" (file end / loop miss) or "error".
        self._quit_reason: str | None = None
        self._frames_at_run_start = 0

    # -- Source preparation ---------------------------------------------------
    def _prepare_source(self) -> None:
        """Ensure video file exists (downloading to volume if missing) and remux to faststart.

        decodebin inserts a typefind element that forces qtdemux into push mode;
        if the moov atom sits at EOF (non-faststart files), qtdemux gives up
        after 10 MB. Remuxing (no re-encode) puts the moov at the front. Runs
        once at startup; H.264-in-MP4 is assumed (typical for CCTV).
        """
        if self._source_path.startswith("/dev/video"):
            return

        # 1. Download video on connect if it does not exist or is empty
        if not os.path.exists(self._source_path) or os.path.getsize(self._source_path) == 0:
            target_dir = os.path.dirname(self._source_path)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            video_url = os.environ.get(
                "VIDEO_URL",
                "https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4",
            )
            metrics.log_json("info", "downloading_video_source", path=self._source_path, url=video_url)
            try:
                import urllib.request
                urllib.request.urlretrieve(video_url, self._source_path)
                metrics.log_json(
                    "info",
                    "download_complete",
                    path=self._source_path,
                    size_bytes=os.path.getsize(self._source_path),
                )
            except Exception as exc:
                metrics.log_json("warn", "download_failed", error=str(exc))

        if not self._source_path.lower().endswith((".mp4", ".mov", ".m4v", ".qt")):
            return
        out = "/tmp/source_faststart.mp4"
        desc = (
            f"filesrc location={self._source_path} ! qtdemux ! h264parse ! "
            f"mp4mux faststart=true ! filesink location={out}"
        )
        try:
            pipe = Gst.parse_launch(desc)
        except GLib.Error as exc:
            metrics.log_json("warn", "remux_skip", error=str(exc))
            return
        pipe.set_state(Gst.State.PLAYING)
        msg = pipe.get_bus().timed_pop_filtered(
            120 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        pipe.set_state(Gst.State.NULL)
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            metrics.log_json("warn", "remux_failed", error=str(err))
            return
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            metrics.log_json("warn", "remux_empty", source=self._source_path)
            return
        metrics.log_json("info", "remux_done", source=self._source_path, faststart=out)
        self._source_path = out

    # -- Launch description ---------------------------------------------------
    def _build_pipeline_desc(self) -> str:
        caps = f"video/x-raw,framerate={FPS}/1"
        scale = f"video/x-raw,width={WIDTH},height={HEIGHT}"
        enc = (
            f"x264enc name=enc tune=zerolatency speed-preset=ultrafast "
            f"key-int-max={FPS} bitrate={BITRATE_KBPS}"
        )
        if self._source_path.startswith("/dev/video"):
            source = f"v4l2src device={self._source_path} do-timestamp=true"
        else:
            # do-timestamp paces playback to the (system) pipeline clock; filesrc
            # is seekable so demuxers find the moov atom; looping via seek on EOS.
            # name=src so segment seeks target the source, not rtspclientsink.
            source = (
                f"filesrc name=src location={self._source_path} do-timestamp=true"
            )
        # rtspclientsink auto-creates the RTP payloader; ntp-time-source=ntp maps
        # RTCP SR / NTP-64 to true wall clock (matches the old server's rtpbin).
        return (
            f"{source} ! decodebin ! videoconvert ! videorate ! {caps} ! "
            f"videoscale ! {scale} ! {enc} ! h264parse ! "
            f"rtspclientsink name=sink location={RTSP_URI} "
            f"protocols={RTSP_PROTOCOL} ntp-time-source=ntp"
        )

    # -- Payloader configuration (per RTSP RECORD session) --------------------
    def _on_new_payloader(self, _sink, payloader) -> None:
        """Configure the payloader rtspclientsink just created.

        This replaces the old gst-rtsp-server media-configure hook: the NTP-64
        header extension and SPS/PPS repetition are attached here instead.
        """
        try:
            payloader.set_property("config-interval", 1)
        except (TypeError, GLib.Error):
            pass
        self._add_ntp64_extension(payloader)

    @staticmethod
    def _add_ntp64_extension(payloader) -> None:
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
        except Exception as exc:  # noqa: BLE001 - never fail the pipeline
            metrics.log_json("warn", "ntp64_ext_failed", error=str(exc))

    # -- Encoder probes -------------------------------------------------------
    def _attach_enc_probes(self) -> None:
        if self.pipeline is None:
            return
        enc = self.pipeline.get_by_name("enc")
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

    # -- Looping --------------------------------------------------------------
    def _segment_seek(self, flush: bool) -> bool:
        """Seek to the start as a SEGMENT seek on the file source only.

        A SEGMENT seek makes the source post SEGMENT_DONE (not EOS) at the end,
        so EOS never reaches rtspclientsink and the RTSP RECORD session stays up
        across loops. Seeking the whole pipeline fails when rtspclientsink (a
        live network sink) rejects the seek; target filesrc (name=src) instead.
        """
        if self.pipeline is None:
            return False
        flags = Gst.SeekFlags.SEGMENT | Gst.SeekFlags.KEY_UNIT
        if flush:
            flags |= Gst.SeekFlags.FLUSH
        target = self.pipeline.get_by_name("src")
        if target is None:
            metrics.log_json("warn", "segment_seek_no_src")
            return False
        ok = target.seek(
            1.0,
            Gst.Format.TIME,
            flags,
            Gst.SeekType.SET,
            0,
            Gst.SeekType.NONE,
            -1,
        )
        if not ok:
            # filesrc often rejects TIME seeks; send the event upstream from the
            # encoder sink pad so qtdemux handles it without touching rtspclientsink.
            event = Gst.Event.new_seek(
                1.0,
                Gst.Format.TIME,
                flags,
                Gst.SeekType.SET,
                0,
                Gst.SeekType.NONE,
                -1,
            )
            enc = self.pipeline.get_by_name("enc")
            enc_sink = enc.get_static_pad("sink") if enc is not None else None
            if enc_sink is not None:
                ok = enc_sink.send_event(event)
            if not ok:
                pad = target.get_static_pad("src")
                if pad is not None:
                    ok = pad.push_event(event)
        if not ok:
            metrics.log_json("warn", "segment_seek_failed", flush=flush)
        elif flush:
            metrics.log_json("info", "segment_seek_armed", flush=flush)
        return bool(ok)

    # -- Bus ------------------------------------------------------------------
    def _on_bus(self, _bus, message):
        mtype = message.type
        if mtype == Gst.MessageType.ASYNC_DONE:
            # Do NOT flush-seek on start: with filesrc+decodebin+rtspclientsink a
            # flush SEGMENT seek races qtdemux ("atom has bogus size") and aborts
            # the pipeline. Seamless looping via SEGMENT_DONE still works if a
            # later non-flush seek arms segment mode; otherwise EOS reconnects
            # immediately (see run()).
            return True
        elif mtype == Gst.MessageType.SEGMENT_DONE:
            if self._loop_file and not self._stop:
                self._segment_seek(flush=False)
            return True
        elif mtype == Gst.MessageType.EOS:
            # File end (or loop miss): rebuild the pipeline. run() reconnects
            # with backoff_s=0 for expected file EOS so zeros last <1s.
            metrics.log_json("warn", "eos", uri=RTSP_URI)
            self._quit_reason = "eos"
            self.loop.quit()
        elif mtype == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            metrics.log_json("error", "pipeline_error", error=str(err), debug=debug)
            self._quit_reason = "error"
            self.loop.quit()
        return True

    # -- Periodic reporting ---------------------------------------------------
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
                "publisher_stats",
                fps=round(fps, 2),
                frames_total=count,
                target_bitrate_kbps=BITRATE_KBPS,
                path=STREAM_PATH,
            )

    # -- Run ------------------------------------------------------------------
    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_ADDR)
        metrics.start_chrony_offset_updater(CLOCK_OFFSET, host=CHRONYC_HOST)
        self._prepare_source()
        threading.Thread(target=self._report_loop, daemon=True).start()

        metrics.log_json(
            "info",
            "publisher_start",
            target=RTSP_URI,
            protocol=RTSP_PROTOCOL,
            video_source=self._source_path,
            fps=FPS,
            resolution=f"{WIDTH}x{HEIGHT}",
        )

        backoff = 1.0
        while not self._stop:
            metrics.log_json("info", "starting_pipeline", uri=RTSP_URI)
            self._segment_seek_started = False
            self._quit_reason = None
            with self._lock:
                self._frames_at_run_start = self._frame_count
            try:
                self.pipeline = Gst.parse_launch(self._build_pipeline_desc())
            except GLib.Error as exc:
                metrics.log_json("error", "parse_failed", error=str(exc))
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            # System clock so RTCP SR / NTP-64 map RTP time to true wall clock.
            self.pipeline.use_clock(Gst.SystemClock.obtain())

            sink = self.pipeline.get_by_name("sink")
            if sink is not None:
                sink.connect("new-payloader", self._on_new_payloader)
            self._attach_enc_probes()

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
                frames_this_run = self._frame_count - self._frames_at_run_start
            # Any frames delivered means the session was healthy; reset backoff.
            if frames_this_run > 0:
                backoff = 1.0

            # Expected file-end (segment loop miss): rebuild immediately.
            if self._quit_reason == "eos" and self._loop_file:
                metrics.log_json(
                    "warn",
                    "reconnecting",
                    backoff_s=0.0,
                    reason="eos",
                    frames_this_run=frames_this_run,
                )
                continue

            # Real failure (edge down, RTSP error, etc.): exponential backoff.
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
    Publisher().run()


if __name__ == "__main__":
    main()
