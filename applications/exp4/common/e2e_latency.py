"""Application E2E one-way delay: t_recv_client - t_send (stamped before app work).

The latest sample is written to EXP4_E2E_LATENCY_FILE for influx_publish.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

FILE = Path(os.environ.get("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms")
HEADER = "X-Exp4-T-Send"


def stamp() -> float:
    return time.time()


def owd_ms(t_send: float, t_recv: float | None = None) -> float:
    recv = time.time() if t_recv is None else float(t_recv)
    try:
        send = float(t_send)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (recv - send) * 1000.0)


def record(t_send: float, t_recv: float | None = None) -> float:
    ms = owd_ms(t_send, t_recv)
    try:
        FILE.write_text(f"{ms:.3f}\n", encoding="utf-8")
    except OSError:
        pass
    return ms


def read_latest(max_age_s: float = 3.0) -> float | None:
    try:
        st = FILE.stat()
        if max_age_s > 0 and (time.time() - st.st_mtime) > max_age_s:
            return None
        return float(FILE.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None
