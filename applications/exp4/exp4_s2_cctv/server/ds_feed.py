#!/usr/bin/env python3
"""Loop sample MP4s into MediaMTX raw/cam{1..N} (ffmpeg RTSP publish)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

LOG = logging.getLogger("ds_feed")

DS_NUM_STREAMS = max(1, int(os.environ.get("DS_NUM_STREAMS", "4")))
MTX_RTSP = os.environ.get("MTX_RTSP_URL", "rtsp://127.0.0.1:8555").rstrip("/")
VIDEO_DIR = Path(os.environ.get("DS_VIDEO_DIR", "/data"))
BITRATE_KBPS = int(os.environ.get("DS_FEED_BITRATE_KBPS", "4000"))
WIDTH = int(os.environ.get("DS_WIDTH", "1280"))
HEIGHT = int(os.environ.get("DS_HEIGHT", "720"))
FPS = int(os.environ.get("DS_FPS", "25"))

SAMPLE_CLIPS = [
    (
        "classroom.mp4",
        "https://github.com/intel-iot-devkit/sample-videos/raw/master/classroom.mp4",
    ),
    (
        "street.mp4",
        "https://github.com/intel-iot-devkit/sample-videos/raw/master/person-bicycle-car-detection.mp4",
    ),
    (
        "walking.mp4",
        "https://github.com/intel-iot-devkit/sample-videos/raw/master/face-demographics-walking.mp4",
    ),
    (
        "car-detection.mp4",
        "https://github.com/intel-iot-devkit/sample-videos/raw/master/car-detection.mp4",
    ),
]


def _ensure_clip(name: str, url: str) -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    path = VIDEO_DIR / name
    if path.is_file() and path.stat().st_size > 10_000:
        return path
    LOG.info("downloading %s", url)
    try:
        urllib.request.urlretrieve(url, path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("download failed %s: %s", name, exc)
    return path


def _clip_for_index(i: int) -> Optional[Path]:
    name, url = SAMPLE_CLIPS[(i - 1) % len(SAMPLE_CLIPS)]
    path = _ensure_clip(name, url)
    if path.is_file() and path.stat().st_size > 10_000:
        return path
    return None


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffmpeg_env() -> dict:
    """DeepStream sets LD_LIBRARY_PATH that breaks system ffmpeg (libavcodec)."""
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("GST_PLUGIN_PATH", None)
    return env


def _cmd_file(path: Path, dest: str) -> List[str]:
    # Re-encode H264 for MediaMTX publisher.
    return [
        _ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"scale={WIDTH}:{HEIGHT}",
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-b:v",
        f"{BITRATE_KBPS}k",
        "-g",
        str(FPS * 2),
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        dest,
    ]


def _cmd_testsrc(dest: str, pattern: int) -> List[str]:
    # lavfi testsrc as fallback when clips are missing.
    return [
        _ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={WIDTH}x{HEIGHT}:rate={FPS}:decimals={pattern}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-b:v",
        f"{BITRATE_KBPS}k",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        dest,
    ]


def _run_feeder(index: int, stop: threading.Event) -> None:
    dest = f"{MTX_RTSP}/raw/cam{index}"
    while not stop.is_set():
        clip = _clip_for_index(index)
        if clip is not None:
            cmd = _cmd_file(clip, dest)
            LOG.info("feed cam%d file=%s -> %s", index, clip, dest)
        else:
            cmd = _cmd_testsrc(dest, pattern=(index % 8))
            LOG.info("feed cam%d testsrc -> %s", index, dest)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_ffmpeg_env(),
            )
        except FileNotFoundError:
            LOG.error("ffmpeg not found in PATH")
            stop.wait(5.0)
            continue
        while not stop.is_set():
            rc = proc.poll()
            if rc is not None:
                err = b""
                try:
                    err = proc.stderr.read() if proc.stderr else b""
                except Exception:  # noqa: BLE001
                    pass
                LOG.warning(
                    "feeder cam%d exited rc=%s stderr=%s",
                    index,
                    rc,
                    (err or b"")[:400].decode("utf-8", "replace"),
                )
                break
            stop.wait(1.0)
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        if not stop.is_set():
            time.sleep(2.0)


def start_feeders(num: Optional[int] = None) -> tuple[threading.Event, list[threading.Thread]]:
    n = num if num is not None else DS_NUM_STREAMS
    stop = threading.Event()
    threads: list[threading.Thread] = []
    for i in range(1, n + 1):
        t = threading.Thread(target=_run_feeder, args=(i, stop), name=f"ds-feed-{i}", daemon=True)
        t.start()
        threads.append(t)
    LOG.info("started %d MediaMTX raw feeders (ffmpeg-v2)", n)
    return stop, threads


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    stop, _threads = start_feeders()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
