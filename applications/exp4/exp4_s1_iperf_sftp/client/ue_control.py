#!/usr/bin/env python3
"""UE console control: main application + extra iperf3, with separate log buffers.

iperf3 is an optional extra load alongside the slice app. It is stopped by
default (IPERF_AUTOSTART=0). Start/stop are independent of the main app.
"""

from __future__ import annotations

import collections
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    from to_server import detect_to_server_iface, iperf_bind_args, pin_to_server
except ImportError:
    detect_to_server_iface = None  # type: ignore
    iperf_bind_args = None  # type: ignore
    pin_to_server = None  # type: ignore

INTERVAL_RE = re.compile(
    r"\[(?:\s*\d+|SUM)\]\s+"
    r"([\d.]+)-([\d.]+)\s+sec\s+"
    r".*?\s+"
    r"([\d.]+)\s+([KMG])?bits/sec",
    re.IGNORECASE,
)
SUM_RE = re.compile(r"\[SUM\]", re.IGNORECASE)


def env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class LogBuffer:
    """Ring buffer of {seq, ts, kind, line} for one console terminal."""

    def __init__(self, kind: str = "app", maxlen: int = 400) -> None:
        self.kind = kind
        self._lock = threading.Lock()
        self._seq = 0
        self._items: collections.deque[dict[str, Any]] = collections.deque(maxlen=maxlen)

    def append(self, line: str, *, kind: Optional[str] = None) -> dict[str, Any]:
        text = str(line).rstrip("\n\r")
        if not text:
            return {}
        with self._lock:
            self._seq += 1
            item = {
                "seq": self._seq,
                "ts": time.strftime("%H:%M:%S"),
                "kind": kind or self.kind,
                "line": text,
            }
            self._items.append(item)
            return dict(item)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._items)


class IperfControl(BaseModel):
    action: str = Field("start", description="start|stop|restart|update")
    args: Optional[str] = Field(None, description="iperf3 argv after the binary")
    parallel: Optional[int] = Field(None, ge=1, le=32)
    bandwidth: Optional[str] = None
    time: Optional[str] = None


class AppControl(BaseModel):
    action: str = Field("start", description="start|stop|restart")


def parse_mbps(line: str) -> Optional[float]:
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    bits = value * {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return bits / 1e6


def iperf_host() -> str:
    return (
        _env("IPERF_HOST")
        or _env("TARGET_SERVER_IP")
        or _env("SFTP_HOST")
        or _env("SERVER_HOST")
        or _env("BROKER_HOST")
        or _env("RTSP_TARGET_HOST")
        or _env("MTX_SOURCE_HOST")
    )


class Iperf3Client:
    """iperf3 -c -R supervisor. Stopped until start() unless IPERF_AUTOSTART=1."""

    def __init__(self) -> None:
        self.port = int(_env("IPERF_PORT", "5201") or "5201")
        self.port_count = max(1, int(_env("IPERF_PORT_COUNT", "8") or "8"))
        self.autostart = env_flag("IPERF_AUTOSTART", "0")
        self.log = LogBuffer(kind="iperf", maxlen=int(_env("IPERF_LOG_MAX", "400") or "400"))
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._wanted = False
        self._wake = threading.Event()
        self._started = False
        self._restart_pending = False
        self.cfg: dict[str, Any] = {
            "parallel": int(_env("IPERF_PARALLEL", "5") or "5"),
            "bandwidth": _env("IPERF_BANDWIDTH", "10M") or "10M",
            "time": _env("IPERF_TIME", "0") or "0",
            "interval": _env("IPERF_INTERVAL", "1") or "1",
            "port": self.port,
        }
        self.cfg["args"] = _env("IPERF_ARGS") or self._default_args_unlocked()
        self.state: dict[str, Any] = {
            "running": False,
            "wanted": False,
            "autostart": self.autostart,
            "pid": None,
            "cmd": [],
            "mbits_per_second": None,
            "error": "",
            "args": self.cfg["args"],
            "config": dict(self.cfg),
        }

    def start_supervisor(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(
            target=self._supervisor, daemon=True, name="iperf3-supervisor"
        ).start()
        if self.autostart:
            self.set_wanted(True)
            self.log.append("iperf3 autostart enabled")
        else:
            self.log.append("iperf3 stopped (start from console to add extra load)")

    def set_wanted(self, wanted: bool) -> None:
        with self._lock:
            self._wanted = wanted
            self.state["wanted"] = wanted
        self._wake.set()
        if not wanted:
            self._kill()

    def _default_args_unlocked(self) -> str:
        host = iperf_host() or "127.0.0.1"
        return (
            f"-c {host} -p {self.cfg.get('port', self.port)} -R "
            f"-P {self.cfg['parallel']} -b {self.cfg['bandwidth']} "
            f"-t {self.cfg['time']} -i {self.cfg['interval']} --forceflush"
        )

    @staticmethod
    def _parse_user_args(raw: str) -> list[str]:
        try:
            tokens = shlex.split(raw or "")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid iperf3 arguments: {exc}"
            ) from exc
        if tokens and os.path.basename(tokens[0]) == "iperf3":
            tokens = tokens[1:]
        return tokens

    @staticmethod
    def _flag_value(tokens: list[str], flag: str) -> Optional[str]:
        if flag not in tokens:
            return None
        i = tokens.index(flag)
        if i + 1 < len(tokens) and not str(tokens[i + 1]).startswith("-"):
            return tokens[i + 1]
        return None

    @staticmethod
    def _set_flag(tokens: list[str], flag: str, value: str) -> list[str]:
        out = list(tokens)
        if flag in out:
            i = out.index(flag)
            if i + 1 < len(out) and not str(out[i + 1]).startswith("-"):
                out[i + 1] = value
            else:
                out.insert(i + 1, value)
        else:
            out.extend([flag, value])
        return out

    def _sync_cfg_from_tokens(self, tokens: list[str]) -> None:
        parallel = self._flag_value(tokens, "-P")
        if parallel and parallel.isdigit():
            self.cfg["parallel"] = int(parallel)
        bandwidth = self._flag_value(tokens, "-b")
        if bandwidth:
            self.cfg["bandwidth"] = bandwidth
        time_s = self._flag_value(tokens, "-t")
        if time_s is not None:
            self.cfg["time"] = time_s
        interval = self._flag_value(tokens, "-i")
        if interval:
            self.cfg["interval"] = interval
        port = self._flag_value(tokens, "-p")
        if port and port.isdigit():
            self.cfg["port"] = int(port)

    def _store_state_cfg(self) -> None:
        self.state["config"] = dict(self.cfg)
        self.state["args"] = self.cfg.get("args") or ""

    def apply_cfg(self, body: IperfControl) -> None:
        with self._lock:
            if body.args is not None:
                raw = str(body.args).strip()
                tokens = self._parse_user_args(raw)
                if not tokens:
                    raw = self._default_args_unlocked()
                    tokens = self._parse_user_args(raw)
                self.cfg["args"] = raw
                self._sync_cfg_from_tokens(tokens)
            else:
                changed = False
                if body.parallel is not None:
                    self.cfg["parallel"] = int(body.parallel)
                    changed = True
                if body.bandwidth:
                    self.cfg["bandwidth"] = str(body.bandwidth).strip()
                    changed = True
                if body.time is not None and str(body.time).strip() != "":
                    self.cfg["time"] = str(body.time).strip()
                    changed = True
                if changed:
                    self.cfg["args"] = self._default_args_unlocked()
            self._store_state_cfg()

    def control(self, body: IperfControl) -> dict[str, Any]:
        action = (body.action or "").strip().lower()
        if action not in ("start", "stop", "restart", "update"):
            raise HTTPException(
                status_code=400, detail="action must be start|stop|restart|update"
            )
        if action in ("start", "restart", "update"):
            self.apply_cfg(body)
        if action == "stop":
            self.set_wanted(False)
            self.log.append("control: stop")
        elif action == "update":
            self.log.append("control: update " + (self.cfg.get("args") or ""))
            with self._lock:
                wanted = self._wanted
            if wanted:
                self._restart_pending = True
                self._kill()
                self._wake.set()
        elif action == "start":
            with self._lock:
                was_wanted = self._wanted
                self._wanted = True
                self.state["wanted"] = True
            self.log.append("control: start " + (self.cfg.get("args") or ""))
            if was_wanted:
                self._restart_pending = True
                self._kill()
            self._wake.set()
        else:
            self.set_wanted(False)
            time.sleep(0.3)
            self.set_wanted(True)
            self.log.append("control: restart")
        return {"ok": True, **self.snapshot()}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **dict(self.state),
                "config": dict(self.cfg),
                "args": self.cfg.get("args") or "",
                "log": self.log.snapshot(),
            }

    def _cmd(self, port: Optional[int] = None) -> list[str]:
        host = iperf_host() or "127.0.0.1"
        with self._lock:
            if port is not None:
                self.cfg["port"] = int(port)
                self._store_state_cfg()
            cfg = dict(self.cfg)
        raw = (cfg.get("args") or "").strip() or self._default_args_unlocked()
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = shlex.split(self._default_args_unlocked())
        if tokens and os.path.basename(tokens[0]) == "iperf3":
            tokens = tokens[1:]
        if "-c" not in tokens:
            tokens = self._set_flag(tokens, "-c", host)
        tokens = self._set_flag(tokens, "-p", str(cfg.get("port") or self.port))
        if "--forceflush" not in tokens:
            tokens.append("--forceflush")
        cmd = ["iperf3"] + tokens
        bind_flags = ("-B", "--bind", "--bind-dev")
        if iperf_bind_args is not None and not any(f in cmd for f in bind_flags):
            cmd.extend(iperf_bind_args())
        return cmd

    def _next_port(self, port: int) -> int:
        return self.port + ((int(port) - self.port + 1) % self.port_count)

    def _kill(self) -> None:
        with self._lock:
            proc = self._proc
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
        with self._lock:
            if self._proc is proc:
                self._proc = None
                self.state["running"] = False
                self.state["pid"] = None

    def _append_line(self, line: str) -> None:
        text = line.rstrip("\n\r")
        if not text:
            return
        mbps = parse_mbps(text)
        self.log.append(text)
        with self._lock:
            parallel = int(self.cfg.get("parallel") or 1)
            if mbps is not None and (SUM_RE.search(text) or parallel <= 1):
                self.state["mbits_per_second"] = mbps

    def _supervisor(self) -> None:
        backoff = 2.0
        while True:
            with self._lock:
                wanted = self._wanted
            if not wanted:
                self._kill()
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue

            if detect_to_server_iface is not None and not detect_to_server_iface():
                self.log.append("waiting for 5G to-server iface before iperf3")
                time.sleep(1.0)
                continue
            if pin_to_server is not None:
                pin_to_server()
            if not iperf_host():
                self.log.append("IPERF_HOST / TARGET_SERVER_IP not set")
                time.sleep(5.0)
                continue

            cmd = self._cmd()
            self.log.append("starting: " + " ".join(cmd))
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
                self.log.append("iperf3 binary not found")
                with self._lock:
                    self.state["running"] = False
                    self.state["error"] = "iperf3 not found"
                time.sleep(10)
                continue
            except OSError as exc:
                self.log.append(f"iperf3 spawn failed: {exc}")
                with self._lock:
                    self.state["running"] = False
                    self.state["error"] = str(exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            with self._lock:
                self._proc = proc
                self.state["running"] = True
                self.state["pid"] = proc.pid
                self.state["cmd"] = cmd
                self.state["error"] = ""
                self.state["config"] = dict(self.cfg)

            assert proc.stdout is not None
            for line in proc.stdout:
                with self._lock:
                    still = self._wanted
                if not still:
                    break
                low = line.lower()
                if "busy running a test" in low or "server is busy" in low:
                    busy = True
                self._append_line(line)

            if not self._wanted:
                self._kill()
                self.log.append("iperf3 stopped by control")
                backoff = 2.0
                continue

            rc = proc.wait()
            with self._lock:
                self._proc = None
                self.state["running"] = False
                self.state["pid"] = None
                cur_port = int(self.cfg.get("port") or self.port)
                restart_pending = self._restart_pending
                self._restart_pending = False
            if restart_pending:
                backoff = 0.15
                continue
            if busy or (rc != 0 and time.time() - started < 3):
                nxt = self._next_port(cur_port)
                self.log.append(f"iperf3 :{cur_port} busy/failed; trying :{nxt}")
                self._cmd(nxt)
                backoff = 0.4
                if self._wake.wait(timeout=backoff):
                    self._wake.clear()
                continue
            if time.time() - started > 5:
                backoff = 2.0
            with self._lock:
                still = self._wanted
            if not still:
                continue
            self.log.append(f"iperf3 exited rc={rc}; retry in {backoff:.0f}s")
            if self._wake.wait(timeout=backoff):
                self._wake.clear()
            backoff = min(backoff * 2, 30.0)


def attach_iperf_routes(app: FastAPI, client: Iperf3Client) -> None:
    @app.post("/api/iperf")
    def iperf(body: IperfControl) -> dict:
        return client.control(body)

    @app.post("/api/iperf/start")
    def iperf_start(body: Optional[IperfControl] = None) -> dict:
        payload = body or IperfControl(action="start")
        return client.control(
            IperfControl(
                action="start",
                args=payload.args,
                parallel=payload.parallel,
                bandwidth=payload.bandwidth,
                time=payload.time,
            )
        )

    @app.post("/api/iperf/stop")
    def iperf_stop() -> dict:
        return client.control(IperfControl(action="stop"))

    @app.post("/api/iperf/update")
    def iperf_update(body: Optional[IperfControl] = None) -> dict:
        payload = body or IperfControl(action="update")
        return client.control(
            IperfControl(
                action="update",
                args=payload.args,
                parallel=payload.parallel,
                bandwidth=payload.bandwidth,
                time=payload.time,
            )
        )


def attach_app_routes(
    app: FastAPI,
    *,
    start_fn: Callable[[], Any],
    stop_fn: Callable[[], Any],
    log: Optional[LogBuffer] = None,
) -> None:
    def _run(action: str) -> dict:
        action = (action or "").strip().lower()
        if action not in ("start", "stop", "restart"):
            raise HTTPException(status_code=400, detail="action must be start|stop|restart")
        if action == "stop":
            if log is not None:
                log.append("control: stop")
            stop_fn()
        elif action == "start":
            if log is not None:
                log.append("control: start")
            start_fn()
        else:
            if log is not None:
                log.append("control: restart")
            stop_fn()
            time.sleep(0.2)
            start_fn()
        return {"ok": True, "action": action}

    @app.post("/api/app")
    def app_ctrl(body: AppControl) -> dict:
        return _run(body.action)

    @app.post("/api/app/start")
    def app_start() -> dict:
        return _run("start")

    @app.post("/api/app/stop")
    def app_stop() -> dict:
        return _run("stop")


def app_fields(
    *,
    running: bool,
    wanted: bool,
    log: LogBuffer,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    out = {
        "running": bool(running),
        "wanted": bool(wanted),
        "log": log.snapshot(),
    }
    if extra:
        out.update(extra)
    return out
