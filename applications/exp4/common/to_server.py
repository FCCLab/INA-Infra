#!/usr/bin/env python3
"""UE data-plane: detect the to-server (5G) iface and pin app-server routes.

paper/exp4 only names ifaces (TO_SERVER_IFACE, CONSOLE_IFACE) and the app-server
IP. This module is the built-in behaviour for every Exp4 client:

  - discover the live to-server iface (configured name, else first oaitun* with
    IPv4; sim-5G uses net1)
  - install /32 routes so 10.1.137.21N is not stolen by net2's connected /24
  - bind TCP onto that iface's IPv4 so sockets cannot fall back to console

Throughput (RX counters) and latency probes must use detect_to_server_iface().
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time


_CONSOLE_IFACES = frozenset(
    {"net2", "eth0", "eth1", "lo", "rf", "rfsim0", "docker0"}
)
_PIN_LOCK = threading.Lock()
_PIN_WATCH_STARTED = False


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def no5g() -> bool:
    return _env("SCHEME_ID") == "exp4-no5g" or _env("EXP4_NO5G").lower() in (
        "1",
        "true",
        "yes",
    )


def is_console_iface(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n or n in _CONSOLE_IFACES:
        return True
    console = _env("CONSOLE_IFACE").lower()
    return bool(console) and n == console


def iface_ipv4(name: str) -> str:
    if not name:
        return ""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    for tok in out.split():
        if tok.count(".") == 3 and "/" in tok:
            return tok.split("/", 1)[0]
    return ""


def iface_rx_bytes(name: str) -> int | None:
    try:
        return int(
            open(f"/sys/class/net/{name}/statistics/rx_bytes", encoding="utf-8").read()
        )
    except (OSError, ValueError):
        return None


def _net_names() -> list[str]:
    try:
        return sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []


def configured_to_server_name() -> str:
    if no5g():
        return _env("TO_SERVER_IFACE") or _env("PDU_IFACE") or _env("BIND_DEV") or "net1"
    return (
        _env("TO_SERVER_IFACE")
        or _env("PDU_IFACE")
        or _env("BIND_DEV")
        or "oaitun_ue1"
    )


def detect_to_server_iface() -> str | None:
    """Live 5G / sim-5G iface with IPv4, or None while PDU is down."""
    names: list[str] = []
    want = configured_to_server_name()
    if want:
        names.append(want)
    if not no5g():
        for n in _net_names():
            if n.startswith("oaitun") and n not in names:
                names.append(n)
    elif "net1" not in names:
        names.append("net1")
    for name in names:
        if is_console_iface(name):
            continue
        if not no5g() and not name.startswith("oaitun"):
            continue
        if iface_ipv4(name):
            return name
    return None


def path_ready() -> bool:
    return detect_to_server_iface() is not None


def bind_source_ip() -> str:
    """IPv4 to bind sockets onto so traffic leaves the to-server iface."""
    src = _env("SIM5G_IP") or _env("MULTUS_IP")
    if src:
        return src
    iface = detect_to_server_iface()
    return iface_ipv4(iface) if iface else ""


def connect_tcp(host: str, port: int, timeout: float = 30.0) -> socket.socket:
    src = bind_source_ip()
    if not src:
        raise OSError("to-server iface has no IPv4 (PDU not ready)")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.bind((src, 0))
    sock.connect((host, int(port)))
    return sock


def _is_console_host(host: str) -> bool:
    h = (host or "").strip()
    if not h:
        return True
    if h in ("10.1.137.1", _env("MULTUS_GW"), _env("CONSOLE_IP"), _env("GW")):
        return True
    return False


def app_server_hosts() -> list[str]:
    hosts: list[str] = []
    for key in (
        "TARGET_SERVER_IP",
        "IPERF_HOST",
        "SFTP_HOST",
        "BROKER_HOST",
        "SERVER_HOST",
        "SERVER_RTSP_HOST",
        "MTX_SOURCE_HOST",
        "RTSP_TARGET_HOST",
        "PDU_ROUTE_HOSTS",
    ):
        raw = _env(key).replace(",", " ")
        for h in raw.split():
            h = h.strip()
            if h and h not in hosts and not _is_console_host(h):
                hosts.append(h)
    return hosts


def pin_to_server(iface: str | None = None) -> str | None:
    """Install app-server /32s via the to-server iface. Returns the iface used."""
    live = iface or detect_to_server_iface()
    if not live:
        return None
    src = iface_ipv4(live)
    if not src:
        return None
    ok = False
    for host in app_server_hosts():
        cmd = ["ip", "route", "replace", f"{host}/32", "dev", live]
        if src:
            cmd.extend(["src", src])
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=5)
        except Exception:
            continue
        if r.returncode == 0:
            ok = True
            continue
        try:
            r = subprocess.run(
                ["ip", "route", "replace", f"{host}/32", "dev", live],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            continue
        if r.returncode == 0:
            ok = True
    return live if ok or not app_server_hosts() else live


_IPERF_BIND_DEV: bool | None = None


def _iperf_has_bind_dev() -> bool:
    global _IPERF_BIND_DEV
    if _IPERF_BIND_DEV is not None:
        return _IPERF_BIND_DEV
    try:
        out = subprocess.check_output(
            ["iperf3", "--help"],
            text=True,
            timeout=3,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        _IPERF_BIND_DEV = False
        return False
    _IPERF_BIND_DEV = "--bind-dev" in out
    return _IPERF_BIND_DEV


def iperf_bind_args() -> list[str]:
    iface = detect_to_server_iface()
    if not iface:
        return []
    args: list[str] = []
    src = iface_ipv4(iface)
    if src:
        args.extend(["-B", src])
    if _iperf_has_bind_dev():
        args.extend(["--bind-dev", iface])
    return args


def ensure_pin_watch(period_s: float = 2.0) -> None:
    """Re-pin /32s after PDU flaps. Safe to call from every backend."""
    global _PIN_WATCH_STARTED
    with _PIN_LOCK:
        if _PIN_WATCH_STARTED:
            return
        _PIN_WATCH_STARTED = True

    def _loop() -> None:
        while True:
            try:
                pin_to_server()
            except Exception:
                pass
            time.sleep(max(0.5, period_s))

    threading.Thread(target=_loop, daemon=True, name="exp4-to-server-pin").start()
