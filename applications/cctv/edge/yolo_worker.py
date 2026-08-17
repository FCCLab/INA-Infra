"""Per-client YOLO process (no GStreamer). Spawned by the CCTV analyzer.

Each CCTV camera gets its own process + model so inference can run in
parallel across CPU cores (avoids the GIL and a single shared YOLO).
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional, Sequence


def _limit_threads(n: int) -> None:
    n = max(1, int(n))
    os.environ["OMP_NUM_THREADS"] = str(n)
    os.environ["MKL_NUM_THREADS"] = str(n)
    os.environ["OPENBLAS_NUM_THREADS"] = str(n)
    os.environ["NUMEXPR_NUM_THREADS"] = str(n)
    os.environ["ULTRALYTICS_OFFLINE"] = "1"


def _pin_cpus(cpu_set: Optional[Sequence[int]]) -> None:
    if not cpu_set:
        return
    try:
        os.sched_setaffinity(0, set(int(c) for c in cpu_set))
    except (AttributeError, OSError, ValueError):
        pass


def _encode_jpeg(frame_arr: Any) -> Optional[bytes]:
    try:
        import cv2

        ok, buf = cv2.imencode(".jpg", frame_arr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def run(
    in_q: Any,
    out_q: Any,
    client_id: str,
    model_name: str,
    device: Optional[str],
    threads: int,
    cpu_set: Optional[Sequence[int]] = None,
) -> None:
    _limit_threads(threads)
    _pin_cpus(cpu_set)
    try:
        import torch

        torch.set_num_threads(max(1, int(threads)))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
    except Exception:
        torch = None  # type: ignore[assignment]

    from ultralytics import YOLO

    model = YOLO(model_name)
    resolved = device
    if resolved:
        try:
            model.to(resolved)
        except Exception:
            resolved = None
    if resolved is None and torch is not None:
        resolved = "cuda:0" if torch.cuda.is_available() else "cpu"

    while True:
        item = in_q.get()
        if item is None:
            break
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            seq, frame, t_capture_ns = item[0], item[1], item[2]
        else:
            seq, frame = item
            t_capture_ns = None
        labels = []
        count = 0
        annotated = frame
        t0 = time.monotonic()
        try:
            results = model.predict(
                frame,
                device=resolved,
                verbose=False,
                workers=0,
            )
            delay = time.monotonic() - t0
            if results:
                annotated = results[0].plot()
                if results[0].boxes is not None:
                    count = len(results[0].boxes)
                    names = results[0].names or {}
                    for b in results[0].boxes:
                        cls_id = int(b.cls[0].item()) if b.cls is not None else 0
                        labels.append(names.get(cls_id, f"obj-{cls_id}"))
        except Exception as exc:
            delay = time.monotonic() - t0
            try:
                out_q.put((seq, None, [], 0, delay, t_capture_ns, str(exc)))
            except Exception:
                pass
            continue
        jpeg = _encode_jpeg(annotated)
        try:
            # Drop if parent is behind: keep the newest result only.
            while True:
                try:
                    out_q.get_nowait()
                except Exception:
                    break
            out_q.put((seq, jpeg, labels, count, delay, t_capture_ns, None))
        except Exception:
            pass
