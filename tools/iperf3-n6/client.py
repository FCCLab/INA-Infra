#!/usr/bin/env python3
"""
iperf3 DL UDP client → N3 server (single port, -P parallel streams).

Default:
  iperf3 -c <server> -p <port> -u -R -P 5 -b 50M -t <duration> -i 1

-R = reverse (server→client) = downlink toward UE.
-P = parallel streams on the same server port (one test, not N one-off ports).

Writes aggregate receive throughput to InfluxDB every REPORT_INTERVAL as
role=client_agg (prefers iperf3 [SUM] lines). Stdout throttled to LOG_INTERVAL.
"""

from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional, Tuple

from influx_writer import InfluxWriter, env, mbps_from_bits

# Per-stream or SUM interval lines, e.g.:
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


def parse_interval_line(line: str) -> Optional[Tuple[float, bool]]:
    """Return (bits_per_sec, is_sum) or None."""
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    mult = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return value * mult, bool(SUM_RE.search(line))


class ClientRunner:
    def __init__(
        self,
        server: str,
        port: int,
        parallel: int,
        bandwidth: str,
        duration: int,
        sample_interval_s: float,
        log_interval_s: float,
        bind_dev: str,
        port_min: int,
        port_max: int,
        stop: threading.Event,
    ) -> None:
        self.server = server
        self.port = port
        self.parallel = parallel
        self.bandwidth = bandwidth
        self.duration = duration
        self.sample_interval_s = sample_interval_s
        self.log_interval_s = max(log_interval_s, 0.0)
        self.bind_dev = bind_dev
        self.port_min = port_min
        self.port_max = port_max
        self.stop = stop
        self._proc: Optional[subprocess.Popen[str]] = None
        self._last_log = 0.0
        self.latest_bps: Optional[float] = None
        self.lock = threading.Lock()

    def _cmd(self) -> List[str]:
        cmd = [
            "iperf3",
            "-c",
            self.server,
            "-p",
            str(self.port),
            "-u",
            "-R",
            "-P",
            str(self.parallel),
            "-b",
            self.bandwidth,
            "-i",
            str(self.sample_interval_s),
            "--forceflush",
        ]
        if self.duration > 0:
            cmd.extend(["-t", str(self.duration)])
        else:
            cmd.extend(["-t", "0"])
        if self.bind_dev:
            cmd.extend(["--bind-dev", self.bind_dev])
        return cmd

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _should_log(self) -> bool:
        if self.log_interval_s <= 0:
            return True
        now = time.monotonic()
        if now - self._last_log >= self.log_interval_s:
            self._last_log = now
            return True
        return False

    def _advance_port(self, reason: str) -> None:
        nxt = self.port + 1
        if nxt > self.port_max:
            nxt = self.port_min
        print(f"[client] {reason} on {self.port}, trying {nxt}", flush=True)
        self.port = nxt

    def _wait_bind_dev(self) -> None:
        if not self.bind_dev:
            return
        while not self.stop.is_set():
            try:
                out = subprocess.check_output(
                    ["ip", "-4", "addr", "show", "dev", self.bind_dev],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                if "inet " in out:
                    return
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            print(f"[client] waiting for {self.bind_dev} ...", flush=True)
            if self.stop.wait(2):
                return

    def run_forever(self) -> None:
        while not self.stop.is_set():
            if self.bind_dev:
                self._wait_bind_dev()
                if self.stop.is_set():
                    break

            cmd = self._cmd()
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                print("iperf3 binary not found", file=sys.stderr)
                time.sleep(5)
                continue
            except Exception as exc:
                if self.bind_dev and "--bind-dev" in cmd:
                    print(f"[client] retry without --bind-dev ({exc})", flush=True)
                    cmd = [
                        c
                        for i, c in enumerate(cmd)
                        if c != "--bind-dev" and not (i > 0 and cmd[i - 1] == "--bind-dev")
                    ]
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                else:
                    raise

            assert self._proc.stdout is not None
            print(f"[client] start {' '.join(cmd)}", flush=True)
            self._last_log = 0.0
            fail_reason: Optional[str] = None
            zero_sum_streak = 0
            interval_sum = 0.0
            interval_n = 0
            try:
                for line in self._proc.stdout:
                    if self.stop.is_set():
                        break
                    line = line.rstrip()
                    low = line.lower()
                    if "server is busy" in low:
                        fail_reason = "server busy"
                    elif "unable to connect" in low or "connection refused" in low:
                        fail_reason = "connect failed"
                    elif "no such device" in low:
                        fail_reason = "no such device"
                    elif "idle timeout" in low:
                        fail_reason = "idle timeout"

                    parsed = parse_interval_line(line)
                    is_sample = parsed is not None
                    if line and (not is_sample or self._should_log()):
                        print(f"[client] {line}", flush=True)
                    if parsed is None:
                        if fail_reason:
                            break
                        continue
                    bps, is_sum = parsed
                    if is_sum:
                        with self.lock:
                            self.latest_bps = bps
                        interval_sum = 0.0
                        interval_n = 0
                        if bps <= 0:
                            zero_sum_streak += 1
                            if zero_sum_streak >= 5:
                                fail_reason = "zero throughput"
                                break
                        else:
                            zero_sum_streak = 0
                    else:
                        interval_sum += bps
                        interval_n += 1
                        if interval_n >= self.parallel:
                            with self.lock:
                                self.latest_bps = interval_sum
                            interval_sum = 0.0
                            interval_n = 0
            finally:
                self._kill()
                self._proc = None

            if fail_reason == "no such device":
                # PDU iface gone (e.g. CU-UP restart) — wait, do not burn ports.
                print(f"[client] {fail_reason}; waiting for {self.bind_dev or 'network'}", flush=True)
                if not self.stop.wait(2):
                    continue
                break

            if fail_reason in ("server busy", "connect failed", "idle timeout", "zero throughput"):
                self._advance_port(fail_reason)
                if not self.stop.wait(0.5):
                    continue

            if self.duration > 0:
                break
            if not self.stop.wait(1):
                continue


def main() -> int:
    ap = argparse.ArgumentParser(description="iperf3 DL UDP client (-P parallel on one port)")
    ap.add_argument("--server", default=env("IPERF_SERVER", ""), help="UPF N3 iperf3 server IP")
    ap.add_argument("--port", type=int, default=int(env("PORT_START", env("PORT", "5210"))))
    ap.add_argument(
        "--parallel",
        "-P",
        type=int,
        default=int(env("PARALLEL", env("PROCESSES", "5"))),
        help="iperf3 -P streams on one port (env PARALLEL or PROCESSES)",
    )
    ap.add_argument(
        "--bandwidth",
        default=env("BANDWIDTH", "50M"),
        help="Per-stream UDP -b (total ≈ bandwidth × parallel)",
    )
    ap.add_argument("--duration", type=int, default=int(env("DURATION", "0")), help="0 = forever")
    ap.add_argument(
        "--interval",
        type=float,
        default=float(env("REPORT_INTERVAL", "1")),
        help="iperf3 -i / Influx sample period (seconds)",
    )
    ap.add_argument(
        "--log-interval",
        type=float,
        default=float(env("LOG_INTERVAL", "5")),
        help="stdout log period (seconds); 0 = log every sample",
    )
    ap.add_argument("--bind-dev", default=env("BIND_DEV", ""), help="e.g. oaitun_ue1")
    ap.add_argument("--testbed", default=env("TESTBED", "oai-benchmark"))
    ap.add_argument(
        "--port-max",
        type=int,
        default=int(env("PORT_MAX", "5326")),
        help="Upper bound when skipping a busy server port",
    )
    args = ap.parse_args()

    if not args.server:
        raise SystemExit("IPERF_SERVER / --server is required (UPF N3 address)")
    if args.parallel < 1:
        raise SystemExit("--parallel must be >= 1")

    print(
        f"iperf3-n6 client → {args.server}:{args.port} "
        f"-P {args.parallel} -b {args.bandwidth}/stream (DL UDP -R) "
        f"influx={args.interval}s log={args.log_interval}s",
        flush=True,
    )

    influx = InfluxWriter()
    stop = threading.Event()
    last_agg_log = 0.0

    def _stop(*_a: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    runner = ClientRunner(
        args.server,
        args.port,
        args.parallel,
        args.bandwidth,
        args.duration,
        args.interval,
        args.log_interval,
        args.bind_dev,
        args.port,
        args.port_max,
        stop,
    )
    thread = threading.Thread(target=runner.run_forever, daemon=True, name="iperf3-client")
    thread.start()

    tags = {"testbed": args.testbed}
    while not stop.wait(args.interval):
        with runner.lock:
            bps = runner.latest_bps
        if bps is None:
            continue
        try:
            influx.write(
                {
                    "bits_per_second": float(bps),
                    "mbits_per_second": mbps_from_bits(bps),
                    "streams": float(args.parallel),
                },
                tags={
                    "role": "client_agg",
                    "server": args.server,
                    "port": str(runner.port),
                    **tags,
                },
            )
        except Exception as exc:  # noqa: BLE001
            now = time.monotonic()
            if args.log_interval <= 0 or now - last_agg_log >= args.log_interval:
                print(f"agg influx error: {exc}", file=sys.stderr, flush=True)
                last_agg_log = now

        now = time.monotonic()
        if args.log_interval <= 0 or now - last_agg_log >= args.log_interval:
            print(
                f"[agg] total={mbps_from_bits(bps):.1f} Mbit/s "
                f"(-P {args.parallel} port={runner.port})",
                flush=True,
            )
            last_agg_log = now

        if args.duration > 0 and not thread.is_alive():
            break

    stop.set()
    runner._kill()
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
