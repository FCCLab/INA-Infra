#!/usr/bin/env python3
"""Exp4 S4 server backend: same as S1 (iperf3 -s + queued SFTP) plus encrypt.

Application time is create-random + encrypt + enqueue (encoded in the filename).
Queue wait is not part of application latency; the UE pulls every listed file.
"""

from __future__ import annotations

import collections
import hashlib
import os
import pwd
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from connected_clients import attach as attach_clients
from connected_clients import snapshot as snapshot_clients

app = FastAPI(title="Exp4 S4 backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
attach_clients(app)

IPERF_PORT = int(os.environ.get("IPERF_PORT", "5201"))
IPERF_PORT_COUNT = max(1, int(os.environ.get("IPERF_PORT_COUNT", "8")))
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))
LOG_MAX = int(os.environ.get("IPERF_LOG_MAX", "400"))
SSHD_LOG = os.environ.get("SSHD_LOG", "/tmp/sshd.log")
IPERF_AUTOSTART = os.environ.get("IPERF_AUTOSTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
SFTP_AUTOSTART = os.environ.get("SFTP_AUTOSTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
BLOB_BYTES = int(os.environ.get("EXP4_BLOB_BYTES", str(1 * 1024 * 1024)))
PBKDF2_ITERS = int(os.environ.get("EXP4_PBKDF2_ITERS", "80000"))
READY_DEPTH = max(1, int(os.environ.get("EXP4_READY_QUEUE_DEPTH", "32")))
DOWNLOAD_DIR = Path(os.environ.get("EXP4_DOWNLOAD_DIR", "/home/ina/download"))
FILE_RE = re.compile(
    r"^q-(\d+)-([0-9]+(?:\.[0-9]+)?)(?:-([0-9]+(?:\.[0-9]+)?))?\.bin$"
)

_SFTP_WANTED = SFTP_AUTOSTART
_FILE_SEQ = 0
_SFTP_STATS: dict[str, Any] = {
    "generated": 0,
    "ready": 0,
    "last_file": "",
    "last_error": "",
    "last_encrypt_ms": None,
    "last_app_ms": None,
}

_LOCK = threading.Lock()
_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=LOG_MAX)
_SEQ = 0
_IPERF_PROCS: dict[int, subprocess.Popen[str]] = {}
_IPERF_WANTED = False
_IPERF_WAKE = threading.Event()
_IPERF_PORT_STATE: dict[int, dict[str, Any]] = {
    p: {"running": False, "pid": None, "cmd": [], "error": ""}
    for p in range(IPERF_PORT, IPERF_PORT + IPERF_PORT_COUNT)
}


class IperfControl(BaseModel):
    action: str = Field(..., description="start|stop|restart")


def _iperf_ports() -> list[int]:
    return list(range(IPERF_PORT, IPERF_PORT + IPERF_PORT_COUNT))


def _ina_ids() -> tuple[int, int]:
    try:
        rec = pwd.getpwnam("ina")
        return rec.pw_uid, rec.pw_gid
    except KeyError:
        return os.getuid(), os.getgid()


def _ready_files() -> list[Path]:
    if not DOWNLOAD_DIR.is_dir():
        return []
    items: list[tuple[int, Path]] = []
    for path in DOWNLOAD_DIR.iterdir():
        m = FILE_RE.match(path.name)
        if m and path.is_file():
            items.append((int(m.group(1)), path))
    items.sort(key=lambda x: x[0])
    return [p for _, p in items]


def _encrypt_blob(plain: bytes) -> bytes:
    digest = hashlib.sha256(plain).hexdigest()
    key = hashlib.pbkdf2_hmac("sha256", b"ina-exp4", digest.encode(), PBKDF2_ITERS)
    klen = len(key)
    out = bytearray(len(plain))
    for i, a in enumerate(plain):
        out[i] = a ^ key[i % klen]
    return bytes(out)


def _generate_one() -> Path:
    """Application work: create a random 1 MB file, encrypt it, enqueue it."""
    global _FILE_SEQ
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with _LOCK:
        _FILE_SEQ += 1
        seq = _FILE_SEQ
    plain = os.urandom(BLOB_BYTES)
    t_enc = time.time()
    blob = _encrypt_blob(plain)
    encrypt_ms = (time.time() - t_enc) * 1000.0
    tmp = DOWNLOAD_DIR / f".w-{seq:06d}"
    tmp.write_bytes(blob)
    uid, gid = _ina_ids()
    os.chown(tmp, uid, gid)
    os.chmod(tmp, 0o644)
    app_ms = max(0.0, (time.time() - t0) * 1000.0)
    path = DOWNLOAD_DIR / f"q-{seq:06d}-{t0:.6f}-{app_ms:.3f}.bin"
    tmp.replace(path)
    with _LOCK:
        _SFTP_STATS["generated"] = int(_SFTP_STATS["generated"]) + 1
        _SFTP_STATS["last_file"] = path.name
        _SFTP_STATS["last_error"] = ""
        _SFTP_STATS["last_encrypt_ms"] = encrypt_ms
        _SFTP_STATS["last_app_ms"] = app_ms
    _append_log(
        f"generate+encrypt {path.name} bytes={BLOB_BYTES} encrypt_ms={encrypt_ms:.1f} "
        f"app_ms={app_ms:.3f} q={len(_ready_files())}/{READY_DEPTH}",
        "sftp",
    )
    return path


def _sftp_generator() -> None:
    while True:
        with _LOCK:
            wanted = _SFTP_WANTED
        if not wanted:
            time.sleep(0.2)
            continue
        ready = _ready_files()
        with _LOCK:
            _SFTP_STATS["ready"] = len(ready)
        if len(ready) >= READY_DEPTH:
            time.sleep(0.1)
            continue
        try:
            _generate_one()
        except Exception as exc:
            with _LOCK:
                _SFTP_STATS["last_error"] = str(exc)
            _append_log(f"generate failed: {exc}", "sftp")
            time.sleep(1.0)


def _listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _append_log(line: str, kind: str = "iperf") -> None:
    global _SEQ
    text = line.rstrip("\n\r")
    if not text:
        return
    with _LOCK:
        _SEQ += 1
        _LOG.append(
            {
                "seq": _SEQ,
                "ts": time.strftime("%H:%M:%S"),
                "kind": kind,
                "line": text,
            }
        )


def _iperf_cmd(port: int) -> list[str]:
    cmd = ["iperf3", "-s", "-p", str(port), "-i", "1", "--forceflush"]
    bind = (
        os.environ.get("SIM5G_IP")
        or os.environ.get("MULTUS_IP")
        or os.environ.get("N6_IP")
        or ""
    ).strip()
    if bind:
        cmd.extend(["-B", bind])
    return cmd


def _kill_one(port: int) -> None:
    with _LOCK:
        proc = _IPERF_PROCS.get(port)
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    with _LOCK:
        if _IPERF_PROCS.get(port) is proc:
            _IPERF_PROCS.pop(port, None)
            st = _IPERF_PORT_STATE.setdefault(port, {})
            st["running"] = False
            st["pid"] = None


def _kill_iperf() -> None:
    for port in list(_iperf_ports()):
        _kill_one(port)


def _set_wanted(wanted: bool) -> None:
    global _IPERF_WANTED
    with _LOCK:
        _IPERF_WANTED = wanted
    _IPERF_WAKE.set()
    if not wanted:
        _kill_iperf()


def _iperf_supervisor_one(port: int) -> None:
    backoff = 2.0
    while True:
        with _LOCK:
            wanted = _IPERF_WANTED
        if not wanted:
            _kill_one(port)
            _IPERF_WAKE.wait(timeout=1.0)
            _IPERF_WAKE.clear()
            continue

        cmd = _iperf_cmd(port)
        _append_log("starting: " + " ".join(cmd), "iperf")
        started = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError:
            _append_log("iperf3 binary not found", "iperf")
            with _LOCK:
                _IPERF_PORT_STATE[port] = {
                    "running": False,
                    "pid": None,
                    "cmd": cmd,
                    "error": "iperf3 not found",
                }
            time.sleep(10)
            continue
        except OSError as exc:
            _append_log(f"iperf3 :{port} spawn failed: {exc}", "iperf")
            with _LOCK:
                _IPERF_PORT_STATE[port] = {
                    "running": False,
                    "pid": None,
                    "cmd": cmd,
                    "error": str(exc),
                }
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue

        with _LOCK:
            _IPERF_PROCS[port] = proc
            _IPERF_PORT_STATE[port] = {
                "running": True,
                "pid": proc.pid,
                "cmd": cmd,
                "error": "",
            }

        assert proc.stdout is not None
        for line in proc.stdout:
            with _LOCK:
                still = _IPERF_WANTED
            if not still:
                break
            _append_log(f":{port} {line}", "iperf")

        if not _IPERF_WANTED:
            _kill_one(port)
            _append_log(f"iperf3 :{port} stopped by control", "iperf")
            backoff = 2.0
            continue

        rc = proc.wait()
        with _LOCK:
            if _IPERF_PROCS.get(port) is proc:
                _IPERF_PROCS.pop(port, None)
            st = _IPERF_PORT_STATE.setdefault(port, {})
            st["running"] = False
            st["pid"] = None
        if time.time() - started > 5:
            backoff = 2.0
        with _LOCK:
            still = _IPERF_WANTED
        if not still:
            continue
        _append_log(f"iperf3 :{port} exited rc={rc}; retry in {backoff:.0f}s", "iperf")
        if _IPERF_WAKE.wait(timeout=backoff):
            _IPERF_WAKE.clear()
        backoff = min(backoff * 2, 30.0)


def _tail_sshd() -> None:
    path = Path(SSHD_LOG)
    while True:
        try:
            if not path.exists():
                time.sleep(0.5)
                continue
            with path.open(encoding="utf-8", errors="replace") as fh:
                while True:
                    line = fh.readline()
                    if line:
                        _append_log(line, "sshd")
                    else:
                        time.sleep(0.2)
        except OSError as exc:
            _append_log(f"sshd log: {exc}", "sshd")
            time.sleep(2.0)


def _snapshot() -> dict[str, Any]:
    bind = os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or os.environ.get("N6_IP") or "127.0.0.1"
    ready = _ready_files()
    with _LOCK:
        logs = list(_LOG)
        ports = {p: dict(st) for p, st in _IPERF_PORT_STATE.items()}
        wanted = _IPERF_WANTED
        sftp_wanted = _SFTP_WANTED
        sftp_stats = dict(_SFTP_STATS)
        sftp_stats["ready"] = len(ready)
    listening = [p for p in _iperf_ports() if _listening(p, bind)]
    clients = snapshot_clients()
    return {
        "ok": True,
        "app": "exp4-s4",
        "role": "server-backend",
        "iperf_listen": bool(listening),
        "iperf_ports": [
            {"port": p, "listen": p in listening, **ports.get(p, {})} for p in _iperf_ports()
        ],
        "sftp_listen": _listening(SFTP_PORT),
        "n6_ip": os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or os.environ.get("N6_IP") or "",
        "to_client_iface": os.environ.get("TO_CLIENT_IFACE", "net1"),
        "blob_bytes": BLOB_BYTES,
        "payload_dir": str(DOWNLOAD_DIR),
        "sftp": {
            "wanted": sftp_wanted,
            "autostart": SFTP_AUTOSTART,
            "ready_q": len(ready),
            "ready_max": READY_DEPTH,
            **sftp_stats,
        },
        "iperf": {
            "running": any(st.get("running") for st in ports.values()),
            "wanted": wanted,
            "autostart": IPERF_AUTOSTART,
            "listen_ports": listening,
        },
        "clients": clients,
        "clients_count": len(clients),
        "log": logs,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/e2e")
def e2e() -> dict:
    """Fallback probe. Native SFTP E2E uses t_send in the queued filename (pre-encrypt)."""
    t_send = time.time()
    ready = _ready_files()
    bind = os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or "127.0.0.1"
    listening = _listening(IPERF_PORT, bind)
    return {
        "ok": True,
        "t_send": t_send,
        "blob_bytes": BLOB_BYTES,
        "ready_q": len(ready),
        "iperf_listen": listening,
    }


@app.get("/api/status")
def status() -> dict:
    return _snapshot()


@app.post("/api/iperf")
def iperf(body: IperfControl) -> dict:
    action = (body.action or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="action must be start|stop|restart")
    if action == "stop":
        _set_wanted(False)
        _append_log("control: stop", "iperf")
    elif action == "start":
        _set_wanted(True)
        _append_log("control: start", "iperf")
    else:
        _set_wanted(False)
        time.sleep(0.3)
        _set_wanted(True)
        _append_log("control: restart", "iperf")
    return {"ok": True, **_snapshot()}


@app.on_event("startup")
def _startup() -> None:
    for port in _iperf_ports():
        threading.Thread(
            target=_iperf_supervisor_one,
            args=(port,),
            daemon=True,
            name=f"iperf-{port}",
        ).start()
    threading.Thread(target=_tail_sshd, daemon=True, name="sshd-tail").start()
    threading.Thread(target=_sftp_generator, daemon=True, name="sftp-generate").start()
    if SFTP_AUTOSTART:
        _append_log(
            f"sftp queue autostart — {BLOB_BYTES} byte encrypted files, "
            f"pbkdf2={PBKDF2_ITERS} depth {READY_DEPTH} in {DOWNLOAD_DIR}",
            "sftp",
        )
    if IPERF_AUTOSTART:
        _set_wanted(True)
        _append_log(
            f"autostart enabled — iperf3 -s on {IPERF_PORT}-{IPERF_PORT + IPERF_PORT_COUNT - 1}",
            "iperf",
        )
    else:
        _append_log("autostart disabled — use Control to start iperf3 -s", "iperf")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BACKEND_PORT", "8080")))
