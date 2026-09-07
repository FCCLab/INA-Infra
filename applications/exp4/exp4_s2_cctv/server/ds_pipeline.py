#!/usr/bin/env python3
"""DeepStream multi-stream with one dedicated YOLO engine per camera.

Each stream is an independent graph (no shared nvstreammux batch):
  raw/cam{i} → mux(batch=1) → nvinfer → nvosd → encode → annotated/cam{i}

This avoids cross-stream label mixing from batched stream_id demux.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List, Optional

LOG = logging.getLogger("ds_pipeline")

DS_NUM_STREAMS = max(1, int(os.environ.get("DS_NUM_STREAMS", "4")))
MTX_RTSP = os.environ.get("MTX_RTSP_URL", "rtsp://127.0.0.1:8555").rstrip("/")
DS_WIDTH = int(os.environ.get("DS_WIDTH", "1280"))
DS_HEIGHT = int(os.environ.get("DS_HEIGHT", "720"))
DS_BITRATE_KBPS = int(os.environ.get("DS_PUBLISH_BITRATE_KBPS", "4000"))
DS_WAIT_RAW_S = float(os.environ.get("DS_WAIT_RAW_S", "15"))
DS_PGIE_CONFIG_DIR = os.environ.get("DS_PGIE_CONFIG_DIR", "/tmp/ds_pgie")


def _raw_uri(i: int) -> str:
    return f"{MTX_RTSP}/raw/cam{i}"


def _annotated_uri(i: int) -> str:
    return f"{MTX_RTSP}/annotated/cam{i}"


def _pgie_config_for(stream_idx: int) -> str:
    """1-based stream index → per-stream PGIE config (batch-size=1)."""
    path = os.path.join(DS_PGIE_CONFIG_DIR, f"config_infer_yoloV8_s{stream_idx}.txt")
    if os.path.isfile(path):
        return path
    # Fallback: single shared config if dedicated files were not prepared.
    shared = os.environ.get("DS_PGIE_CONFIG", "")
    if shared and os.path.isfile(shared):
        return shared
    raise RuntimeError(f"PGIE config missing for stream {stream_idx}: {path}")


def _wait_for_raw(num: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
        if time.monotonic() >= deadline - 0.01:
            break
    LOG.info("proceeding to connect %d dedicated YOLO streams (waited up to %.1fs)", num, timeout_s)


def _make_encoder(Gst, i: int):
    capsfilter = Gst.ElementFactory.make("capsfilter", f"caps-{i}")
    enc = None
    enc_name = ""
    for factory, nvmm in (
        ("nvv4l2h264enc", True),
        ("x264enc", False),
        ("avenc_h264", False),
    ):
        cand = Gst.ElementFactory.make(factory, f"enc-{i}")
        if cand is None:
            continue
        enc = cand
        enc_name = factory
        if factory == "x264enc":
            enc.set_property("tune", "zerolatency")
            enc.set_property("speed-preset", "veryfast")
            enc.set_property("bitrate", DS_BITRATE_KBPS)
            enc.set_property("key-int-max", 30)
        elif factory == "avenc_h264":
            try:
                enc.set_property("bitrate", DS_BITRATE_KBPS * 1000)
            except Exception:  # noqa: BLE001
                pass
        elif factory == "nvv4l2h264enc":
            try:
                enc.set_property("bitrate", DS_BITRATE_KBPS * 1000)
                enc.set_property("preset-id", 1)
                enc.set_property("insert-sps-pps", 1)
                enc.set_property("iframeinterval", 30)
            except Exception:  # noqa: BLE001
                pass
        if nvmm:
            capsfilter.set_property(
                "caps",
                Gst.Caps.from_string("video/x-raw(memory:NVMM),format=I420"),
            )
        else:
            capsfilter.set_property(
                "caps", Gst.Caps.from_string("video/x-raw,format=I420")
            )
        break
    if enc is None:
        raise RuntimeError(
            "no H264 encoder (need nvv4l2h264enc / gstreamer1.0-plugins-ugly / libav)"
        )
    return capsfilter, enc, enc_name


def _add_dedicated_branch(pipeline, Gst, i: int) -> None:
    """Stream index i is 0-based; MediaMTX paths use cam{i+1}."""
    cam = i + 1
    uri = _raw_uri(cam)
    pgie_cfg = _pgie_config_for(cam)

    streammux = Gst.ElementFactory.make("nvstreammux", f"mux-{i}")
    if not streammux:
        raise RuntimeError("nvstreammux missing — is this a DeepStream image?")
    streammux.set_property("batch-size", 1)
    streammux.set_property("width", DS_WIDTH)
    streammux.set_property("height", DS_HEIGHT)
    streammux.set_property("batched-push-timeout", 40000)
    streammux.set_property("live-source", 1)

    src = Gst.ElementFactory.make("nvurisrcbin", f"src-{i}")
    if src is not None:
        src.set_property("uri", uri)
        try:
            src.set_property("rtsp-reconnect-interval", 5)
        except Exception:  # noqa: BLE001
            pass
    else:
        src = Gst.ElementFactory.make("uridecodebin", f"src-{i}")
        if not src:
            raise RuntimeError("neither nvurisrcbin nor uridecodebin available")
        src.set_property("uri", uri)

    pgie = Gst.ElementFactory.make("nvinfer", f"pgie-{i}")
    if not pgie:
        raise RuntimeError("nvinfer missing")
    pgie.set_property("config-file-path", pgie_cfg)

    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"convertor-{i}")
    nvosd = Gst.ElementFactory.make("nvdsosd", f"osd-{i}")
    queue = Gst.ElementFactory.make("queue", f"queue-{i}")
    conv = Gst.ElementFactory.make("nvvideoconvert", f"conv-out-{i}")
    capsfilter, enc, enc_name = _make_encoder(Gst, i)
    parse = Gst.ElementFactory.make("h264parse", f"parse-{i}")
    sink = Gst.ElementFactory.make("rtspclientsink", f"sink-{i}")
    if not all((nvvidconv, nvosd, queue, conv, parse, sink)):
        raise RuntimeError(f"failed to create dedicated branch {cam}")

    sink.set_property("location", _annotated_uri(cam))
    sink.set_property("protocols", "tcp")
    try:
        sink.set_property("latency", 0)
    except Exception:  # noqa: BLE001
        pass

    for el in (
        src,
        streammux,
        pgie,
        nvvidconv,
        nvosd,
        queue,
        conv,
        capsfilter,
        enc,
        parse,
        sink,
    ):
        pipeline.add(el)

    def _on_pad_added(element, pad, mux=streammux):
        caps = pad.get_current_caps() or pad.query_caps(None)
        name = caps.to_string() if caps else ""
        if caps and caps.get_structure(0):
            st = caps.get_structure(0).get_name()
            if st and not st.startswith("video") and "video" not in name:
                return
        sink_pad = mux.get_request_pad("sink_0")
        if sink_pad and not sink_pad.is_linked():
            pad.link(sink_pad)

    src.connect("pad-added", _on_pad_added)
    try:
        src_pad = src.get_static_pad("vsrc_0") or src.get_static_pad("src")
        if src_pad is not None:
            sink_pad = streammux.get_request_pad("sink_0")
            if sink_pad and not sink_pad.is_linked():
                src_pad.link(sink_pad)
    except Exception:  # noqa: BLE001
        pass

    streammux.link(pgie)
    pgie.link(nvvidconv)
    nvvidconv.link(nvosd)
    nvosd.link(queue)
    queue.link(conv)
    conv.link(capsfilter)
    capsfilter.link(enc)
    enc.link(parse)
    parse.link(sink)

    LOG.info(
        "dedicated YOLO cam%d pgie=%s enc=%s -> %s",
        cam,
        pgie_cfg,
        enc_name,
        _annotated_uri(cam),
    )


def build_pipeline(num: int):
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402

    Gst.init(None)

    pipeline = Gst.Pipeline.new("ds-yolo-dedicated")
    for i in range(num):
        _add_dedicated_branch(pipeline, Gst, i)
    return pipeline, [], []


def run(num: Optional[int] = None) -> int:
    n = num if num is not None else DS_NUM_STREAMS
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    _wait_for_raw(n, DS_WAIT_RAW_S)
    LOG.info(
        "starting DeepStream DEDICATED N=%d (1 YOLO engine/stream) raw=%s/raw/cam* annotated=%s/annotated/cam*",
        n,
        MTX_RTSP,
        MTX_RTSP,
    )

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst  # noqa: E402

    pipeline, _sources, _branches = build_pipeline(n)
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def _on_message(_bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            LOG.warning("EOS — restarting pipeline")
            pipeline.set_state(Gst.State.NULL)
            pipeline.set_state(Gst.State.PLAYING)
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            LOG.error("pipeline error: %s (%s)", err, debug)
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            LOG.warning("pipeline warn: %s (%s)", err, debug)
        return True

    bus.connect("message", _on_message)
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        LOG.error("failed to set PLAYING")
        return 1
    LOG.info("DeepStream dedicated pipeline PLAYING (N=%d)", n)
    try:
        loop.run()
    except KeyboardInterrupt:
        LOG.info("interrupt")
    finally:
        pipeline.set_state(Gst.State.NULL)
    return 0


if __name__ == "__main__":
    sys.exit(run())
