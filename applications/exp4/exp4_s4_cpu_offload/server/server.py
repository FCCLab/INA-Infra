"""Exp4 slice 4 backend: generate → queue → encrypt → queue → download → delete."""

from __future__ import annotations

import collections
import hashlib
import io
import os
import threading
import time
import zipfile
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from connected_clients import attach as attach_clients
from connected_clients import snapshot as snapshot_clients

SIZE = int(os.environ.get("EXP4_BLOB_BYTES", str(5 * 1024 * 1024)))
PBKDF2_ITERS = int(os.environ.get("EXP4_PBKDF2_ITERS", "80000"))
PLAIN_DEPTH = max(1, int(os.environ.get("EXP4_PLAIN_QUEUE_DEPTH", "8")))
READY_DEPTH = max(1, int(os.environ.get("EXP4_READY_QUEUE_DEPTH", "8")))
QUEUE_WAIT_S = float(os.environ.get("EXP4_QUEUE_WAIT_S", "15"))
STREAM_AUTOSTART = os.environ.get("EXP4_STREAM_AUTOSTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
WORK_DIR = Path(os.environ.get("EXP4_WORK_DIR", "/tmp/exp4-s4"))
PLAIN_DIR = WORK_DIR / "plain"
READY_DIR = WORK_DIR / "ready"
LOG_MAX = int(os.environ.get("EXP4_LOG_MAX", "400"))

_LOCK = threading.Lock()
_PLAIN: Queue = Queue(maxsize=PLAIN_DEPTH)
_READY: Queue = Queue(maxsize=READY_DEPTH)
_WANTED = STREAM_AUTOSTART
_SEQ = 0
_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=LOG_MAX)
_LOG_SEQ = 0
_STATS: dict[str, Any] = {
    "generated": 0,
    "encrypted": 0,
    "downloaded": 0,
    "deleted": 0,
    "last_proc_ms": None,
    "last_bytes": 0,
    "last_file_id": "",
    "last_error": "",
}

app = FastAPI(title="Exp4 S4 backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
attach_clients(app)


class StreamControl(BaseModel):
    action: str = Field(..., description="start|stop")


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _log(line: str, kind: str = "") -> None:
    global _LOG_SEQ
    with _LOCK:
        _LOG_SEQ += 1
        entry = {"seq": _LOG_SEQ, "ts": _ts(), "line": line, "kind": kind}
        _LOG.append(entry)
    print(line, flush=True)


def _wipe_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_file():
            child.unlink(missing_ok=True)


def _next_id() -> str:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        seq = _SEQ
    return f"{int(time.time())}-{seq:06d}"


def _generate() -> dict[str, Any]:
    file_id = _next_id()
    path = PLAIN_DIR / f"{file_id}.bin"
    path.write_bytes(os.urandom(SIZE))
    job = {"id": file_id, "path": path, "bytes": SIZE, "created": time.time()}
    with _LOCK:
        _STATS["generated"] = int(_STATS["generated"]) + 1
    return job


def _encrypt(job: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    plain = job["path"].read_bytes()
    digest = hashlib.sha256(plain).hexdigest()
    key = hashlib.pbkdf2_hmac("sha256", b"ina-exp4", digest.encode(), PBKDF2_ITERS)
    enc = bytes(a ^ key[i % len(key)] for i, a in enumerate(plain))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.bin", enc)
        zf.writestr("lut.txt", digest)
        zf.writestr("id.txt", str(job["id"]))
    out = READY_DIR / f"{job['id']}.zip"
    body = buf.getvalue()
    out.write_bytes(body)
    job["path"].unlink(missing_ok=True)
    proc_ms = (time.time() - t0) * 1000.0
    ready = {
        "id": job["id"],
        "path": out,
        "bytes": len(body),
        "plain_bytes": job["bytes"],
        "digest": digest,
        "proc_ms": proc_ms,
    }
    with _LOCK:
        _STATS["encrypted"] = int(_STATS["encrypted"]) + 1
        _STATS["last_proc_ms"] = proc_ms
        _STATS["last_bytes"] = len(body)
        _STATS["last_file_id"] = job["id"]
    return ready


def _generator_loop() -> None:
    while True:
        if not _WANTED:
            time.sleep(0.2)
            continue
        if _PLAIN.full():
            time.sleep(0.05)
            continue
        job = None
        try:
            job = _generate()
            _PLAIN.put(job, timeout=2.0)
        except Full:
            if job:
                Path(job["path"]).unlink(missing_ok=True)
            continue
        except Exception as exc:
            if job:
                Path(job["path"]).unlink(missing_ok=True)
            with _LOCK:
                _STATS["last_error"] = f"generate: {exc}"
            _log(f"generate failed: {exc}", kind="err")
            time.sleep(1.0)
            continue
        _log(f"generate {job['id']} bytes={SIZE} q_plain={_PLAIN.qsize()}/{PLAIN_DEPTH}")


def _encryptor_loop() -> None:
    while True:
        if not _WANTED:
            time.sleep(0.2)
            continue
        if _READY.full():
            time.sleep(0.05)
            continue
        try:
            job = _PLAIN.get(timeout=0.5)
        except Empty:
            continue
        ready = None
        try:
            ready = _encrypt(job)
            _READY.put(ready, timeout=2.0)
        except Full:
            if ready:
                Path(ready["path"]).unlink(missing_ok=True)
            Path(job.get("path", "")).unlink(missing_ok=True)
            continue
        except Exception as exc:
            if ready:
                Path(ready["path"]).unlink(missing_ok=True)
            Path(job.get("path", "")).unlink(missing_ok=True)
            with _LOCK:
                _STATS["last_error"] = f"encrypt: {exc}"
            _log(f"encrypt failed: {exc}", kind="err")
            continue
        _log(
            f"encrypt {ready['id']} proc_ms={ready['proc_ms']:.1f} "
            f"zip_bytes={ready['bytes']} q_ready={_READY.qsize()}/{READY_DEPTH}"
        )


def _set_wanted(wanted: bool) -> None:
    global _WANTED
    with _LOCK:
        _WANTED = wanted
    _log("stream " + ("start" if wanted else "stop"))


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        log = list(_LOG)
        stats = dict(_STATS)
        wanted = _WANTED
    clients = snapshot_clients()
    return {
        "ok": True,
        "app": "exp4-s4",
        "role": "server-backend",
        "blob_bytes": SIZE,
        "pbkdf2_iters": PBKDF2_ITERS,
        "n6_ip": os.environ.get("MULTUS_IP") or "",
        "stream_running": wanted,
        "runs": stats["encrypted"],
        **stats,
        "pipeline": {
            "generate": {"q": _PLAIN.qsize(), "max": PLAIN_DEPTH, "count": stats["generated"]},
            "encrypt": {"q": _READY.qsize(), "max": READY_DEPTH, "count": stats["encrypted"]},
            "download": {"count": stats["downloaded"]},
            "delete": {"count": stats["deleted"]},
        },
        "clients": clients,
        "clients_count": len(clients),
        "log": log,
    }


def _pop_ready() -> dict[str, Any]:
    deadline = time.time() + QUEUE_WAIT_S
    while True:
        try:
            return _READY.get(timeout=0.25)
        except Empty:
            if time.time() >= deadline:
                raise HTTPException(status_code=503, detail="no encrypted file ready") from None


def _iter_and_delete(job: dict[str, Any]) -> Iterator[bytes]:
    path = Path(job["path"])
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        with _LOCK:
            _STATS["downloaded"] = int(_STATS["downloaded"]) + 1
        _log(f"[ok] download {job['id']} bytes={job['bytes']}", kind="ok")
    finally:
        path.unlink(missing_ok=True)
        with _LOCK:
            _STATS["deleted"] = int(_STATS["deleted"]) + 1
        _log(f"delete {job['id']}")


@app.get("/health")
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


@app.post("/api/run")
def run() -> dict:
    """Kick one generate → encrypt into the ready queue (does not wait for download)."""
    ready = None
    try:
        ready = _encrypt(_generate())
        _READY.put(ready, timeout=QUEUE_WAIT_S)
    except Full as exc:
        if ready:
            Path(ready["path"]).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="ready queue full") from exc
    except Exception as exc:
        if ready:
            Path(ready["path"]).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    snap = _snapshot()
    return {"ok": True, "last_proc_ms": ready["proc_ms"], "file_id": ready["id"], **snap}


@app.get("/download")
def download() -> StreamingResponse:
    job = _pop_ready()
    headers = {
        "X-Proc-Ms": f"{float(job.get('proc_ms') or 0):.1f}",
        "X-File-Id": str(job["id"]),
        "X-Plain-Bytes": str(job.get("plain_bytes") or SIZE),
        "Content-Length": str(job["bytes"]),
        "Content-Disposition": f'attachment; filename="{job["id"]}.zip"',
    }
    return StreamingResponse(
        _iter_and_delete(job),
        media_type="application/zip",
        headers=headers,
    )


@app.on_event("startup")
def _startup() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    _wipe_dir(PLAIN_DIR)
    _wipe_dir(READY_DIR)
    if STREAM_AUTOSTART:
        _set_wanted(True)
        _log("autostart generate→encrypt queues")
    threading.Thread(target=_generator_loop, daemon=True, name="s4-generate").start()
    threading.Thread(target=_encryptor_loop, daemon=True, name="s4-encrypt").start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", "8080"))))
