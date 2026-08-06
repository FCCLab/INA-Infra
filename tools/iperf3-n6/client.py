#!/usr/bin/env python3
"""
iperf3 DL UDP client farm → N6 server.

Starts N processes (default 5), each:
  iperf3 -c <server> -p <port> -u -R -b 50M -t <duration> -i 1

-R = reverse (server→client) = downlink toward UE.
Writes aggregate (total) receive throughput to InfluxDB every
REPORT_INTERVAL (default 1s) as role=client_agg. Stdout logging is
throttled to LOG_INTERVAL (default 5s).
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

INTERVAL_RE = re.compile(
    r"\[\s*\d+\]\s+"
    r"([\d.]+)-([\d.]+)\s+sec\s+"
    r".*?\s+"
    r"([\d.]+)\s+([KMG])?bits/sec",
    re.IGNORECASE,
)


def parse_bits_per_sec(line: str) -> Optional[float]:
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    mult = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return value * mult


class ClientWorker(threading.Thread):
    def __init__(
        self,
        index: int,
        server: str,
        port: int,
        bandwidth: str,
        duration: int,
        sample_interval_s: float,
        log_interval_s: float,
        bind_dev: str,
        influx: InfluxWriter,
        stop: threading.Event,
        extra_tags: Dict[str, str],
        latest: Dict[int, float],
        lock: threading.Lock,
    ) -> None:
        super().__init__(daemon=True, name=f"iperf3-c{index}")
        self.index = index
        self.server = server
        self.port = port
        self.bandwidth = bandwidth
        self.duration = duration
        self.sample_interval_s = sample_interval_s
        self.log_interval_s = max(log_interval_s, 0.0)
        self.bind_dev = bind_dev
        self.influx = influx
        self.stop = stop
        self.extra_tags = extra_tags
        self.latest = latest
        self.lock = lock
        self._proc: Optional[subprocess.Popen[str]] = None
        self._last_log = 0.0

    def _cmd(self) -> List[str]:
        cmd = [
            "iperf3",
            "-c",
            self.server,
            "-p",
            str(self.port),
            "-u",
            "-R",
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

    def run(self) -> None:
        while not self.stop.is_set():
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
                    print(f"[c{self.index}] retry without --bind-dev ({exc})", flush=True)
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
            print(f"[c{self.index}] start {' '.join(cmd)}", flush=True)
            self._last_log = 0.0
            try:
                for line in self._proc.stdout:
                    if self.stop.is_set():
                        break
                    line = line.rstrip()
                    bps = parse_bits_per_sec(line)
                    # Always log non-interval chatter (errors / summaries) sparsely:
                    # interval samples only print on LOG_INTERVAL.
                    is_sample = bps is not None
                    if line and (not is_sample or self._should_log()):
                        print(f"[c{self.index}] {line}", flush=True)
                    if bps is None:
                        continue
                    # Per-stream samples stay local; Influx only gets client_agg totals.
                    with self.lock:
                        self.latest[self.index] = bps
            finally:
                self._kill()
                self._proc = None

            if self.duration > 0:
                break
            if not self.stop.wait(1):
                continue


def main() -> int:
    ap = argparse.ArgumentParser(description="iperf3 DL UDP multi-process client")
    ap.add_argument("--server", default=env("IPERF_SERVER", ""), help="UPF N3 iperf3 server IP")
    ap.add_argument("--port-start", type=int, default=int(env("PORT_START", "5201")))
    ap.add_argument("--processes", type=int, default=int(env("PROCESSES", "5")))
    ap.add_argument("--bandwidth", default=env("BANDWIDTH", "50M"), help="Per-process UDP -b")
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
    args = ap.parse_args()

    if not args.server:
        raise SystemExit("IPERF_SERVER / --server is required (UPF N3 address)")

    ports = [args.port_start + i for i in range(args.processes)]
    print(
        f"iperf3-n6 client → {args.server} ports={ports} "
        f"bw={args.bandwidth}/stream processes={args.processes} (DL UDP -R) "
        f"influx={args.interval}s log={args.log_interval}s",
        flush=True,
    )

    influx = InfluxWriter()
    stop = threading.Event()
    latest: Dict[int, float] = {}
    lock = threading.Lock()
    last_agg_log = 0.0

    def _stop(*_a: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    tags = {"testbed": args.testbed}
    workers = [
        ClientWorker(
            i,
            args.server,
            ports[i],
            args.bandwidth,
            args.duration,
            args.interval,
            args.log_interval,
            args.bind_dev,
            influx,
            stop,
            tags,
            latest,
            lock,
        )
        for i in range(args.processes)
    ]
    for w in workers:
        w.start()

    # aggregate Influx every sample interval; log on LOG_INTERVAL
    while not stop.wait(args.interval):
        with lock:
            vals = dict(latest)
        if not vals:
            continue
        total = float(sum(vals.values()))
        try:
            influx.write(
                {
                    "bits_per_second": total,
                    "mbits_per_second": mbps_from_bits(total),
                    "streams": float(len(vals)),
                },
                tags={"role": "client_agg", "server": args.server, **tags},
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
                f"({len(vals)}/{args.processes} streams)",
                flush=True,
            )
            last_agg_log = now

        if args.duration > 0 and all(not w.is_alive() for w in workers):
            break

    stop.set()
    for w in workers:
        w.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
