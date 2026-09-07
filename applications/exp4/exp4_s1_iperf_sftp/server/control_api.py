#!/usr/bin/env python3
"""Exp4 S1 server backend: multi-client iperf3 -s pool + SFTP status."""

from __future__ import annotations

import collections
import os
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

app = FastAPI(title="Exp4 S1 backend", docs_url="/docs")
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
    with _LOCK:
        logs = list(_LOG)
        ports = {p: dict(st) for p, st in _IPERF_PORT_STATE.items()}
        wanted = _IPERF_WANTED
    listening = [p for p in _iperf_ports() if _listening(p, bind)]
    clients = snapshot_clients()
    return {
        "ok": True,
        "app": "exp4-s1",
        "role": "server-backend",
        "iperf_listen": bool(listening),
        "iperf_ports": [
            {"port": p, "listen": p in listening, **ports.get(p, {})} for p in _iperf_ports()
        ],
        "sftp_listen": _listening(SFTP_PORT),
        "n6_ip": os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or os.environ.get("N6_IP") or "",
        "to_client_iface": os.environ.get("TO_CLIENT_IFACE", "net1"),
        "payload": "/home/ina/download/exp4-5mb.bin",
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
