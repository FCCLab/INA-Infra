#!/usr/bin/env python3
"""UE ICMP RTT probe: long-running ping -I <iface>, parse time=, write Influx."""
from __future__ import annotations

import argparse
import os
import re
import select
import subprocess
import time
import urllib.request


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


INFLUX_URL = _env("INFLUXDB_URL", "http://influxdb.influxdb.svc:8086").rstrip("/")
INFLUX_TOKEN = _env("INFLUXDB_TOKEN", "ina-infra-influxdb-token")
INFLUX_ORG = _env("INFLUXDB_ORG", "ina-infra")
INFLUX_BUCKET = _env("INFLUXDB_BUCKET", "default")
MEASUREMENT = _env("INFLUXDB_MEASUREMENT", "application_metrics")
SLICE_ID = _env("SLICE_ID", "1")
PROFILE = _env("PROFILE_NAME", "ina-infra")
APP_TYPE = _env("APP_TYPE", "unknown")
APP_NAME = _env("APP_NAME", "app")
CLUSTER = _env("TARGET_CLUSTER", "edge")
WRITE_URL = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns"
HEADERS = {
    "Authorization": f"Token {INFLUX_TOKEN}",
    "Content-Type": "text/plain; charset=utf-8",
}
# ping -I is SO_BINDTODEVICE; these mean the bind/netdev is gone.
IFACE_ERR = (
    "SO_BINDTODEVICE",
    "No such device",
    "Network is down",
    "sendmsg:",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ICMP RTT probe via ping -I")
    p.add_argument(
        "-I",
        "--iface",
        default=_env("RTT_PING_IFACE", "oaitun_ue1"),
        help="bind ping to this interface (ping -I). env RTT_PING_IFACE",
    )
    p.add_argument(
        "target",
        nargs="?",
        default=_env("RTT_PING_TARGET", "10.1.137.1"),
        help="ping target. env RTT_PING_TARGET",
    )
    return p.parse_args()


def iface_ifindex(name: str) -> int | None:
    """Current netdev ifindex, or None if the name is not present."""
    try:
        with open(f"/sys/class/net/{name}/ifindex", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def wait_iface(name: str) -> int:
    """Block until iface exists; return its ifindex (new device after a flap)."""
    n = 0
    while True:
        idx = iface_ifindex(name)
        if idx is not None:
            return idx
        if n == 0 or n % 10 == 0:
            print(f"[rtt-probe] waiting for iface {name}", flush=True)
        n += 1
        time.sleep(1)


def start_ping(ifn: str, target: str) -> subprocess.Popen[str]:
    cmds = (
        ["stdbuf", "-oL", "-eL", "ping", "-i", "1", "-W", "2", "-I", ifn, target],
        ["ping", "-i", "1", "-W", "2", "-I", ifn, target],
    )
    last: Exception | None = None
    for cmd in cmds:
        try:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            last = exc
    raise last or FileNotFoundError("ping")


def publish(rtt_ms: float) -> None:
    now_ns = time.time_ns()
    tags = (
        f"profile_name={PROFILE},slice_id={SLICE_ID},app_type={APP_TYPE},"
        f"app_name={APP_NAME},cluster={CLUSTER},origin=client,ue_id={APP_NAME}"
    )
    body = f"{MEASUREMENT},{tags} app_ue_rtt_ms={rtt_ms},rtt_ms={rtt_ms} {now_ns}\n"
    req = urllib.request.Request(
        WRITE_URL, data=body.encode("utf-8"), headers=HEADERS, method="POST"
    )
    with urllib.request.urlopen(req, timeout=4):
        pass


def iface_lost(line: str) -> bool:
    return any(s in line for s in IFACE_ERR)


def run_ping(ifn: str, target: str, idx: int) -> None:
    """One forever ping bound to ifindex idx. Returns when iface flaps or ping dies."""
    proc = start_ping(ifn, target)
    print(
        f"[rtt-probe] ping pid={proc.pid} {target} -I {ifn} ifindex={idx}",
        flush=True,
    )
    stdout = proc.stdout
    assert stdout is not None
    try:
        while True:
            cur = iface_ifindex(ifn)
            if cur != idx:
                print(
                    f"[rtt-probe] iface {ifn} ifindex {idx} -> {cur}; restart ping",
                    flush=True,
                )
                break
            if proc.poll() is not None:
                break
            ready, _, _ = select.select([stdout], [], [], 1.0)
            if not ready:
                continue
            raw = stdout.readline()
            if raw == "":
                break
            line = raw.rstrip("\n")
            if line:
                print(line, flush=True)
            if iface_lost(line):
                print("[rtt-probe] ping lost device; restart ping", flush=True)
                break
            m = re.search(r"time=([0-9.]+)", line)
            if not m:
                continue
            rtt_ms = float(m.group(1))
            if rtt_ms <= 0:
                continue
            try:
                publish(rtt_ms)
            except Exception as exc:
                print(f"[rtt-probe] influx write error: {exc}", flush=True)
    finally:
        if proc.poll() is None:
            proc.kill()
        rc = proc.wait()
        print(f"[rtt-probe] ping exited rc={rc}", flush=True)


def main() -> None:
    args = parse_args()
    ifn = args.iface
    target = args.target
    print(
        f"[rtt-probe] ICMP stream {target} -I {ifn} for {APP_NAME} -> {WRITE_URL}",
        flush=True,
    )
    while True:
        try:
            idx = wait_iface(ifn)
            run_ping(ifn, target, idx)
        except Exception as exc:
            print(f"[rtt-probe] loop error: {exc}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
