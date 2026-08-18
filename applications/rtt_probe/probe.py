#!/usr/bin/env python3
"""UE ICMP RTT probe: long-running ping via oaitun, parse time=, write Influx."""
from __future__ import annotations

import os
import re
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
TARGET = _env("RTT_PING_TARGET", "10.1.137.1")
WRITE_URL = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns"
HEADERS = {
    "Authorization": f"Token {INFLUX_TOKEN}",
    "Content-Type": "text/plain; charset=utf-8",
}


def oaituns() -> list[str]:
    out: list[str] = []
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                name = line.split(":", 1)[0].strip()
                if name.startswith("oaitun"):
                    out.append(name)
    except OSError:
        pass
    return out


def ensure_pdu_route() -> None:
    for ifn in oaituns():
        try:
            subprocess.run(
                ["ip", "route", "replace", f"{TARGET}/32", "dev", ifn],
                capture_output=True,
                timeout=2,
                check=False,
            )
        except Exception:
            pass


def start_ping() -> subprocess.Popen[str]:
    cmds = (
        ["stdbuf", "-oL", "-eL", "ping", "-i", "1", TARGET],
        ["ping", "-i", "1", TARGET],
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


def main() -> None:
    print(
        f"[rtt-probe] ICMP stream {TARGET} via oaitun for {APP_NAME} -> {WRITE_URL}",
        flush=True,
    )
    while True:
        try:
            ifaces = oaituns()
            if not ifaces:
                print("[rtt-probe] waiting for oaitun", flush=True)
                time.sleep(1)
                continue
            ensure_pdu_route()
            proc = start_ping()
            print(
                f"[rtt-probe] ping pid={proc.pid} {TARGET} via {ifaces[0]}",
                flush=True,
            )
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    if line:
                        print(line, flush=True)
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
                print(f"[rtt-probe] ping exited rc={rc}; restarting", flush=True)
        except Exception as exc:
            print(f"[rtt-probe] loop error: {exc}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
