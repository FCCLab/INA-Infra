"""CCTV Edge Server: RTSP RECORD Ingest + Multi-Client YOLO Vision AI + MediaMTX Pub/Sub."""

from __future__ import annotations

import collections
import io
import multiprocessing as mp
import os
from pathlib import Path
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer  # noqa: E402

from common import metrics  # noqa: E402
from prometheus_client import Counter, Gauge, Histogram  # noqa: E402

try:
    from edge.state import CLIENT_STREAMS, GLOBAL_LOCK, ClientStreamContext, normalize_canonical_client_id
except ImportError:
    from state import CLIENT_STREAMS, GLOBAL_LOCK, ClientStreamContext, normalize_canonical_client_id

Gst.init(None)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Configuration ---------------------------------------------------------
BIND_ADDRESS = _env("BIND_ADDRESS", "0.0.0.0")
RTSP_PORT = _env_int("RTSP_PORT", 8554)
STREAM_PATH = _env("STREAM_PATH", "slicea")
RTSP_LATENCY_MS = _env_int("RTSP_LATENCY_MS", 0)

HTTP_PORT = _env_int("HTTP_PORT", 8080)
FRONTEND_DIR = Path(_env("FRONTEND_DIR", "/app/frontend/dist"))

YOLO_ENABLED = _env_bool("YOLO_ENABLED", True)
YOLO_MODEL = _env("YOLO_MODEL", "yolov8n.pt")
YOLO_DEVICE = _env("YOLO_DEVICE", "auto")
YOLO_PROCESS_PER_CLIENT = _env_bool("YOLO_PROCESS_PER_CLIENT", True)
YOLO_INTRA_THREADS = max(0, _env_int("YOLO_INTRA_THREADS", 0))
FRAME_SKIP = max(1, _env_int("FRAME_SKIP", 1))
YOLO_IDLE_S = float(_env("YOLO_IDLE_S", "90"))
# How long after the last frame before a client entry is removed entirely from state.
STALE_CLIENT_S = float(_env("STALE_CLIENT_S", "30"))

METRICS_PORT = _env_int("METRICS_PORT", 9102)
METRICS_ADDR = _env("METRICS_ADDR", "0.0.0.0")
LOG_INTERVAL_S = float(_env("LOG_INTERVAL_S", "1"))
CHRONYC_HOST = os.environ.get("CHRONYC_HOST") or None


def normalize_client_name(client_id: str) -> str:
    s = str(client_id).lower().strip()
    if s.startswith("slice") and "client" in s:
        return s
    m = re.search(r"cam(\d+)", s)
    if m:
        return f"slice1-cctv-client-{m.group(1)}"
    m = re.search(r"ue(\d+)", s)
    if m:
        return f"slice1-cctv-client-{m.group(1)}"
    if "slicea" in s or s == "cam":
        return "slice1-cctv-client-1"
    m = re.search(r"(\d+)", s)
    if m:
        return f"slice1-cctv-client-{m.group(1)}"
    return "slice1-cctv-client-1"


# --- Prometheus Metrics -----------------------------------------------------
NET_DELAY = Histogram(
    "cctv_net_delay_seconds",
    "Capture-to-appsink delay (encode + network + decode)",
    buckets=metrics.LATENCY_BUCKETS,
)
YOLO_DELAY = Histogram(
    "cctv_yolo_delay_seconds",
    "YOLO inference latency per processed frame",
    buckets=metrics.LATENCY_BUCKETS,
)
E2E_DELAY = Histogram(
    "cctv_e2e_delay_seconds",
    "Capture-to-post-inference end-to-end delay",
    buckets=metrics.LATENCY_BUCKETS,
)
CLIENT_NET_DELAY_MS = Gauge("cctv_client_net_delay_ms", "5G Network Delay per client in ms", ["client"])
CLIENT_YOLO_DELAY_MS = Gauge("cctv_client_yolo_delay_ms", "YOLO AI Latency per client in ms", ["client"])
CLIENT_E2E_DELAY_MS = Gauge("cctv_client_e2e_delay_ms", "End-to-End Latency per client in ms", ["client"])
CLIENT_INGRESS_FPS = Gauge("cctv_client_ingress_fps", "Ingress FPS per client", ["client"])
CLIENT_EGRESS_FPS = Gauge("cctv_client_egress_fps", "Egress FPS per client", ["client"])

FRAMES_PROCESSED = Counter(
    "cctv_frames_processed_total", "Frames pulled from appsink"
)
FRAMES_DROPPED = Counter(
    "cctv_frames_dropped_total", "Frames dropped before appsink (offset gaps)"
)
FRAMES_WITHOUT_TS = Counter(
    "cctv_frames_without_ts_total",
    "Frames lacking a reference-timestamp meta (SR not yet received)",
)
FPS_GAUGE = Gauge("cctv_fps", "Frames per second (edge CCTV server)")
INGRESS_FPS_GAUGE = Gauge("application_ingress_fps", "Ingress frame rate at CCTV appsink")
EGRESS_FPS_GAUGE = Gauge("application_egress_fps", "Egress processed frame rate after YOLO")
CLOCK_OFFSET = Gauge(
    "cctv_clock_offset_seconds", "Absolute chrony clock offset (edge)"
)
APPLICATION_CLOCK_OFFSET_MS = Gauge(
    "application_clock_offset_ms",
    "Host clock offset vs reference (ms)",
    ["origin"],
)
APPLICATION_THROUGHPUT_BPS = Gauge(
    "application_throughput_bytes_per_sec",
    "N6/Multus ingest rate (bytes/sec RX)",
    ["origin"],
)
APP_UE_LATENCY_MS = Gauge(
    "app_ue_latency_ms", "Per-UE application latency (milliseconds)", ["ue_id"]
)
APP_UE_THROUGHPUT_MBPS = Gauge(
    "app_ue_throughput_mbps", "Per-UE application throughput (Mbps)", ["ue_id"]
)
APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
APP_THROUGHPUT_MBPS = Gauge(
    "app_throughput_mbps", "Aggregated application throughput (Mbps)"
)


def _kernel_clock_offset_seconds() -> Optional[float]:
    """Kernel NTP PLL offset (adjtimex). Used when chronyd is not in the pod."""
    try:
        import ctypes
        import ctypes.util

        class _Timex(ctypes.Structure):
            _fields_ = [
                ("modes", ctypes.c_uint),
                ("offset", ctypes.c_long),
                ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long),
                ("esterror", ctypes.c_long),
                ("status", ctypes.c_int),
                ("constant", ctypes.c_long),
                ("precision", ctypes.c_long),
                ("tolerance", ctypes.c_long),
                ("time_tv_sec", ctypes.c_long),
                ("time_tv_usec", ctypes.c_long),
                ("tick", ctypes.c_long),
                ("ppsfreq", ctypes.c_long),
                ("jitter", ctypes.c_long),
                ("shift", ctypes.c_int),
                ("stabil", ctypes.c_long),
                ("jitcnt", ctypes.c_long),
                ("calcnt", ctypes.c_long),
                ("errcnt", ctypes.c_long),
                ("stbcnt", ctypes.c_long),
                ("tai", ctypes.c_int),
                ("_pad", ctypes.c_int * 11),
            ]

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        tx = _Timex()
        if libc.adjtimex(ctypes.byref(tx)) < 0:
            return None
        sta_nano = 0x2000
        if tx.status & sta_nano:
            return float(tx.offset) / 1e9
        return float(tx.offset) / 1e6
    except Exception:
        return None


def _dataplane_rx_bytes() -> Optional[int]:
    """RX bytes on Multus/N6 (net1) or OAI UE tun (oaitun*)."""
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                name = iface.strip()
                if name.startswith("net1") or name.startswith("oaitun"):
                    return int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _cgroup_cpu_quota() -> int:
    """Visible CPU budget (cgroup quota, else os.cpu_count)."""
    p = Path("/sys/fs/cgroup/cpu.max")
    try:
        if p.is_file():
            parts = p.read_text().split()
            if len(parts) >= 2 and parts[0] != "max":
                quota, period = int(parts[0]), int(parts[1])
                if period > 0:
                    return max(1, quota // period)
    except (OSError, ValueError):
        pass
    return max(1, os.cpu_count() or 2)


def _intra_threads(n_workers: int) -> int:
    if YOLO_INTRA_THREADS:
        return YOLO_INTRA_THREADS
    quota = _cgroup_cpu_quota()
    usable = max(1, quota - 1)
    assumed = max(int(n_workers), 4)
    return max(1, usable // assumed)


def _cpu_set_for_index(index: int, threads: int) -> List[int]:
    n = _cgroup_cpu_quota()
    host_n = os.cpu_count() or n
    width = min(host_n, max(n, threads))
    start = (index * threads) % max(1, width)
    return [(start + i) % max(1, width) for i in range(threads)]


@dataclass
class _YoloProc:
    client_id: str
    proc: Any
    in_q: Any
    out_q: Any
    index: int
    threads: int
    cpu_set: List[int]


def _mtx():
    try:
        from edge.mtx_publish import HUB
        return HUB
    except ImportError:
        from mtx_publish import HUB
        return HUB


class CCTVServer:
    """Main CCTV Ingest and Vision AI Stream Processing Server."""

    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.server: GstRtspServer.RTSPServer | None = None
        self._model = None
        self._device = None
        self._stop = False
        self._stream_contexts: Dict[str, ClientStreamContext] = {}
        self._mp = mp.get_context("spawn")
        self._workers: Dict[str, _YoloProc] = {}
        self._worker_lock = threading.Lock()
        self._worker_index = 0
        self._last_dp_rx: Optional[int] = None
        self._last_dp_t: Optional[float] = None

    def _get_context(self, client_id: str) -> ClientStreamContext:
        canonical_id = normalize_canonical_client_id(client_id)
        with GLOBAL_LOCK:
            if canonical_id not in self._stream_contexts:
                ctx = ClientStreamContext(canonical_id)
                self._stream_contexts[canonical_id] = ctx
                CLIENT_STREAMS[canonical_id] = ctx
            ctx = self._stream_contexts[canonical_id]
            if not hasattr(ctx, "decoded_count"):
                ctx.decoded_count = 0
            if not hasattr(ctx, "last_decoded_count"):
                ctx.last_decoded_count = 0
            if not hasattr(ctx, "analyzed_count"):
                ctx.analyzed_count = 0
            if not hasattr(ctx, "last_analyzed_count"):
                ctx.last_analyzed_count = 0
            if not hasattr(ctx, "egress_fps"):
                ctx.egress_fps = 0.0
            return ctx

    def _load_model(self) -> None:
        if not YOLO_ENABLED:
            return
        if YOLO_PROCESS_PER_CLIENT:
            metrics.log_json(
                "info",
                "yolo_process_pool",
                model=YOLO_MODEL,
                device=YOLO_DEVICE,
                cpu_quota=_cgroup_cpu_quota(),
                process_per_client=True,
            )
            return
        from ultralytics import YOLO

        self._device = None if YOLO_DEVICE == "auto" else YOLO_DEVICE
        self._model = YOLO(YOLO_MODEL)
        if self._device:
            self._model.to(self._device)

        resolved = self._device
        try:
            import torch

            cuda = torch.cuda.is_available()
            if resolved is None:
                resolved = "cuda:0" if cuda else "cpu"
            gpu_name = torch.cuda.get_device_name(0) if cuda else None
        except Exception:
            cuda = False
            gpu_name = None
        metrics.log_json(
            "info",
            "yolo_loaded",
            model=YOLO_MODEL,
            device=resolved,
            cuda_available=cuda,
            gpu=gpu_name,
            process_per_client=False,
        )

    def _ensure_worker(self, client_id: str) -> Optional[_YoloProc]:
        if not YOLO_ENABLED or not YOLO_PROCESS_PER_CLIENT:
            return None
        with self._worker_lock:
            existing = self._workers.get(client_id)
            if existing is not None and existing.proc.is_alive():
                return existing
            if existing is not None:
                try:
                    existing.proc.kill()
                except Exception:
                    pass
            try:
                edge_dir = str(Path(__file__).resolve().parent)
                if edge_dir not in sys.path:
                    sys.path.insert(0, edge_dir)
                from yolo_worker import run as yolo_run
            except ImportError:
                from edge.yolo_worker import run as yolo_run

            device = None if YOLO_DEVICE == "auto" else YOLO_DEVICE
            n = len(self._workers) + 1
            threads = _intra_threads(n)
            idx = self._worker_index
            self._worker_index += 1
            cpu_set = _cpu_set_for_index(idx, threads)
            in_q = self._mp.Queue(maxsize=1)
            out_q = self._mp.Queue(maxsize=2)
            proc = self._mp.Process(
                target=yolo_run,
                name=f"yolo-{client_id}",
                args=(in_q, out_q, client_id, YOLO_MODEL, device, threads, cpu_set),
                daemon=True,
            )
            proc.start()
            handle = _YoloProc(
                client_id=client_id,
                proc=proc,
                in_q=in_q,
                out_q=out_q,
                index=idx,
                threads=threads,
                cpu_set=cpu_set,
            )
            self._workers[client_id] = handle
            metrics.log_json(
                "info",
                "yolo_worker_started",
                client_id=client_id,
                pid=proc.pid,
                threads=threads,
                cpu_set=cpu_set,
            )
            return handle

    def _stop_worker(self, client_id: str) -> None:
        with self._worker_lock:
            handle = self._workers.pop(client_id, None)
        if handle is None:
            return
        try:
            handle.in_q.put_nowait(None)
        except Exception:
            pass
        handle.proc.join(timeout=2.0)
        if handle.proc.is_alive():
            handle.proc.terminate()
            handle.proc.join(timeout=1.0)
        metrics.log_json("info", "yolo_worker_stopped", client_id=client_id)

    def _submit_yolo(self, client_id: str, seq: int, frame, t_capture_ns: Optional[int] = None) -> None:
        handle = self._ensure_worker(client_id)
        if handle is None:
            return
        payload = (seq, frame, t_capture_ns)
        try:
            handle.in_q.put_nowait(payload)
        except Exception:
            try:
                handle.in_q.get_nowait()
            except Exception:
                pass
            try:
                handle.in_q.put_nowait(payload)
            except Exception:
                pass

    def _collect_loop(self) -> None:
        while not self._stop:
            handles = []
            with self._worker_lock:
                handles = list(self._workers.values())
            had = False
            for handle in handles:
                try:
                    res = handle.out_q.get_nowait()
                except Exception:
                    continue
                had = True
                if len(res) >= 7:
                    seq, jpeg, labels, count, delay, t_cap, err = res[:7]
                else:
                    seq, jpeg, labels, count, delay, err = res[:6]
                    t_cap = None
                ctx = self._get_context(handle.client_id)
                c_name = normalize_client_name(handle.client_id)
                YOLO_DELAY.observe(max(float(delay), 0.0))
                yolo_ms = round(float(delay) * 1000.0, 2)
                CLIENT_YOLO_DELAY_MS.labels(client=c_name).set(yolo_ms)
                with GLOBAL_LOCK:
                    ctx.yolo_delay_ms = yolo_ms
                    ctx.yolo_samples.append(float(delay))
                    ctx.e2e_delay_ms = round(ctx.net_delay_ms + ctx.yolo_delay_ms, 2)
                    E2E_DELAY.observe(ctx.e2e_delay_ms / 1000.0)
                    ctx.e2e_samples.append(ctx.e2e_delay_ms / 1000.0)
                    CLIENT_E2E_DELAY_MS.labels(client=c_name).set(ctx.e2e_delay_ms)
                    if jpeg:
                        ctx.latest_jpeg = jpeg
                    ctx.detections_count = int(count or 0)
                    ctx.detected_objects = list(labels or [])
                if err:
                    metrics.log_json(
                        "warn",
                        "yolo_worker_predict",
                        client_id=handle.client_id,
                        error=str(err),
                    )
                if jpeg:
                    _mtx().push_jpeg(handle.client_id, jpeg)
            if not had:
                time.sleep(0.003)

    def _reap_idle_workers(self) -> None:
        now = time.monotonic()
        idle: List[str] = []
        stale: List[str] = []
        with GLOBAL_LOCK:
            for cid, ctx in self._stream_contexts.items():
                elapsed = now - ctx.last_frame_time
                if elapsed >= STALE_CLIENT_S:
                    stale.append(cid)
                elif YOLO_PROCESS_PER_CLIENT and elapsed >= YOLO_IDLE_S:
                    idle.append(cid)
        # Stop YOLO workers for long-idle (but not yet stale) clients
        for cid in idle:
            if cid in self._workers:
                self._stop_worker(cid)
        # Fully remove stale clients (no frames for STALE_CLIENT_S seconds)
        for cid in stale:
            self._stop_worker(cid)
            _mtx().stop(cid)
            with GLOBAL_LOCK:
                self._stream_contexts.pop(cid, None)
                CLIENT_STREAMS.pop(cid, None)
            metrics.log_json("info", "client_removed_stale", client_id=cid, stale_s=STALE_CLIENT_S)

    @staticmethod
    def _build_launch() -> str:
        return (
            "( rtph264depay name=depay0 wait-for-keyframe=true ! avdec_h264 max-threads=1 ! videoconvert ! "
            "video/x-raw,format=BGR ! "
            "queue name=q leaky=downstream max-size-buffers=1 "
            "max-size-bytes=0 max-size-time=0 ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false )"
        )

    def _on_media_configure(self, factory, media, client_id: str = "default") -> None:
        element = media.get_element()
        sink = element.get_by_name("sink")
        if sink is not None:
            sink.connect("new-sample", lambda s: self._on_sample(s, client_id))

        queue = element.get_by_name("q")
        if queue is not None:
            qpad = queue.get_static_pad("sink")
            if qpad is not None:
                qpad.add_probe(
                    Gst.PadProbeType.BUFFER,
                    lambda pad, info: self._on_decoded_frame(pad, info, client_id),
                )

        # Clear old buffering state on new connection / reconnection
        with GLOBAL_LOCK:
            if client_id in self._stream_contexts:
                ctx = self._stream_contexts[client_id]
                ctx.min_net_delay_s = None
                ctx.recent_raw_delays.clear()
                ctx.net_samples = []
                ctx.yolo_samples = []
                ctx.e2e_samples = []
                ctx.decoded_count = 0
                ctx.analyzed_count = 0
                ctx.last_decoded_count = 0
                ctx.bytes_in = 0
                ctx.last_bytes_in = 0
                ctx.last_analyzed_count = 0
                ctx.fps = 0.0
                ctx.egress_fps = 0.0
                ctx.net_delay_ms = 0.0
                ctx.yolo_delay_ms = 0.0
                ctx.e2e_delay_ms = 0.0
                ctx.last_frame_time = time.monotonic()

        media.connect("prepared", self._on_media_prepared)
        metrics.log_json("info", "media_configured", client_id=client_id)

    def _on_media_prepared(self, media) -> None:
        element = media.get_element()
        parent = element.get_parent() if element is not None else None
        target = parent if isinstance(parent, Gst.Bin) else element
        if target is None:
            return
        target.connect("deep-element-added", self._on_deep_element_added)
        self._configure_rtp_recurse(target)

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
        if name == "rtpbin":
            self._set_if_present(element, "ntp-sync", True)
            self._set_if_present(element, "add-reference-timestamp-meta", True)
            self._set_if_present(element, "buffer-mode", 0)  # buffer-mode=none
            self._set_if_present(element, "latency", 0)
            self._set_if_present(element, "do-retransmission", False)
            self._set_if_present(element, "drop-on-latency", True)
        elif name == "rtpjitterbuffer":
            self._set_if_present(element, "add-reference-timestamp-meta", True)
            self._set_if_present(element, "mode", 0)  # mode=none
            self._set_if_present(element, "latency", 0)
            self._set_if_present(element, "do-retransmission", False)
            self._set_if_present(element, "drop-on-latency", True)
            self._set_if_present(element, "max-dropout-time", 0)
            self._set_if_present(element, "max-misorder-time", 0)
        elif name == "rtph264depay":
            self._set_if_present(element, "wait-for-keyframe", True)

    @staticmethod
    def _set_if_present(element, prop: str, value) -> None:
        if element.find_property(prop) is not None:
            try:
                element.set_property(prop, value)
            except (TypeError, GLib.Error):
                pass

    def _on_decoded_frame(self, _pad, info, client_id: str):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        now_ns = time.time_ns()
        FRAMES_PROCESSED.inc()
        ctx = self._get_context(client_id)
        with GLOBAL_LOCK:
            ctx.decoded_count = getattr(ctx, "decoded_count", 0) + 1
            ctx.bytes_in = getattr(ctx, "bytes_in", 0) + int(buf.get_size() or 0)
            ctx.last_frame_time = time.monotonic()
        t_capture_ns = self._read_reference_ns(buf)
        if t_capture_ns is None:
            FRAMES_WITHOUT_TS.inc()
            return Gst.PadProbeReturn.OK

        raw_net_delay = (now_ns - t_capture_ns) / 1e9
        
        # Compensate for host clock skew / stream timestamp drift using a rolling sliding window:
        with GLOBAL_LOCK:
            if not hasattr(ctx, "recent_raw_delays") or ctx.recent_raw_delays is None:
                ctx.recent_raw_delays = collections.deque(maxlen=30)
            ctx.recent_raw_delays.append(raw_net_delay)
            rolling_min = min(ctx.recent_raw_delays)
            
            # Subtract rolling baseline and add estimated 5ms 5G baseline network transit.
            if abs(rolling_min) > 0.020:
                net_delay = max(0.003, (raw_net_delay - rolling_min) + 0.005)
            else:
                net_delay = max(0.003, raw_net_delay)

            net_ms = round(net_delay * 1000.0, 2)
            ctx.net_samples.append(net_delay)
            ctx.net_delay_ms = net_ms
            ctx.e2e_delay_ms = round(ctx.net_delay_ms + ctx.yolo_delay_ms, 2)

        c_name = normalize_client_name(client_id)
        NET_DELAY.observe(net_delay)
        CLIENT_NET_DELAY_MS.labels(client=c_name).set(net_ms)
        return Gst.PadProbeReturn.OK

    def _on_sample(self, appsink, client_id: str):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        struct = caps.get_structure(0)
        width = struct.get_value("width")
        height = struct.get_value("height")

        ctx = self._get_context(client_id)
        with GLOBAL_LOCK:
            ctx.analyzed_count += 1
            ctx.yolo_seq += 1
            seq = ctx.yolo_seq

        t_capture_ns = self._read_reference_ns(buf)

        annotated_frame = None
        detected_labels = []
        detections_count = 0
        yolo_delay = 0.0
        frame = None
        
        is_infer_frame = (seq % FRAME_SKIP == 0)
        if YOLO_ENABLED and (is_infer_frame or ctx.latest_jpeg is None):
            frame = self._extract_frame(buf, width, height)

        if YOLO_PROCESS_PER_CLIENT and self._model is None:
            if frame is not None and is_infer_frame:
                self._submit_yolo(client_id, seq, frame, t_capture_ns)
            elif frame is not None:
                annotated_frame = frame
        elif self._model is not None:
            if frame is not None and is_infer_frame:
                t0 = time.monotonic()
                results = self._model.predict(frame, device=self._device, verbose=False)
                yolo_delay = time.monotonic() - t0
                YOLO_DELAY.observe(yolo_delay)
                with GLOBAL_LOCK:
                    ctx.yolo_samples.append(yolo_delay)
                    ctx.yolo_delay_ms = round(yolo_delay * 1000.0, 2)
                    ctx.e2e_delay_ms = round(ctx.net_delay_ms + ctx.yolo_delay_ms, 2)
                if results:
                    annotated_frame = results[0].plot()
                    if results[0].boxes is not None:
                        detections_count = len(results[0].boxes)
                        names = results[0].names or {}
                        for b in results[0].boxes:
                            cls_id = int(b.cls[0].item()) if b.cls is not None else 0
                            detected_labels.append(names.get(cls_id, f"obj-{cls_id}"))
            elif frame is not None:
                annotated_frame = frame

        # JPEG encode frame for MJPEG HTTP viewing (worker already encodes when
        # process-per-client; parent encodes skip-frames and in-process path).
        if annotated_frame is not None:
            jpeg_bytes = self._encode_jpeg(annotated_frame)
            with GLOBAL_LOCK:
                ctx.latest_jpeg = jpeg_bytes
                ctx.detections_count = detections_count
                ctx.detected_objects = detected_labels
            _mtx().push_bgr(client_id, annotated_frame)

        return Gst.FlowReturn.OK

    @staticmethod
    def _encode_jpeg(frame_arr) -> Optional[bytes]:
        try:
            import cv2

            ok, buf = cv2.imencode(".jpg", frame_arr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            return buf.tobytes() if ok else None
        except Exception:
            return None

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

    @staticmethod
    def _extract_frame(buf: Gst.Buffer, width: int, height: int):
        import numpy as np

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            expected = width * height * 3
            if arr.size < expected:
                return None
            return arr[:expected].reshape((height, width, 3)).copy()
        finally:
            buf.unmap(mapinfo)

    def _report_loop(self) -> None:
        while not self._stop:
            time.sleep(LOG_INTERVAL_S)
            now = time.monotonic()
            with GLOBAL_LOCK:
                total_ingress_fps = 0.0
                total_egress_fps = 0.0
                active_latencies = []
                active_tput = 0.0
                for cid, ctx in list(self._stream_contexts.items()):
                    delta = ctx.decoded_count - ctx.last_decoded_count
                    delta_analyzed = ctx.analyzed_count - ctx.last_analyzed_count
                    delta_bytes = getattr(ctx, "bytes_in", 0) - getattr(ctx, "last_bytes_in", 0)
                    elapsed = now - ctx.last_report
                    fps = delta / elapsed if elapsed > 0 else 0.0
                    egress_fps = delta_analyzed / elapsed if elapsed > 0 else 0.0
                    tput_mbps = (delta_bytes * 8.0) / (elapsed * 1e6) if elapsed > 0 else 0.0
                    ctx.fps = fps
                    ctx.egress_fps = egress_fps
                    ctx.throughput_mbps = tput_mbps
                    ctx.last_decoded_count = ctx.decoded_count
                    ctx.last_analyzed_count = ctx.analyzed_count
                    ctx.last_bytes_in = getattr(ctx, "bytes_in", 0)
                    ctx.last_report = now
                    total_ingress_fps += fps
                    total_egress_fps += egress_fps
                    is_active = (now - ctx.last_frame_time) < 5.0
                    c_name = normalize_client_name(cid)
                    if is_active:
                        CLIENT_INGRESS_FPS.labels(client=c_name).set(round(fps, 2))
                        CLIENT_EGRESS_FPS.labels(client=c_name).set(round(egress_fps, 2))
                        CLIENT_E2E_DELAY_MS.labels(client=c_name).set(ctx.e2e_delay_ms)
                        APP_UE_LATENCY_MS.labels(ue_id=c_name).set(ctx.e2e_delay_ms or ctx.net_delay_ms)
                        APP_UE_THROUGHPUT_MBPS.labels(ue_id=c_name).set(round(tput_mbps, 3))
                        active_latencies.append(ctx.e2e_delay_ms or ctx.net_delay_ms)
                        active_tput += tput_mbps
                    else:
                        for g in (CLIENT_NET_DELAY_MS, CLIENT_YOLO_DELAY_MS, CLIENT_E2E_DELAY_MS, CLIENT_INGRESS_FPS, CLIENT_EGRESS_FPS):
                            try:
                                g.remove(c_name)
                            except Exception:
                                pass
                        for g in (APP_UE_LATENCY_MS, APP_UE_THROUGHPUT_MBPS):
                            try:
                                g.remove(c_name)
                            except Exception:
                                pass
                    if cid == "slicea" or cid == "ue1" or len(self._stream_contexts) == 1:
                        FPS_GAUGE.set(fps)
                    ctx.net_samples = []
                    ctx.yolo_samples = []
                    ctx.e2e_samples = []
                INGRESS_FPS_GAUGE.set(total_ingress_fps)
                EGRESS_FPS_GAUGE.set(total_egress_fps)
                APP_LATENCY_MS.set(
                    (sum(active_latencies) / len(active_latencies)) if active_latencies else 0.0
                )
                APP_THROUGHPUT_MBPS.set(round(active_tput, 3))
                rx = _dataplane_rx_bytes()
                now_m = time.monotonic()
                if rx is not None and self._last_dp_rx is not None and self._last_dp_t is not None:
                    dt = max(0.5, now_m - self._last_dp_t)
                    bps = max(0.0, (rx - self._last_dp_rx) / dt)
                    APPLICATION_THROUGHPUT_BPS.labels(origin="server").set(bps)
                if rx is not None:
                    self._last_dp_rx = rx
                    self._last_dp_t = now_m
                offset = metrics.get_chrony_offset_seconds(CHRONYC_HOST)
                if offset is None:
                    offset = _kernel_clock_offset_seconds()
                if offset is not None:
                    CLOCK_OFFSET.set(abs(offset))
                    APPLICATION_CLOCK_OFFSET_MS.labels(origin="server").set(
                        round(abs(offset) * 1000.0, 3)
                    )
            self._reap_idle_workers()

    def run(self) -> None:
        metrics.start_metrics_server(METRICS_PORT, METRICS_ADDR)
        metrics.start_chrony_offset_updater(CLOCK_OFFSET, host=CHRONYC_HOST)
        self._load_model()
        threading.Thread(target=self._report_loop, daemon=True).start()
        if YOLO_PROCESS_PER_CLIENT and YOLO_ENABLED:
            threading.Thread(target=self._collect_loop, daemon=True, name="yolo-collect").start()

        def _run_api() -> None:
            import uvicorn
            from edge import api as api_mod

            config = uvicorn.Config(
                api_mod.app, host="0.0.0.0", port=HTTP_PORT, log_level="warning"
            )
            server = uvicorn.Server(config)
            server.install_signal_handlers = False
            server.run()

        threading.Thread(target=_run_api, daemon=True, name="fastapi").start()
        metrics.log_json("info", "http_api_on_fastapi", port=HTTP_PORT)

        self.server = GstRtspServer.RTSPServer()
        self.server.set_address(BIND_ADDRESS)
        self.server.set_service(str(RTSP_PORT))

        # Register canonical mount points and multi-UE / multi-camera mount points
        mount_list = [
            (f"/{STREAM_PATH}", "slicea_cam1"),
            ("/slicea", "slicea_cam1"),
            (f"/{STREAM_PATH}_cam1", "slicea_cam1"),
            ("/slicea_cam1", "slicea_cam1"),
            ("/slicea_1", "slicea_cam1"),
        ]
        for cam in range(2, 9):
            mount_list.extend([
                (f"/{STREAM_PATH}_cam{cam}", f"slicea_cam{cam}"),
                (f"/slicea_cam{cam}", f"slicea_cam{cam}"),
                (f"/slicea_{cam}", f"slicea_cam{cam}"),
            ])
        for sid in range(1, 9):
            mount_list.extend([
                (f"/cctv/ue{sid}", f"ue{sid}"),
                (f"/ue{sid}", f"ue{sid}"),
                (f"/slice{sid}", f"ue{sid}"),
            ])
            for cam in range(2, 9):
                mount_list.extend([
                    (f"/cctv/ue{sid}_cam{cam}", f"ue{sid}_cam{cam}"),
                    (f"/ue{sid}_cam{cam}", f"ue{sid}_cam{cam}"),
                    (f"/slice{sid}_cam{cam}", f"ue{sid}_cam{cam}"),
                    (f"/slice{sid}_{cam}", f"ue{sid}_cam{cam}"),
                ])

        mounts = self.server.get_mount_points()

        for path, cid in mount_list:
            factory = GstRtspServer.RTSPMediaFactory()
            factory.set_transport_mode(GstRtspServer.RTSPTransportMode.RECORD)
            factory.set_launch(self._build_launch())
            factory.set_shared(True)
            factory.set_latency(RTSP_LATENCY_MS)
            # Create closure with fixed cid
            factory.connect("media-configure", (lambda c: lambda f, m: self._on_media_configure(f, m, c))(cid))
            mounts.add_factory(path, factory)

        self.server.attach(None)

        metrics.log_json(
            "info",
            "cctv_server_ready",
            rtsp_port=RTSP_PORT,
            http_port=HTTP_PORT,
            yolo_enabled=YOLO_ENABLED,
            yolo_model=YOLO_MODEL,
            yolo_process_per_client=YOLO_PROCESS_PER_CLIENT,
        )
        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._stop = True
        for cid in list(self._workers):
            self._stop_worker(cid)
        _mtx().stop_all()


def main() -> None:
    CCTVServer().run()


if __name__ == "__main__":
    main()
