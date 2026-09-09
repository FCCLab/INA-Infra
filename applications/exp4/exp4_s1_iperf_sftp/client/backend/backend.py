#!/usr/bin/env python3
"""Exp4 S1 UE backend: SFTP + controllable iperf3 DL, logs forwarded to the console.

List the server queue every 1s and download every file that is not already in
flight. Application latency is generate-random time from the filename; E2E is
app + ICMP tx, smoothed with EMA 0.5.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import sys

for _p in ("/usr/local/bin", "/app/backend", "/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from to_server import (
        connect_tcp,
        detect_to_server_iface,
        ensure_pin_watch,
        pin_to_server,
    )
except ImportError:
    connect_tcp = None  # type: ignore
    detect_to_server_iface = None  # type: ignore
    ensure_pin_watch = None  # type: ignore
    pin_to_server = None  # type: ignore
from ue_control import (
    Iperf3Client,
    LogBuffer,
    app_fields,
    attach_app_routes,
    attach_iperf_routes,
)

app = FastAPI(title="Exp4 S1 UE backend", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SERVER = os.environ.get("TARGET_SERVER_IP") or os.environ.get("SFTP_HOST") or "10.1.137.211"
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))
SFTP_AUTOSTART = os.environ.get("SFTP_AUTOSTART", "1").strip().lower() not in ("0", "false", "no", "off")
SFTP_REMOTE_DIR = os.environ.get("SFTP_REMOTE_DIR", "download")
LIST_PERIOD_S = float(os.environ.get("EXP4_LIST_PERIOD_S", "1.0"))
EMA_ALPHA = float(os.environ.get("EXP4_EMA_ALPHA", "0.5"))
FILE_RE = re.compile(
    r"^q-(\d+)-([0-9]+(?:\.[0-9]+)?)(?:-([0-9]+(?:\.[0-9]+)?))?\.bin$"
)
APP_LAT_FILE = Path(os.environ.get("EXP4_APP_LATENCY_FILE") or "/tmp/exp4_app_latency_ms")
E2E_LAT_FILE = Path(os.environ.get("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms")
COMBO_LAT_FILE = Path(os.environ.get("EXP4_COMBO_E2E_FILE") or "/tmp/exp4_combo_e2e_latency_ms")

_LOCK = threading.Lock()
_LAST: dict[str, Any] = {}
_BUSY = False
APP_LOG = LogBuffer(kind="sftp")
IPERF = Iperf3Client()
_SFTP_WANTED = SFTP_AUTOSTART
_IN_FLIGHT: set[str] = set()
_EMA_APP: float | None = None
_EMA_E2E: float | None = None
_SFTP_STATS: dict[str, Any] = {
    "running": False,
    "wanted": SFTP_AUTOSTART,
    "success": 0,
    "failed": 0,
    "deleted": 0,
    "in_flight": 0,
    "ema_app_ms": None,
    "ema_e2e_ms": None,
    "error": "",
}


def _append_app(line: str) -> None:
    APP_LOG.append(line)


def _tx_ms() -> float | None:
    host = os.environ.get("SFTP_HOST") or SERVER
    iface = detect_to_server_iface() if detect_to_server_iface is not None else None
    cmd = ["ping", "-c", "1", "-W", "1"]
    if iface:
        cmd.extend(["-I", iface])
    cmd.append(host)
    try:
        out = subprocess.check_output(cmd, text=True, timeout=2, stderr=subprocess.STDOUT)
    except Exception:
        return None
    m = re.search(r"time[=<]([\d.]+)\s*ms", out)
    return float(m.group(1)) if m else None


def _write_ms(path: Path, ms: float) -> None:
    try:
        path.write_text(f"{ms:.3f}\n", encoding="utf-8")
    except OSError:
        pass


def _on_file_done(name: str, app_ms: float | None, nbytes: int, transfer_s: float) -> None:
    global _EMA_APP, _EMA_E2E
    tx = _tx_ms()
    with _LOCK:
        _IN_FLIGHT.discard(name)
        if app_ms is not None:
            _EMA_APP = app_ms if _EMA_APP is None else (EMA_ALPHA * _EMA_APP + (1.0 - EMA_ALPHA) * app_ms)
            _write_ms(APP_LAT_FILE, _EMA_APP)
            _write_ms(E2E_LAT_FILE, _EMA_APP)
            if tx is not None:
                sample = app_ms + tx
                _EMA_E2E = sample if _EMA_E2E is None else (EMA_ALPHA * _EMA_E2E + (1.0 - EMA_ALPHA) * sample)
                _write_ms(COMBO_LAT_FILE, _EMA_E2E)
        _SFTP_STATS["success"] = int(_SFTP_STATS["success"]) + 1
        _SFTP_STATS["deleted"] = int(_SFTP_STATS["deleted"]) + 1
        _SFTP_STATS["in_flight"] = len(_IN_FLIGHT)
        _SFTP_STATS["ema_app_ms"] = _EMA_APP
        _SFTP_STATS["ema_e2e_ms"] = _EMA_E2E
        _SFTP_STATS["error"] = ""
        _LAST.update(
            {
                "kind": "sftp",
                "file_id": name,
                "bytes": nbytes,
                "app_ms": app_ms,
                "tx_ms": tx,
                "e2e_ms": (app_ms + tx) if (app_ms is not None and tx is not None) else app_ms,
                "ema_app_ms": _EMA_APP,
                "ema_e2e_ms": _EMA_E2E,
                "transfer_s": transfer_s,
                "goodput_mbit": (nbytes * 8.0) / max(transfer_s, 1e-6) / 1e6,
                "deleted": True,
            }
        )
        last = dict(_LAST)
    _append_app(
        f"sftp {name} bytes={nbytes} app_ms={app_ms} tx_ms={tx} "
        f"ema_app={last.get('ema_app_ms')} ema_e2e={last.get('ema_e2e_ms')} "
        f"transfer_s={transfer_s:.3f}"
    )


def _open_sftp():
    import paramiko

    host = os.environ.get("SFTP_HOST") or SERVER
    user = os.environ.get("SFTP_USER", "ina")
    password = os.environ.get("SFTP_PASS", "ina")
    if connect_tcp is not None:
        sock = connect_tcp(host, SFTP_PORT, timeout=30)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        src = os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or ""
        if src:
            sock.bind((src, 0))
        sock.connect((host, SFTP_PORT))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=SFTP_PORT, username=user, password=password, timeout=30, sock=sock)
    return client, client.open_sftp()


def _list_remote() -> list[tuple[str, float | None]]:
    client = None
    try:
        client, sftp = _open_sftp()
        out: list[tuple[str, float | None]] = []
        for name in sftp.listdir(SFTP_REMOTE_DIR):
            m = FILE_RE.match(name)
            if not m:
                continue
            app_ms = float(m.group(3)) if m.group(3) else None
            out.append((name, app_ms))
        sftp.close()
        return out
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _download_one(name: str, app_ms: float | None) -> None:
    local_dir = Path(os.environ.get("SFTP_LOCAL_DIR", "/tmp/exp4-s1-dl"))
    local_dir.mkdir(parents=True, exist_ok=True)
    local = local_dir / name
    client = None
    nbytes = 0
    try:
        client, sftp = _open_sftp()
        remote = f"{SFTP_REMOTE_DIR}/{name}"

        def _cb(transferred: int, _total: int) -> None:
            nonlocal nbytes
            nbytes = transferred

        t0 = time.time()
        sftp.get(remote, str(local), callback=_cb)
        transfer_s = max(time.time() - t0, 1e-6)
        try:
            sftp.remove(remote)
        except OSError:
            pass
        sftp.close()
        local.unlink(missing_ok=True)
        _on_file_done(name, app_ms, nbytes, transfer_s)
    except Exception as exc:
        with _LOCK:
            _IN_FLIGHT.discard(name)
            _SFTP_STATS["failed"] = int(_SFTP_STATS["failed"]) + 1
            _SFTP_STATS["in_flight"] = len(_IN_FLIGHT)
            _SFTP_STATS["error"] = str(exc)
        _append_app(f"sftp {name} failed: {exc}")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        local.unlink(missing_ok=True)


def _sftp_once() -> dict:
    """One-shot: list and start any idle files; return current snapshot last."""
    listed = _list_remote()
    started = 0
    for name, app_ms in listed:
        with _LOCK:
            if name in _IN_FLIGHT:
                continue
            _IN_FLIGHT.add(name)
            _SFTP_STATS["in_flight"] = len(_IN_FLIGHT)
        threading.Thread(
            target=_download_one, args=(name, app_ms), daemon=True, name=f"sftp-{name}"
        ).start()
        started += 1
    if started == 0 and not listed:
        raise TimeoutError("no queued file ready")
    time.sleep(0.05)
    with _LOCK:
        return dict(_LAST)


def _sftp_supervisor() -> None:
    while True:
        with _LOCK:
            wanted = _SFTP_WANTED
        if not wanted:
            with _LOCK:
                _SFTP_STATS["running"] = False
            time.sleep(0.2)
            continue
        if detect_to_server_iface is not None and not detect_to_server_iface():
            time.sleep(1.0)
            continue
        if pin_to_server is not None:
            pin_to_server()
        with _LOCK:
            _SFTP_STATS["running"] = True
        try:
            listed = _list_remote()
            for name, app_ms in listed:
                with _LOCK:
                    if name in _IN_FLIGHT:
                        continue
                    _IN_FLIGHT.add(name)
                    _SFTP_STATS["in_flight"] = len(_IN_FLIGHT)
                threading.Thread(
                    target=_download_one,
                    args=(name, app_ms),
                    daemon=True,
                    name=f"sftp-{name}",
                ).start()
        except Exception as exc:
            with _LOCK:
                _SFTP_STATS["error"] = str(exc)
            _append_app(f"sftp list failed: {exc}")
        time.sleep(max(0.2, LIST_PERIOD_S))


def _set_sftp_wanted(wanted: bool) -> None:
    global _SFTP_WANTED
    with _LOCK:
        _SFTP_WANTED = wanted
        _SFTP_STATS["wanted"] = wanted


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        sftp = dict(_SFTP_STATS)
        last = dict(_LAST)
        busy = _BUSY
        wanted = _SFTP_WANTED
        running = bool(sftp["running"])
    iperf = IPERF.snapshot()
    app = app_fields(running=running, wanted=wanted, log=APP_LOG, extra={"kind": "sftp"})
    return {
        "ok": True,
        "name": "exp4-s1",
        "role": "client-backend",
        "server": SERVER,
        "busy": busy,
        "last": last,
        "sftp": sftp,
        "sftp_running": running,
        "success": sftp["success"],
        "failed": sftp["failed"],
        "deleted": sftp["deleted"],
        "log": app["log"],
        "app": app,
        "iperf": iperf,
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
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        with _LOCK:
            _BUSY = False


attach_iperf_routes(app, IPERF)
attach_app_routes(
    app,
    start_fn=lambda: _set_sftp_wanted(True),
    stop_fn=lambda: _set_sftp_wanted(False),
    log=APP_LOG,
)


@app.on_event("startup")
def _startup() -> None:
    try:
        from heartbeat import start as start_heartbeat
    except ImportError:
        start_heartbeat = None  # type: ignore[assignment]

    def _hb() -> dict:
        iperf = IPERF.snapshot()
        running = bool(iperf.get("running"))
        port = (iperf.get("config") or {}).get("port") or 5201
        with _LOCK:
            sftp_on = _SFTP_WANTED
        return {
            "detail": (
                f"sftp {'on' if sftp_on else 'off'}; "
                f"iperf3 {'running' if running else 'idle'} :{port}"
            ),
        }

    if start_heartbeat is not None:
        start_heartbeat(payload_fn=_hb)
    if ensure_pin_watch is not None:
        ensure_pin_watch()
    IPERF.start_supervisor()
    threading.Thread(target=_sftp_supervisor, daemon=True, name="sftp-download").start()
    if SFTP_AUTOSTART:
        _append_app("sftp autostart — list every 1s, pull all idle files in parallel")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BACKEND_PORT", "8090")))
