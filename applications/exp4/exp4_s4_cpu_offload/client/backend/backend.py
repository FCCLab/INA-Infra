#!/usr/bin/env python3
"""Exp4 S4 UE backend: continuous download from the encrypt queue, then delete."""

from __future__ import annotations

import collections
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

SERVER = os.environ.get("TARGET_SERVER_IP") or "10.1.137.214"
URL = os.environ.get("DOWNLOAD_URL") or f"http://{SERVER}/download"
WORK_DIR = Path(os.environ.get("EXP4_DOWNLOAD_DIR", "/tmp/exp4-s4-dl"))
IDLE_SLEEP_S = float(os.environ.get("EXP4_IDLE_SLEEP_S", "0.4"))
STREAM_AUTOSTART = os.environ.get("EXP4_STREAM_AUTOSTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
LOG_MAX = int(os.environ.get("EXP4_LOG_MAX", "400"))

_LOCK = threading.Lock()
_WANTED = STREAM_AUTOSTART
_BUSY = False
_LAST: dict[str, Any] = {}
_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=LOG_MAX)
_LOG_SEQ = 0
_STATS: dict[str, Any] = {
    "success": 0,
    "failed": 0,
    "deleted": 0,
    "running": False,
    "wanted": STREAM_AUTOSTART,
    "error": "",
}

app = FastAPI(title="Exp4 S4 UE backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StreamControl(BaseModel):
    action: str = Field(..., description="start|stop")


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _log(line: str, kind: str = "") -> None:
    global _LOG_SEQ
    with _LOCK:
        _LOG_SEQ += 1
        _LOG.append({"seq": _LOG_SEQ, "ts": _ts(), "line": line, "kind": kind})
    print(line, flush=True)


def _download_once() -> dict[str, Any]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    t_first = None
    n = 0
    proc = ""
    file_id = ""
    tmp = WORK_DIR / f"dl-{time.time_ns()}.zip"
    try:
        with urllib.request.urlopen(URL, timeout=180) as resp:
            proc = resp.headers.get("X-Proc-Ms", "") or ""
            file_id = resp.headers.get("X-File-Id", "") or ""
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    if t_first is None:
                        t_first = time.time()
                    n += len(chunk)
                    out.write(chunk)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        if exc.code == 503:
            raise TimeoutError("no encrypted file ready") from exc
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    t_last = time.time()
    first = t_first or t0
    dt = max(t_last - first, 1e-6)
    tmp.unlink(missing_ok=True)
    out = {
        "file_id": file_id,
        "bytes": n,
        "transfer_s": dt,
        "x_proc_ms": proc,
        "url": URL,
        "deleted": True,
        "ok": True,
    }
    with _LOCK:
        _LAST.update(out)
        _STATS["success"] = int(_STATS["success"]) + 1
        _STATS["deleted"] = int(_STATS["deleted"]) + 1
        _STATS["error"] = ""
    _log(
        f"[ok] download {file_id or 'file'} bytes={n} transfer_s={dt:.3f} deleted",
        kind="ok",
    )
    return out


def _supervisor() -> None:
    backoff = IDLE_SLEEP_S
    while True:
        with _LOCK:
            wanted = _WANTED
        if not wanted:
            with _LOCK:
                _STATS["running"] = False
            time.sleep(0.2)
            backoff = IDLE_SLEEP_S
            continue
        with _LOCK:
            _STATS["running"] = True
        try:
            _download_once()
            backoff = IDLE_SLEEP_S
        except TimeoutError:
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 2.0)
        except Exception as exc:
            with _LOCK:
                _STATS["failed"] = int(_STATS["failed"]) + 1
                _STATS["error"] = str(exc)
            _log(f"download failed: {exc}", kind="err")
            time.sleep(min(backoff, 5.0))
            backoff = min(backoff * 2, 10.0)


def _set_wanted(wanted: bool) -> None:
    global _WANTED
    with _LOCK:
        _WANTED = wanted
        _STATS["wanted"] = wanted
    _log("stream " + ("start" if wanted else "stop"))


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "ok": True,
            "app": "exp4-s4",
            "role": "client-backend",
            "url": URL,
            "server": SERVER,
            "busy": _BUSY,
            "stream_running": _STATS["running"],
            "wanted": _WANTED,
            "success": _STATS["success"],
            "failed": _STATS["failed"],
            "deleted": _STATS["deleted"],
            "last": dict(_LAST),
            "error": _STATS["error"],
            "log": list(_LOG),
        }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    return _snapshot()


@app.post("/api/stream")
def stream(body: StreamControl) -> dict:
    action = (body.action or "").strip().lower()
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action must be start|stop")
    _set_wanted(action == "start")
    return {"ok": True, **_snapshot()}


@app.post("/api/download")
def download() -> dict:
    global _BUSY
    with _LOCK:
        if _BUSY:
            raise HTTPException(status_code=409, detail="busy")
        _BUSY = True
    try:
        result = _download_once()
        snap = _snapshot()
        snap["last"] = result
        return {"ok": True, **snap}
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        with _LOCK:
            _BUSY = False


@app.on_event("startup")
def _startup() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from heartbeat import start as start_heartbeat
    except ImportError:
        start_heartbeat = None  # type: ignore[assignment]

    def _hb() -> dict:
        with _LOCK:
            wanted = _WANTED
            last = dict(_LAST) if _LAST else {}
        detail = "streaming" if wanted else "idle"
        if last.get("file_id"):
            detail += f" last={last['file_id']}"
        return {"detail": detail}

    if start_heartbeat is not None:
        start_heartbeat(payload_fn=_hb)
    if STREAM_AUTOSTART:
        _set_wanted(True)
        _log("autostart download loop")
    threading.Thread(target=_supervisor, daemon=True, name="s4-download").start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BACKEND_PORT", "8090")))
