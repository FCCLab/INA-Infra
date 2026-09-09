"""Shared DL generate/publish runtime (control_api <-> controller).

Written by the console/API; polled by the MQTT controller. Survives neither
process restart unless the file still exists under DL_RUNTIME_PATH.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

_LOCK = threading.Lock()
RUNTIME_PATH = os.environ.get("DL_RUNTIME_PATH", "/tmp/exp4-s5-dl-runtime.json")
STATS_PATH = os.environ.get("DL_STATS_PATH", "/tmp/exp4-s5-dl-stats.json")
_STATS_LOCK = threading.Lock()


def write_stats(
    *,
    msgs_per_s: float,
    bytes_per_s: float,
    publish_errors_per_s: float = 0.0,
    total_msgs: int = 0,
    total_bytes: int = 0,
) -> None:
    """Controller publishes measured generate rates for the console/API."""
    payload = {
        "dl_pub_msgs_per_s": round(float(msgs_per_s), 2),
        "dl_pub_bytes_per_s": round(float(bytes_per_s), 1),
        "dl_pub_mbps": round((float(bytes_per_s) * 8.0) / 1e6, 3),
        "dl_pub_errors_per_s": round(float(publish_errors_per_s), 2),
        "dl_pub_total_msgs": int(total_msgs),
        "dl_pub_total_bytes": int(total_bytes),
        "ts": time.time(),
    }
    with _STATS_LOCK:
        tmp = STATS_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, STATS_PATH)
        except OSError:
            pass


def read_stats() -> dict[str, Any]:
    with _STATS_LOCK:
        try:
            with open(STATS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
    return {
        "dl_pub_msgs_per_s": 0.0,
        "dl_pub_bytes_per_s": 0.0,
        "dl_pub_mbps": 0.0,
        "dl_pub_errors_per_s": 0.0,
        "dl_pub_total_msgs": 0,
        "dl_pub_total_bytes": 0,
        "ts": None,
    }


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class DlRuntime:
    """Fast-tier DL generator settings.

    msgs_per_s:
      > 0  — publish that many messages per second per device
      == 0 — max rate (period ~0; broker/queue limited)
    """

    running: bool = True
    msgs_per_s: float = 0.0
    payload_bytes: int = 5000

    def period_s(self) -> float:
        if self.msgs_per_s <= 0:
            return 0.0
        return 1.0 / self.msgs_per_s

    def est_mbps_per_device(self) -> Optional[float]:
        if self.msgs_per_s <= 0:
            return None
        return (self.msgs_per_s * self.payload_bytes * 8.0) / 1e6

    def est_mbps_total(self, n_devices: int) -> Optional[float]:
        per = self.est_mbps_per_device()
        if per is None:
            return None
        return per * max(0, int(n_devices))


def defaults_from_env() -> DlRuntime:
    # Prefer DL_MSGS_PER_S; else derive from DL_FAST_PERIOD_S (0 = max rate).
    period = _env_float("DL_FAST_PERIOD_S", -1.0)
    msgs = _env_float("DL_MSGS_PER_S", -1.0)
    if msgs < 0:
        if period < 0:
            msgs = 3000.0
        elif period <= 0:
            msgs = 0.0
        else:
            msgs = 1.0 / period
    return DlRuntime(
        running=_env_bool("DL_GENERATE", True),
        msgs_per_s=max(0.0, msgs),
        payload_bytes=max(64, _env_int("DL_PAYLOAD_BYTES", 128)),
    )


def _coerce(data: dict[str, Any], base: Optional[DlRuntime] = None) -> DlRuntime:
    cur = base or defaults_from_env()
    running = cur.running
    if "running" in data:
        running = bool(data["running"])
    msgs = cur.msgs_per_s
    if "msgs_per_s" in data and data["msgs_per_s"] is not None:
        try:
            msgs = float(data["msgs_per_s"])
        except (TypeError, ValueError) as exc:
            raise ValueError("msgs_per_s must be a number") from exc
        if msgs < 0:
            raise ValueError("msgs_per_s must be >= 0")
    elif "period_s" in data and data["period_s"] is not None:
        try:
            period = float(data["period_s"])
        except (TypeError, ValueError) as exc:
            raise ValueError("period_s must be a number") from exc
        if period < 0:
            raise ValueError("period_s must be >= 0")
        msgs = 0.0 if period <= 0 else (1.0 / period)
    payload = cur.payload_bytes
    if "payload_bytes" in data and data["payload_bytes"] is not None:
        try:
            payload = int(data["payload_bytes"])
        except (TypeError, ValueError) as exc:
            raise ValueError("payload_bytes must be an integer") from exc
        if payload < 64:
            raise ValueError("payload_bytes must be >= 64")
    return DlRuntime(running=running, msgs_per_s=msgs, payload_bytes=payload)


def load() -> DlRuntime:
    with _LOCK:
        try:
            with open(RUNTIME_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return _coerce(data, defaults_from_env())
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        rt = defaults_from_env()
        _write_unlocked(rt)
        return rt


_CACHE_RT: Optional[DlRuntime] = None
_CACHE_MTIME: float = -1.0
_CACHE_MONO: float = 0.0


def load_cached(ttl_s: float = 0.25) -> DlRuntime:
    """Avoid open()+json on every publish; refresh on mtime or TTL."""
    global _CACHE_RT, _CACHE_MTIME, _CACHE_MONO
    now = time.monotonic()
    with _LOCK:
        if _CACHE_RT is not None and (now - _CACHE_MONO) < ttl_s:
            return _CACHE_RT
        try:
            mtime = os.stat(RUNTIME_PATH).st_mtime
        except OSError:
            mtime = -1.0
        if _CACHE_RT is not None and mtime == _CACHE_MTIME and mtime >= 0:
            _CACHE_MONO = now
            return _CACHE_RT
    rt = load()
    with _LOCK:
        _CACHE_RT = rt
        _CACHE_MTIME = mtime
        _CACHE_MONO = now
        return rt


def invalidate_cache() -> None:
    global _CACHE_RT, _CACHE_MTIME
    with _LOCK:
        _CACHE_RT = None
        _CACHE_MTIME = -1.0


def save(rt: DlRuntime) -> DlRuntime:
    with _LOCK:
        _write_unlocked(rt)
    invalidate_cache()
    return rt


def update(**kwargs: Any) -> DlRuntime:
    with _LOCK:
        try:
            with open(RUNTIME_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            base = _coerce(data, defaults_from_env()) if isinstance(data, dict) else defaults_from_env()
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            base = defaults_from_env()
        rt = _coerce(kwargs, base)
        _write_unlocked(rt)
    invalidate_cache()
    return rt


def _write_unlocked(rt: DlRuntime) -> None:
    tmp = RUNTIME_PATH + ".tmp"
    payload = asdict(rt)
    payload["period_s"] = rt.period_s()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, RUNTIME_PATH)


def status_fields(n_devices: int = 1) -> dict[str, Any]:
    rt = load()
    per = rt.est_mbps_per_device()
    total = rt.est_mbps_total(n_devices)
    out = {
        "dl_running": rt.running,
        "dl_msgs_per_s": rt.msgs_per_s,
        "dl_payload_bytes": rt.payload_bytes,
        "dl_fast_period_s": rt.period_s(),
        "dl_est_mbps_per_device": None if per is None else round(per, 3),
        "dl_est_mbps_total": None if total is None else round(total, 3),
        "dl_est_note": (
            "max rate (broker/queue limited)"
            if rt.msgs_per_s <= 0
            else f"{rt.msgs_per_s:g} msg/s × {rt.payload_bytes} B × {max(0, int(n_devices))} device(s)"
        ),
    }
    out.update(read_stats())
    return out
