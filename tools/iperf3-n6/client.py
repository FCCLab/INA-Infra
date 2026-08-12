#!/usr/bin/env python3
"""
iperf3 DL client → N3 server (single port, -P parallel streams).

Controlled by ina-infra over WebSocket (`/api/v1/ues/ws`):
  declare → welcome + desired → hot-restart iperf on protocol/action changes.

Desired controls protocol (udp|tcp), bandwidth, parallel, direction, etc.
Port is chosen locally (failover on busy server) — not from desired.

Default (UDP):
  iperf3 -c <server> -p <port> -u -R -P 5 -b 50M -t 0 -i 1

TCP: same without -u; -b omitted unless TCP_BANDWIDTH is set.

Control plane uses the pod network (INA_INFRA_API_URL). Data plane binds PDU
via --bind-dev (oaitun_ue1).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from influx_writer import InfluxWriter, env, mbps_from_bits

INTERVAL_RE = re.compile(
    r"\[(?:\s*\d+|SUM)\]\s+"
    r"([\d.]+)-([\d.]+)\s+sec\s+"
    r".*?\s+"
    r"([\d.]+)\s+([KMG])?bits/sec",
    re.IGNORECASE,
)
SUM_RE = re.compile(r"\[SUM\]", re.IGNORECASE)

CLIENT_VERSION = "ws-ctrl-7"


def parse_interval_line(line: str) -> Optional[Tuple[float, bool]]:
    m = INTERVAL_RE.search(line)
    if not m:
        return None
    value = float(m.group(3))
    unit = (m.group(4) or "").upper()
    mult = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1.0)
    return value * mult, bool(SUM_RE.search(line))


def normalize_protocol(raw: str) -> str:
    p = (raw or "udp").strip().lower()
    if p in ("tcp", "udp"):
        return p
    raise ValueError(f"protocol must be udp or tcp, got: {raw!r}")


def use_udp_bandwidth(bandwidth: str) -> str:
    b = (bandwidth or "").strip()
    return b if b and b.lower() not in ("0", "unlimited", "none", "-") else "50M"


def use_tcp_bandwidth(bandwidth: str) -> Optional[str]:
    b = (bandwidth or "").strip()
    if not b or b.lower() in ("0", "unlimited", "none", "-"):
        return None
    return b


def api_url_to_ws(api_url: str) -> str:
    raw = (api_url or "").strip()
    if not raw or raw == "-":
        return ""
    if raw.startswith("ws://") or raw.startswith("wss://"):
        base = raw.rstrip("/")
        if base.endswith("/ues/ws"):
            return base
        return base + "/api/v1/ues/ws"
    u = urlparse(raw)
    scheme = "wss" if u.scheme == "https" else "ws"
    host = u.netloc or u.path
    return f"{scheme}://{host}/api/v1/ues/ws"


def default_ue_id(cluster: str, namespace: str) -> str:
    host = env("HOSTNAME", socket.gethostname() or "ue")
    override = env("INA_UE_ID", "")
    if override:
        return override
    return f"{cluster}-{namespace}-{host}"


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
        protocol: str = "udp",
        tcp_bandwidth: str = "",
    ) -> None:
        self.server = server
        self.port = port
        self.parallel = parallel
        self.bandwidth = bandwidth
        self.tcp_bandwidth = tcp_bandwidth
        self.duration = duration
        self.sample_interval_s = sample_interval_s
        self.log_interval_s = max(log_interval_s, 0.0)
        self.bind_dev = bind_dev
        self.port_min = port_min
        self.port_max = port_max
        self.stop = stop
        self.protocol = normalize_protocol(protocol)
        self.reverse = True  # DL (-R); overridden by desired.reverse
        self.enabled = True
        self.reload = threading.Event()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._last_log = 0.0
        self.latest_bps: Optional[float] = None
        self.status = "idle"
        self.lock = threading.Lock()
        self.applied_generation = 0

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            bps = self.latest_bps
            return {
                "protocol": self.protocol,
                "status": self.status,
                "server": self.server,
                "port": self.port,
                "parallel": self.parallel,
                "bandwidth": self.bandwidth,
                "tcp_bandwidth": self.tcp_bandwidth,
                "reverse": self.reverse,
                "duration": self.duration,
                "interval": self.sample_interval_s,
                "mbits_per_second": mbps_from_bits(bps) if bps is not None else 0.0,
                "enabled": self.enabled,
                "applied_generation": self.applied_generation,
            }

    def apply_desired(self, desired: dict) -> Tuple[bool, str]:
        """Apply WS desired config; returns (changed, message)."""
        try:
            proto = normalize_protocol(desired.get("protocol") or self.protocol)
        except ValueError as exc:
            return False, str(exc)
        action = str(desired.get("action") or "start").strip().lower()
        if action not in ("start", "stop", "set"):
            action = "start"
        parallel = int(desired.get("parallel") or self.parallel)
        if parallel < 1:
            parallel = 1
        bandwidth = str(desired.get("bandwidth") or self.bandwidth or "50M")
        tcp_bw = str(
            desired["tcp_bandwidth"]
            if "tcp_bandwidth" in desired and desired["tcp_bandwidth"] is not None
            else self.tcp_bandwidth or ""
        )
        # Empty server in desired = keep current (client-local IPERF_SERVER).
        server_in = desired.get("server")
        if server_in is None or str(server_in).strip() == "":
            server = self.server
        else:
            server = str(server_in).strip()
        # Port is client-local (failover across PORT_START..PORT_START+COUNT).
        # Never take port from desired — that caused restart loops when the
        # client advanced past a busy server and API re-pushed the old port.
        reverse = (
            bool(desired["reverse"])
            if "reverse" in desired and desired["reverse"] is not None
            else self.reverse
        )
        duration = (
            int(desired["duration"])
            if "duration" in desired and desired["duration"] is not None
            else self.duration
        )
        if duration < 0:
            duration = 0
        interval = (
            float(desired["interval"])
            if "interval" in desired and desired["interval"] is not None
            else self.sample_interval_s
        )
        if interval <= 0:
            interval = 1.0
        gen = int(desired.get("generation") or 0)

        with self.lock:
            # Seed/snapshot (gen 0) must not override a UI-applied config.
            if gen <= 0 and self.applied_generation > 0 and action != "stop":
                return (
                    False,
                    f"ignore seed gen={gen} (applied={self.applied_generation})",
                )
            changed = (
                proto != self.protocol
                or parallel != self.parallel
                or bandwidth != self.bandwidth
                or tcp_bw != self.tcp_bandwidth
                or server != self.server
                or reverse != self.reverse
                or duration != self.duration
                or abs(self.sample_interval_s - interval) >= 1e-9
                or (action == "stop" and self.enabled)
                or (action in ("start", "set") and not self.enabled)
            )
            # Same generation + same config = no-op (redeclare). Same generation
            # but drifted live config (e.g. after a bad seed) → re-apply.
            if (
                gen
                and gen == self.applied_generation
                and action != "stop"
                and not changed
            ):
                return False, f"gen={gen} already applied"
            self.protocol = proto
            self.parallel = parallel
            self.bandwidth = bandwidth
            self.tcp_bandwidth = tcp_bw
            self.server = server
            self.reverse = reverse
            self.duration = duration
            self.sample_interval_s = interval
            self.enabled = action != "stop"
            self.applied_generation = gen if gen > 0 else self.applied_generation
            self.status = "idle" if not self.enabled else self.status

        # Always kill on stop (even if already idle) so an orphan iperf cannot
        # keep running after a missed earlier stop while WS was stuck.
        if changed or action == "stop":
            self.reload.set()
            self._kill()
            if changed:
                return (
                    True,
                    f"applied gen={gen} {proto} -P{parallel} "
                    f"{'DL' if reverse else 'UL'} action={action}",
                )
        return False, f"no-op gen={gen}"

    def _cmd(self) -> List[str]:
        with self.lock:
            protocol = self.protocol
            parallel = self.parallel
            bandwidth = self.bandwidth
            tcp_bandwidth = self.tcp_bandwidth
            port = self.port
            server = self.server
            reverse = self.reverse
            duration = self.duration
            interval = self.sample_interval_s
        cmd = [
            "iperf3",
            "-c",
            server,
            "-p",
            str(port),
            "-P",
            str(parallel),
            "-i",
            str(interval),
            "--forceflush",
        ]
        if reverse:
            cmd.append("-R")
        if protocol == "udp":
            cmd.append("-u")
            cmd.extend(["-b", use_udp_bandwidth(bandwidth)])
        else:
            tcp_b = use_tcp_bandwidth(tcp_bandwidth)
            if tcp_b:
                cmd.extend(["-b", tcp_b])
        if duration > 0:
            cmd.extend(["-t", str(duration)])
        else:
            cmd.extend(["-t", "0"])
        if self.bind_dev:
            cmd.extend(["--bind-dev", self.bind_dev])
        return cmd

    def _kill(self) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    return
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _should_log(self) -> bool:
        if self.log_interval_s <= 0:
            return True
        now = time.monotonic()
        if now - self._last_log >= self.log_interval_s:
            self._last_log = now
            return True
        return False

    def _advance_port(self, reason: str) -> None:
        with self.lock:
            nxt = self.port + 1
            if nxt > self.port_max:
                nxt = self.port_min
            print(f"[client] {reason} on {self.port}, trying {nxt}", flush=True)
            self.port = nxt

    def _wait_bind_dev(self) -> None:
        if not self.bind_dev:
            return
        with self.lock:
            self.status = "waiting_pdu"
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
            self.reload.clear()
            if not self.enabled:
                with self.lock:
                    self.status = "idle"
                    self.latest_bps = None
                # Wait until start or stop process.
                while not self.stop.is_set() and not self.enabled:
                    if self.reload.wait(0.5):
                        self.reload.clear()
                    with self.lock:
                        if self.enabled:
                            break
                continue

            if self.bind_dev:
                self._wait_bind_dev()
                if self.stop.is_set():
                    break
                if not self.enabled:
                    continue

            cmd = self._cmd()
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
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
                        if c != "--bind-dev"
                        and not (i > 0 and cmd[i - 1] == "--bind-dev")
                    ]
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        start_new_session=True,
                    )
                else:
                    raise

            assert self._proc.stdout is not None
            with self.lock:
                self.status = "running"
            print(f"[client] start {' '.join(cmd)}", flush=True)
            self._last_log = 0.0
            fail_reason: Optional[str] = None
            zero_sum_streak = 0
            interval_sum = 0.0
            interval_n = 0
            try:
                for line in self._proc.stdout:
                    if self.stop.is_set() or self.reload.is_set():
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
                    with self.lock:
                        parallel = self.parallel
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
                        if interval_n >= parallel:
                            with self.lock:
                                self.latest_bps = interval_sum
                            interval_sum = 0.0
                            interval_n = 0
            finally:
                self._kill()
                self._proc = None

            if self.reload.is_set():
                continue

            if fail_reason == "no such device":
                print(
                    f"[client] {fail_reason}; waiting for {self.bind_dev or 'network'}",
                    flush=True,
                )
                with self.lock:
                    self.status = "waiting_pdu"
                if not self.stop.wait(2):
                    continue
                break

            if fail_reason in (
                "server busy",
                "connect failed",
                "idle timeout",
                "zero throughput",
            ):
                self._advance_port(fail_reason)
                if not self.stop.wait(0.5):
                    continue

            if self.duration > 0:
                break
            if not self.stop.wait(1):
                continue


def _ws_send(ws: Any, payload: dict, *, timeout: float = 2.0) -> bool:
    """Send JSON with a short timeout so status cannot block Stop/desired."""
    raw = json.dumps(payload)
    try:
        ws.send(raw, timeout=timeout)
        return True
    except TypeError:
        try:
            ws.send(raw)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[ws] send failed: {exc}", flush=True)
            return False
    except TimeoutError:
        print(f"[ws] send timeout ({timeout}s) — reconnecting", flush=True)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[ws] send failed: {exc}", flush=True)
        return False


def _ws_loop(
    ws_url: str,
    ue_id: str,
    cluster: str,
    namespace: str,
    ue_name: str,
    runner: ClientRunner,
    stop: threading.Event,
) -> None:
    """Reconnect loop using websockets sync client."""
    try:
        from websockets.sync.client import connect
    except ImportError:
        print(
            "[ws] websockets package missing — control plane disabled",
            file=sys.stderr,
            flush=True,
        )
        return

    backoff = 1.0
    while not stop.is_set():
        try:
            print(f"[ws] connecting {ws_url}", flush=True)
            # Disable library protocol pings: under UL load eth0 stalls and the
            # keepalive thread dumps a ConnectionClosedError traceback, while
            # Stop/desired was already blocked on the same socket. Health is
            # from timed status sends below (reconnect → re-push desired).
            try:
                ws_cm = connect(
                    ws_url,
                    open_timeout=10,
                    close_timeout=2,
                    ping_interval=None,
                )
            except TypeError:
                ws_cm = connect(ws_url, open_timeout=10, close_timeout=2)
            with ws_cm as ws:
                backoff = 1.0
                snap = runner.snapshot()
                declare = {
                    "type": "declare",
                    "id": ue_id,
                    "cluster": cluster,
                    "namespace": namespace,
                    "pod": env("HOSTNAME", ""),
                    "ue_name": ue_name,
                    "version": CLIENT_VERSION,
                    "protocol": snap["protocol"],
                    "status": snap["status"],
                    "server": snap["server"],
                    "port": snap["port"],
                    "bandwidth": snap["bandwidth"],
                    "parallel": snap["parallel"],
                    "tcp_bandwidth": snap["tcp_bandwidth"],
                    "reverse": snap["reverse"],
                    "mbits_per_second": snap["mbits_per_second"],
                    "message": "websocket declare",
                }
                if not _ws_send(ws, declare, timeout=5.0):
                    raise TimeoutError("declare send failed")
                last_status = 0.0
                last_declare = time.monotonic()

                while not stop.is_set():
                    # Prefer recv so Stop/desired is never stuck behind status sends.
                    try:
                        raw = ws.recv(timeout=0.25)
                    except TimeoutError:
                        raw = None
                    except Exception:
                        break

                    if raw is not None:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            msg = None
                        if isinstance(msg, dict):
                            mtype = str(msg.get("type") or "").lower()
                            if mtype == "desired":
                                changed, note = runner.apply_desired(msg)
                                snap = runner.snapshot()
                                if not _ws_send(
                                    ws,
                                    {
                                        "type": "apply_report",
                                        "generation": int(
                                            msg.get("generation") or 0
                                        ),
                                        "ok": True,
                                        "message": note,
                                        "protocol": snap["protocol"],
                                        "status": snap["status"],
                                    },
                                    timeout=2.0,
                                ):
                                    break
                                print(
                                    f"[ws] desired → {note} (changed={changed})",
                                    flush=True,
                                )
                            elif mtype == "welcome":
                                print(
                                    f"[ws] welcome id={msg.get('id')}",
                                    flush=True,
                                )
                            elif mtype == "error":
                                print(
                                    f"[ws] error: {msg.get('message')}",
                                    flush=True,
                                )
                            elif mtype in ("pong", "ping"):
                                pass

                    now = time.monotonic()
                    if now - last_status >= 2.0:
                        snap = runner.snapshot()
                        if not _ws_send(
                            ws,
                            {
                                "type": "status",
                                "protocol": snap["protocol"],
                                "status": snap["status"],
                                "server": snap["server"],
                                "port": snap["port"],
                                "bandwidth": snap["bandwidth"],
                                "parallel": snap["parallel"],
                                "tcp_bandwidth": snap["tcp_bandwidth"],
                                "reverse": snap["reverse"],
                                "mbits_per_second": snap["mbits_per_second"],
                                "message": "",
                            },
                            timeout=1.0,
                        ):
                            break
                        last_status = now
                    if now - last_declare >= 15.0:
                        snap = runner.snapshot()
                        declare.update(
                            {
                                "protocol": snap["protocol"],
                                "status": snap["status"],
                                "server": snap["server"],
                                "port": snap["port"],
                                "bandwidth": snap["bandwidth"],
                                "parallel": snap["parallel"],
                                "tcp_bandwidth": snap["tcp_bandwidth"],
                                "reverse": snap["reverse"],
                                "mbits_per_second": snap["mbits_per_second"],
                                "message": "websocket redeclare",
                            }
                        )
                        if not _ws_send(ws, declare, timeout=1.0):
                            break
                        last_declare = now
        except Exception as exc:  # noqa: BLE001
            # One-line notice — library keepalive used to dump a full traceback.
            msg = str(exc).split("\n", 1)[0].strip() or exc.__class__.__name__
            print(f"[ws] disconnected: {msg}", flush=True)
        if stop.wait(backoff):
            break
        backoff = min(backoff * 2, 30.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="iperf3 DL client with WS control")
    ap.add_argument("--server", default=env("IPERF_SERVER", ""))
    ap.add_argument("--port", type=int, default=int(env("PORT_START", env("PORT", "5210"))))
    ap.add_argument(
        "--parallel",
        "-P",
        type=int,
        default=int(env("PARALLEL", env("PROCESSES", "5"))),
    )
    ap.add_argument("--bandwidth", default=env("BANDWIDTH", "50M"))
    ap.add_argument("--tcp-bandwidth", default=env("TCP_BANDWIDTH", ""))
    ap.add_argument("--protocol", default=env("PROTOCOL", "udp"))
    ap.add_argument("--duration", type=int, default=int(env("DURATION", "0")))
    ap.add_argument("--interval", type=float, default=float(env("REPORT_INTERVAL", "1")))
    ap.add_argument("--log-interval", type=float, default=float(env("LOG_INTERVAL", "5")))
    ap.add_argument("--bind-dev", default=env("BIND_DEV", ""))
    ap.add_argument("--testbed", default=env("TESTBED", "oai-benchmark"))
    ap.add_argument("--port-max", type=int, default=int(env("PORT_MAX", "5326")))
    ap.add_argument(
        "--api-url",
        default=env("INA_INFRA_API_URL", "http://10.1.132.200:8082"),
        help="ina-infra HTTP base (WS derived); set '-' to disable",
    )
    args = ap.parse_args()

    if not args.server:
        raise SystemExit("IPERF_SERVER / --server is required (UPF N3 address)")
    if args.parallel < 1:
        raise SystemExit("--parallel must be >= 1")
    try:
        protocol = normalize_protocol(args.protocol)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    cluster = env("INA_UE_CLUSTER", "edge")
    namespace = env("INA_UE_NAMESPACE", args.testbed or "oai-benchmark")
    ue_name = env("INA_UE_NAME", "oai-ue")
    ue_id = default_ue_id(cluster, namespace)
    ws_url = api_url_to_ws(args.api_url)

    print(
        f"iperf3-n6 client → {args.server}:{args.port} "
        f"-P {args.parallel} (DL {protocol.upper()} -R) "
        f"id={ue_id} ws={ws_url or 'disabled'}",
        flush=True,
    )

    influx = InfluxWriter()
    stop = threading.Event()

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
        protocol=protocol,
        tcp_bandwidth=args.tcp_bandwidth,
    )
    thread = threading.Thread(target=runner.run_forever, daemon=True, name="iperf3-client")
    thread.start()

    if ws_url:
        ws_thread = threading.Thread(
            target=_ws_loop,
            args=(ws_url, ue_id, cluster, namespace, ue_name, runner, stop),
            daemon=True,
            name="ue-ws",
        )
        ws_thread.start()
    else:
        print("[ws] INA_INFRA_API_URL disabled", flush=True)

    tags = {"testbed": args.testbed}
    last_agg_log = 0.0
    while not stop.wait(args.interval):
        snap = runner.snapshot()
        bps = None
        with runner.lock:
            bps = runner.latest_bps
        if bps is None:
            continue
        try:
            influx.write(
                {
                    "bits_per_second": float(bps),
                    "mbits_per_second": mbps_from_bits(bps),
                    "streams": float(snap["parallel"]),
                },
                tags={
                    "role": "client_agg",
                    "server": args.server,
                    "port": str(snap["port"]),
                    "protocol": snap["protocol"],
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
                f"(-P {snap['parallel']} port={snap['port']} {snap['protocol'].upper()})",
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
