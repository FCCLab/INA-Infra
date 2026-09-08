#!/usr/bin/env python3
"""Decode annotated RTSP and print t_recv wall clock per frame (system python + GI).

Polls appsink with try-pull-sample. GObject signals need a GLib main loop and
never fire in this process, which left Grafana empty while HLS still played.
"""

from __future__ import annotations

import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: e2e_rtsp_probe.py rtsp://host/path", file=sys.stderr)
        return 2
    url = sys.argv[1]
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    desc = (
        f'uridecodebin uri="{url}" '
        "! videoconvert ! video/x-raw "
        "! appsink name=sink emit-signals=false max-buffers=1 drop=true sync=false"
    )
    pipeline = Gst.parse_launch(desc)
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    timeout = 500 * Gst.MSECOND
    while True:
        sample = sink.emit("try-pull-sample", timeout)
        if sample is not None:
            sys.stdout.write(f"t_recv {time.time():.6f}\n")
            sys.stdout.flush()
        msg = bus.timed_pop_filtered(
            0,
            Gst.MessageType.ERROR | Gst.MessageType.EOS,
        )
        if msg is None:
            continue
        pipeline.set_state(Gst.State.NULL)
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"error {err} {dbg}", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
