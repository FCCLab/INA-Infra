#!/usr/bin/env python3
"""Exp4 S1 UE backend: SFTP + controllable iperf3 DL, logs forwarded to the console."""

from __future__ import annotations

import collections
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Exp4 S1 UE backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SERVER = os.environ.get("TARGET_SERVER_IP") or os.environ.get("SFTP_HOST") or "10.1.137.211"
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))
IPERF_PORT = int(os.environ.get("IPERF_PORT", "5201"))
IPERF_PORT_COUNT = max(1, int(os.environ.get("IPERF_PORT_COUNT", "8")))
IPERF_PARALLEL = int(os.environ.get("IPERF_PARALLEL", "5"))
IPERF_BANDWIDTH = os.environ.get("IPERF_BANDWIDTH", "10M")
IPERF_TIME = os.environ.get("IPERF_TIME", "0")
IPERF_INTERVAL = os.environ.get("IPERF_INTERVAL", "1")
IPERF_AUTOSTART = os.environ.get("IPERF_AUTOSTART", "1").strip().lower() not in ("0", "false", "no", "off")
LOG_MAX = int(os.environ.get("IPERF_LOG_MAX", "400"))

INTERVAL_RE = re.compile(
    r"\[(?:\s*\d+|SUM)\]\s+"
    r"([\d.]+)-([\d.]+)\s+sec\s+"
    r".*?\s+"
    r"([\d.]+)\s+([KMG])?bits/sec",
    re.IGNORECASE,
)
SUM_RE = re.compile(r"\[SUM\]", re.IGNORECASE)

_LOCK = threading.Lock()
_LAST: dict[str, Any] = {}
_BUSY = False
_IPERF_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=LOG_MAX)
_IPERF_SEQ = 0
_IPERF_PROC: Optional[subprocess.Popen[str]] = None
_IPERF_WANTED = False
_IPERF_WAKE = threading.Event()
_IPERF_CFG: dict[str, Any] = {
    "parallel": IPERF_PARALLEL,
    "bandwidth": IPERF_BANDWIDTH,
    "time": IPERF_TIME,
    "interval": IPERF_INTERVAL,
    "port": IPERF_PORT,
}
_IPERF_STATE: dict[str, Any] = {
    "running": False,
    "wanted": False,
    "autostart": IPERF_AUTOSTART,
    "pid": None,
    "cmd": [],
    "mbits_per_second": None,
    "error": "",
    "config": dict(_IPERF_CFG),
}


class IperfControl(BaseModel):
    action: str = Field(..., description="start|stop|restart")
    parallel: Optional[int] = Field(None, ge=1, le=32)
    bandwidth: Optional[str] = None
    time: Optional[str] = None


def _bind_iface() -> str:
    iface = os.environ.get("TO_SERVER_IFACE") or os.environ.get("BIND_DEV") or ""
    if iface:
        return iface
    scheme = os.environ.get("SCHEME_ID", "")
    no5g = scheme == "exp4-no5g" or os.environ.get("EXP4_NO5G", "").lower() in ("1", "true", "yes")
    return "net1" if no5g else ""


def _iperf_cmd(port: Optional[int] = None) -> list[str]:
    host = os.environ.get("IPERF_HOST") or SERVER
    with _LOCK:
        cfg = dict(_IPERF_CFG)
        if port is not None:
            cfg["port"] = int(port)
            _IPERF_CFG["port"] = int(port)
            _IPERF_STATE["config"] = dict(_IPERF_CFG)
    cmd = [
        "iperf3",
        "-c",
        host,
        "-p",
        str(cfg["port"]),
        "-R",
        "-P",
        str(cfg["parallel"]),
        "-b",
        str(cfg["bandwidth"]),
        "-t",
        str(cfg["time"]),
        "-i",
        str(cfg["interval"]),
        "--forceflush",
    ]
    iface = _bind_iface()
    if iface:
        cmd.extend(["--bind-dev", iface])
    return cmd


def _parse_mbps(line: str) -> Optional[float]:
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    bits = value * {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return bits / 1e6


def _append_log(line: str) -> None:
    global _IPERF_SEQ
    text = line.rstrip("\n\r")
    if not text:
        return
    mbps = _parse_mbps(text)
    with _LOCK:
        _IPERF_SEQ += 1
        _IPERF_LOG.append(
            {
                "seq": _IPERF_SEQ,
                "ts": time.strftime("%H:%M:%S"),
                "kind": "iperf",
                "line": text,
            }
        )
        parallel = int(_IPERF_CFG.get("parallel") or 1)
        if mbps is not None and (SUM_RE.search(text) or parallel <= 1):
            _IPERF_STATE["mbits_per_second"] = mbps


def _next_iperf_port(port: int) -> int:
    return IPERF_PORT + ((int(port) - IPERF_PORT + 1) % IPERF_PORT_COUNT)


def _kill_iperf() -> None:
    global _IPERF_PROC
    with _LOCK:
        proc = _IPERF_PROC
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
        if _IPERF_PROC is proc:
            _IPERF_PROC = None
            _IPERF_STATE["running"] = False
            _IPERF_STATE["pid"] = None


def _iperf_supervisor() -> None:
    global _IPERF_PROC
    backoff = 2.0
    while True:
        with _LOCK:
            wanted = _IPERF_WANTED
        if not wanted:
            _kill_iperf()
            _IPERF_WAKE.wait(timeout=1.0)
            _IPERF_WAKE.clear()
            continue

        cmd = _iperf_cmd()
        _append_log("starting: " + " ".join(cmd))
        started = time.time()
        busy = False
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
            _append_log("iperf3 binary not found")
            with _LOCK:
                _IPERF_STATE["running"] = False
                _IPERF_STATE["error"] = "iperf3 not found"
            time.sleep(10)
            continue
        except OSError as exc:
            _append_log(f"iperf3 spawn failed: {exc}")
            with _LOCK:
                _IPERF_STATE["running"] = False
                _IPERF_STATE["error"] = str(exc)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue

        with _LOCK:
            _IPERF_PROC = proc
            _IPERF_STATE["running"] = True
            _IPERF_STATE["pid"] = proc.pid
            _IPERF_STATE["cmd"] = cmd
            _IPERF_STATE["error"] = ""
            _IPERF_STATE["config"] = dict(_IPERF_CFG)

        assert proc.stdout is not None
        for line in proc.stdout:
            with _LOCK:
                still = _IPERF_WANTED
            if not still:
                break
            low = line.lower()
            if "busy running a test" in low or "server is busy" in low:
                busy = True
            _append_log(line)

        if not _IPERF_WANTED:
            _kill_iperf()
            _append_log("iperf3 stopped by control")
            backoff = 2.0
            continue

        rc = proc.wait()
        with _LOCK:
            _IPERF_PROC = None
            _IPERF_STATE["running"] = False
            _IPERF_STATE["pid"] = None
            cur_port = int(_IPERF_CFG.get("port") or IPERF_PORT)
        if busy or (rc != 0 and time.time() - started < 3):
            nxt = _next_iperf_port(cur_port)
            _append_log(f"iperf3 :{cur_port} busy/failed; trying :{nxt}")
            _iperf_cmd(nxt)
            backoff = 0.4
            if _IPERF_WAKE.wait(timeout=backoff):
                _IPERF_WAKE.clear()
            continue
        if time.time() - started > 5:
            backoff = 2.0
        with _LOCK:
            still = _IPERF_WANTED
        if not still:
            continue
        _append_log(f"iperf3 exited rc={rc}; retry in {backoff:.0f}s")
        # Wait with wake so Stop/Start can interrupt the backoff
        if _IPERF_WAKE.wait(timeout=backoff):
            _IPERF_WAKE.clear()
        backoff = min(backoff * 2, 30.0)


def _set_wanted(wanted: bool) -> None:
    global _IPERF_WANTED
    with _LOCK:
        _IPERF_WANTED = wanted
        _IPERF_STATE["wanted"] = wanted
    _IPERF_WAKE.set()
    if not wanted:
        _kill_iperf()


def _apply_cfg(body: IperfControl) -> None:
    with _LOCK:
        if body.parallel is not None:
            _IPERF_CFG["parallel"] = int(body.parallel)
        if body.bandwidth:
            _IPERF_CFG["bandwidth"] = str(body.bandwidth).strip()
        if body.time is not None and str(body.time).strip() != "":
            _IPERF_CFG["time"] = str(body.time).strip()
        _IPERF_STATE["config"] = dict(_IPERF_CFG)


def _sftp_once() -> dict:
    try:
        import paramiko
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="paramiko missing") from exc
    host = os.environ.get("SFTP_HOST") or SERVER
    user = os.environ.get("SFTP_USER", "ina")
    password = os.environ.get("SFTP_PASS", "ina")
    remote = os.environ.get("SFTP_REMOTE", "download/exp4-5mb.bin")
    local = Path(os.environ.get("SFTP_LOCAL", "/tmp/exp4-5mb.bin"))
    t_connect = time.time()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=SFTP_PORT, username=user, password=password, timeout=30)
    sftp = client.open_sftp()
    t_first = None
    nbytes = 0

    def _cb(transferred: int, _total: int) -> None:
        nonlocal t_first, nbytes
        if t_first is None:
            t_first = time.time()
        nbytes = transferred

    local.parent.mkdir(parents=True, exist_ok=True)
    sftp.get(remote, str(local), callback=_cb)
    t_last = time.time()
    sftp.close()
    client.close()
    first = t_first or t_connect
    dt = max(t_last - first, 1e-6)
    out = {
        "kind": "sftp",
        "host": host,
        "bytes": nbytes,
        "transfer_s": dt,
        "goodput_mbit": (nbytes * 8.0) / dt / 1e6,
    }
    with _LOCK:
        _LAST.update(out)
    _append_log(
        f"sftp done host={host} bytes={nbytes} "
        f"transfer_s={dt:.3f} goodput_mbit={out['goodput_mbit']:.2f}"
    )
    return out


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "ok": True,
            "app": "exp4-s1",
            "role": "client-backend",
            "server": SERVER,
            "busy": _BUSY,
            "last": dict(_LAST),
            "log": list(_IPERF_LOG),
            "iperf": {
                **dict(_IPERF_STATE),
                "config": dict(_IPERF_CFG),
                "log": list(_IPERF_LOG),
            },
        }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    return _snapshot()


@app.post("/api/sftp")
def sftp() -> dict:
    global _BUSY
    with _LOCK:
        if _BUSY:
            raise HTTPException(status_code=409, detail="busy")
        _BUSY = True
    try:
        result = _sftp_once()
        snap = _snapshot()
        snap["last"] = result
        return {"ok": True, **snap}
    finally:
        with _LOCK:
            _BUSY = False


@app.post("/api/iperf")
def iperf(body: IperfControl) -> dict:
    action = (body.action or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="action must be start|stop|restart")
    if action in ("start", "restart"):
        _apply_cfg(body)
    if action == "stop":
        _set_wanted(False)
        _append_log("control: stop")
    elif action == "start":
        _set_wanted(True)
        _append_log("control: start")
    else:  # restart
        _set_wanted(False)
        time.sleep(0.3)
        _set_wanted(True)
        _append_log("control: restart")
    return {"ok": True, **_snapshot()}


@app.on_event("startup")
def _startup() -> None:
    try:
        from heartbeat import start as start_heartbeat
    except ImportError:
        start_heartbeat = None  # type: ignore[assignment]

    def _hb() -> dict:
        with _LOCK:
            iperf = dict(_IPERF_STATE)
        running = bool(iperf.get("running"))
        port = (iperf.get("config") or {}).get("port") or IPERF_PORT
        return {
            "detail": f"iperf {'running' if running else 'idle'} :{port}",
        }

    if start_heartbeat is not None:
        start_heartbeat(payload_fn=_hb)
    threading.Thread(target=_iperf_supervisor, daemon=True, name="iperf-supervisor").start()
    if IPERF_AUTOSTART:
        _set_wanted(True)
        _append_log("autostart enabled")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BACKEND_PORT", "8090")))
