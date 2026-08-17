"""Publish annotated camera frames to MediaMTX (RTSP publisher → HLS/WebRTC subscribers)."""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from common import metrics  # noqa: E402

try:
    from edge.state import GLOBAL_LOCK, CLIENT_STREAMS, mtx_path_for
except ImportError:
    from state import GLOBAL_LOCK, CLIENT_STREAMS, mtx_path_for


MTX_RTSP = os.environ.get("MTX_RTSP_URL", "rtsp://127.0.0.1:8555")
BITRATE_KBPS = int(os.environ.get("MTX_PUBLISH_BITRATE_KBPS", "2000"))


class _Publisher:
    def __init__(self, client_id: str, width: int, height: int) -> None:
        self.client_id = client_id
        self.path = mtx_path_for(client_id)
        self.width = int(width)
        self.height = int(height)
        self._lock = threading.Lock()
        location = f"{MTX_RTSP.rstrip('/')}/{self.path}"
        launch = (
            "appsrc name=src is-live=true do-timestamp=true format=time "
            f"caps=video/x-raw,format=BGR,width={self.width},height={self.height},framerate=25/1 "
            "! videoconvert ! video/x-raw,format=I420 "
            f"! x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 "
            f"bitrate={BITRATE_KBPS} bframes=0 "
            "! video/x-h264,profile=baseline "
            f"! rtspclientsink name=sink protocols=tcp location={location} latency=0"
        )
        self.pipeline = Gst.parse_launch(launch)
        self.appsrc = self.pipeline.get_by_name("src")
        self.appsrc.set_property("format", Gst.Format.TIME)
        self.appsrc.set_property("block", False)
        self.appsrc.set_property("max-bytes", 2_000_000)
        self._t0 = time.monotonic()
        self._n = 0
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f"MediaMTX publish pipeline failed for {self.path}")
        metrics.log_json(
            "info",
            "mtx_publisher_started",
            client_id=client_id,
            path=self.path,
            location=location,
            width=self.width,
            height=self.height,
        )

    def push_bgr(self, frame) -> None:
        import numpy as np

        arr = np.ascontiguousarray(frame)
        if arr.ndim != 3:
            return
        h, w = int(arr.shape[0]), int(arr.shape[1])
        if w != self.width or h != self.height:
            return
        buf = Gst.Buffer.new_wrapped(arr.tobytes())
        pts = int((time.monotonic() - self._t0) * 1e9)
        buf.pts = pts
        buf.dts = pts
        buf.duration = int(1e9 / 25)
        with self._lock:
            ret = self.appsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            metrics.log_json(
                "warn",
                "mtx_publisher_push_failed",
                client_id=self.client_id,
                path=self.path,
                flow=str(ret),
            )

    push_frame = push_bgr

    def stop(self) -> None:
        try:
            self.appsrc.emit("end-of-stream")
        except Exception:
            pass
        self.pipeline.set_state(Gst.State.NULL)
        metrics.log_json("info", "mtx_publisher_stopped", client_id=self.client_id, path=self.path)


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pubs: Dict[str, _Publisher] = {}

    def push_bgr(self, client_id: str, frame) -> None:
        if frame is None:
            return
        h, w = int(frame.shape[0]), int(frame.shape[1])
        with self._lock:
            pub = self._pubs.get(client_id)
            if pub is None or pub.width != w or pub.height != h:
                if pub is not None:
                    pub.stop()
                try:
                    pub = _Publisher(client_id, w, h)
                except Exception as exc:
                    metrics.log_json("warn", "mtx_publisher_start_failed", client_id=client_id, error=str(exc))
                    return
                self._pubs[client_id] = pub
                with GLOBAL_LOCK:
                    ctx = CLIENT_STREAMS.get(client_id)
                    if ctx is not None:
                        ctx.mtx_publishing = True
                        ctx.mtx_path = pub.path
            pub.push_bgr(frame)

    push_frame = push_bgr

    def push_jpeg(self, client_id: str, jpeg: Optional[bytes]) -> None:
        if not jpeg:
            return
        try:
            import cv2
            import numpy as np

            arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                return
        except Exception:
            return
        self.push_bgr(client_id, arr)

    def stop(self, client_id: str) -> None:
        with self._lock:
            pub = self._pubs.pop(client_id, None)
        if pub is not None:
            pub.stop()
        with GLOBAL_LOCK:
            ctx = CLIENT_STREAMS.get(client_id)
            if ctx is not None:
                ctx.mtx_publishing = False

    def stop_all(self) -> None:
        with self._lock:
            pubs = list(self._pubs.items())
            self._pubs.clear()
        for cid, pub in pubs:
            pub.stop()
            with GLOBAL_LOCK:
                ctx = CLIENT_STREAMS.get(cid)
                if ctx is not None:
                    ctx.mtx_publishing = False


HUB = Hub()
