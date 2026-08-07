#!/usr/bin/env python3
"""
iperf3 multi-port server bound to UPF N3 (RAN-facing Multus).

Spawns PORT_COUNT iperf3 servers (default 126) starting at PORT_START (5201).
Each server is one-off (-1): after a client finishes (or times out) the listener
restarts. iperf3 --idle-timeout / --rcv-timeout are set when available, and a
Python watchdog kills the process after IDLE_TIMEOUT seconds with no interval
output once a client has been accepted (half-open UE paths often leave ESTAB
control sockets that iperf3 alone will not drop).

Writes aggregate (sum of active ports) send throughput to InfluxDB every
REPORT_INTERVAL as role=server_agg. Per-port samples stay local.
"""

from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

from influx_writer import InfluxWriter, env, mbps_from_bits

# Per-stream or SUM interval lines ( -P parallel reports both):
# [  5]   0.00-1.00   sec  5.96 MBytes  50.0 Mbits/sec
# [SUM]   0.00-1.00   sec  29.8 MBytes   250 Mbits/sec
INTERVAL_RE = re.compile(
    r"\[(?:\s*\d+|SUM)\]\s+"
    r"([\d.]+)-([\d.]+)\s+sec\s+"
    r".*?\s+"
    r"([\d.]+)\s+([KMG])?bits/sec",
    re.IGNORECASE,
)
SUM_RE = re.compile(r"\[SUM\]", re.IGNORECASE)


def parse_interval_line(line: str) -> Optional[tuple]:
    """Return (bits_per_sec, is_sum) or None."""
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    mult = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return value * mult, bool(SUM_RE.search(line))


def iface_ipv4(iface: str) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", iface],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for tok in out.split():
        if "/" in tok and tok[0].isdigit():
            return tok.split("/", 1)[0]
    return None


def wait_n6_ip(iface: str, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ip = iface_ipv4(iface)
        if ip:
            return ip
        time.sleep(1)
    raise SystemExit(f"timeout waiting for IPv4 on {iface}")


def iperf3_supports(flag: str) -> bool:
    try:
        help_txt = subprocess.check_output(["iperf3", "--help"], text=True, stderr=subprocess.STDOUT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return flag in help_txt


class PortServer(threading.Thread):
    def __init__(
        self,
        bind_ip: str,
        port: int,
        idle_timeout_s: int,
        interval_s: float,
        stop: threading.Event,
        latest: Dict[int, float],
        lock: threading.Lock,
    ) -> None:
        super().__init__(daemon=True, name=f"iperf3-{port}")
        self.bind_ip = bind_ip
        self.port = port
        self.idle_timeout_s = idle_timeout_s
        self.interval_s = interval_s
        self.stop = stop
        self.latest = latest
        self.lock = lock
        self._proc: Optional[subprocess.Popen[str]] = None
        self._has_idle = iperf3_supports("--idle-timeout")
        self._has_rcv = iperf3_supports("--rcv-timeout")

    def _cmd(self) -> List[str]:
        cmd = [
            "iperf3",
            "-s",
            "-1",  # one connection then exit (restarted by loop)
            "-B",
            self.bind_ip,
            "-p",
            str(self.port),
            "-i",
            str(self.interval_s),
            "--forceflush",
        ]
        if self._has_idle and self.idle_timeout_s > 0:
            cmd.extend(["--idle-timeout", str(self.idle_timeout_s)])
        # Milliseconds; helps drop stuck active tests when no data arrives.
        if self._has_rcv and self.idle_timeout_s > 0:
            cmd.extend(["--rcv-timeout", str(self.idle_timeout_s * 1000)])
        return cmd

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _clear(self) -> None:
        with self.lock:
            self.latest.pop(self.port, None)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    self._cmd(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                print("iperf3 binary not found", file=sys.stderr)
                time.sleep(5)
                continue

            assert self._proc.stdout is not None
            watchdog: Optional[threading.Timer] = None
            accepted = False

            def _arm_watchdog(reason: str = "stale") -> None:
                """Kill after silence once a client was accepted (not while listening)."""
                nonlocal watchdog
                if self.idle_timeout_s <= 0 or not accepted:
                    return
                if watchdog:
                    watchdog.cancel()

                def _fire() -> None:
                    print(
                        f"[p{self.port}] watchdog: no output for "
                        f"{self.idle_timeout_s}s ({reason}), killing",
                        flush=True,
                    )
                    self._kill()

                watchdog = threading.Timer(self.idle_timeout_s, _fire)
                watchdog.daemon = True
                watchdog.start()

            # With -P N, iperf3 prints per-stream then [SUM]. Prefer [SUM] for totals.
            seen_sum = False
            try:
                for line in self._proc.stdout:
                    if self.stop.is_set():
                        break
                    line = line.rstrip()
                    if line:
                        print(f"[p{self.port}] {line}", flush=True)
                    low = line.lower()
                    if (not accepted) and (
                        "accepted connection" in low or "connected to" in low
                    ):
                        accepted = True
                        _arm_watchdog("post-accept")
                    parsed = parse_interval_line(line)
                    if parsed is None:
                        continue
                    bps, is_sum = parsed
                    accepted = True
                    _arm_watchdog("interval")
                    if is_sum:
                        seen_sum = True
                        with self.lock:
                            self.latest[self.port] = bps
                    elif not seen_sum:
                        # Single-stream tests never emit SUM.
                        with self.lock:
                            self.latest[self.port] = bps
            finally:
                if watchdog:
                    watchdog.cancel()
                self._kill()
                self._proc = None
                self._clear()

            if not self.stop.wait(0.2):
                continue


def main() -> int:
    ap = argparse.ArgumentParser(description="Multi-port iperf3 server on UPF N6")
    bind_iface = env("BIND_IFACE") or env("N6_IFACE") or "n3"
    ap.add_argument(
        "--iface",
        default=bind_iface,
        help="Bind interface (default n3; BIND_IFACE / legacy N6_IFACE)",
    )
    ap.add_argument("--bind", default=env("BIND_IP", ""), help="Override bind IP (else detect)")
    ap.add_argument("--port-start", type=int, default=int(env("PORT_START", "5201")))
    ap.add_argument("--port-count", type=int, default=int(env("PORT_COUNT", "126")))
    ap.add_argument("--idle-timeout", type=int, default=int(env("IDLE_TIMEOUT", "30")))
    ap.add_argument("--interval", type=float, default=float(env("REPORT_INTERVAL", "1")))
    ap.add_argument("--wait-ip-timeout", type=float, default=float(env("WAIT_IP_TIMEOUT", "120")))
    ap.add_argument(
        "--log-interval",
        type=float,
        default=float(env("LOG_INTERVAL", "5")),
        help="stdout aggregate log period (seconds); 0 = every sample",
    )
    ap.add_argument("--testbed", default=env("TESTBED", "oai-benchmark"))
    args = ap.parse_args()

    bind_ip = args.bind or wait_n6_ip(args.iface, args.wait_ip_timeout)
    ports = list(range(args.port_start, args.port_start + args.port_count))
    print(
        f"iperf3-n6 server bind={bind_ip} ports={ports[0]}-{ports[-1]} "
        f"idle_timeout={args.idle_timeout}s interval={args.interval}s "
        f"(Influx role=server_agg)",
        flush=True,
    )

    influx = InfluxWriter()
    stop = threading.Event()
    latest: Dict[int, float] = {}
    lock = threading.Lock()
    last_agg_log = 0.0
    last_hb = 0.0

    def _stop(*_a: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    tags = {"testbed": args.testbed, "iface": args.iface}
    workers = [
        PortServer(bind_ip, p, args.idle_timeout, args.interval, stop, latest, lock)
        for p in ports
    ]
    for w in workers:
        w.start()

    while not stop.wait(args.interval):
        with lock:
            vals = dict(latest)
        if vals:
            total = float(sum(vals.values()))
            try:
                influx.write(
                    {
                        "bits_per_second": total,
                        "mbits_per_second": mbps_from_bits(total),
                        "streams": float(len(vals)),
                    },
                    tags={"role": "server_agg", "bind": bind_ip, **tags},
                )
            except Exception as exc:  # noqa: BLE001
                now = time.monotonic()
                if args.log_interval <= 0 or now - last_agg_log >= args.log_interval:
                    print(f"agg influx error: {exc}", file=sys.stderr, flush=True)
                    last_agg_log = now

            now = time.monotonic()
            if args.log_interval <= 0 or now - last_agg_log >= args.log_interval:
                print(
                    f"[agg] total={mbps_from_bits(total):.1f} Mbit/s "
                    f"({len(vals)} active ports)",
                    flush=True,
                )
                last_agg_log = now

        now = time.monotonic()
        if now - last_hb >= 10.0:
            last_hb = now
            try:
                influx.write(
                    {"up": 1.0, "port_count": float(len(ports))},
                    tags={"role": "server", "bind": bind_ip, **tags},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"heartbeat influx error: {exc}", file=sys.stderr, flush=True)

    for w in workers:
        w.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
