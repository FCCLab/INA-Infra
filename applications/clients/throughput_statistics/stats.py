#!/usr/bin/env python3
"""PDU interface throughput: named iface byte counters -> Mbps, stdout + Influx.

Interface selection (first match wins the sample; each match is published):
  IFACE         exact name, e.g. oaitun_ue1 (optional)
  IFACE_PREFIX  prefix used when IFACE is empty (default: oaitun)
"""
from __future__ import annotations

import os
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
IFACE = _env("IFACE", "")
IFACE_PREFIX = _env("IFACE_PREFIX", "oaitun")
INTERVAL_S = float(_env("SAMPLE_INTERVAL_S", "1") or "1")
WRITE_URL = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns"
HEADERS = {
    "Authorization": f"Token {INFLUX_TOKEN}",
    "Content-Type": "text/plain; charset=utf-8",
}


def read_all_iface_bytes() -> dict[str, tuple[int, int]]:
    """Return {iface: (rx_bytes, tx_bytes)} for every /proc/net/dev interface."""
    out: dict[str, tuple[int, int]] = {}
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                name = iface.strip()
                if not name:
                    continue
                parts = rest.split()
                if len(parts) < 9:
                    continue
                out[name] = (int(parts[0]), int(parts[8]))
    except OSError as exc:
        print(f"[throughput] read /proc/net/dev: {exc}", flush=True)
    return out


def select_ifaces(all_bytes: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Keep only the configured interface(s)."""
    if IFACE:
        if IFACE in all_bytes:
            return {IFACE: all_bytes[IFACE]}
        return {}
    prefix = IFACE_PREFIX
    return {name: counters for name, counters in all_bytes.items() if name.startswith(prefix)}


def waiting_for() -> str:
    if IFACE:
        return IFACE
    return f"{IFACE_PREFIX}*"


def mbps(delta_bytes: int, dt: float) -> float:
    if dt <= 0:
        return 0.0
    return max(0.0, (delta_bytes * 8.0) / (dt * 1_000_000.0))


def _influx_tag(value: str) -> str:
    return (
        value.replace(" ", "_")
        .replace(",", "_")
        .replace("=", "_")
        .replace("\n", "_")
    )


def publish(iface: str, ul: float, dl: float, total: float) -> None:
    now_ns = time.time_ns()
    iface_tag = _influx_tag(iface)
    tags = (
        f"profile_name={PROFILE},slice_id={SLICE_ID},app_type={APP_TYPE},"
        f"app_name={APP_NAME},cluster={CLUSTER},origin=client,ue_id={APP_NAME},"
        f"iface={iface_tag}"
    )
    fields = (
        f"throughput_ul_mbps={ul:.6f},throughput_dl_mbps={dl:.6f},"
        f"throughput_mbps={total:.6f},throughput_client={total:.6f}"
    )
    body = f"{MEASUREMENT},{tags} {fields} {now_ns}\n"
    req = urllib.request.Request(
        WRITE_URL, data=body.encode("utf-8"), headers=HEADERS, method="POST"
    )
    with urllib.request.urlopen(req, timeout=4):
        pass


def main() -> None:
    selector = f"IFACE={IFACE}" if IFACE else f"IFACE_PREFIX={IFACE_PREFIX}*"
    print(
        f"[throughput] {selector} UL=TX DL=RX for {APP_NAME} every {INTERVAL_S}s -> {WRITE_URL}",
        flush=True,
    )
    last = select_ifaces(read_all_iface_bytes())
    last_t = time.time()
    while True:
        time.sleep(max(0.2, INTERVAL_S))
        now = time.time()
        dt = max(0.2, now - last_t)
        curr = select_ifaces(read_all_iface_bytes())
        last_t = now
        if not curr:
            print(f"[throughput] waiting for {waiting_for()}", flush=True)
            last = curr
            continue
        for name, (rx, tx) in sorted(curr.items()):
            prev_rx, prev_tx = last.get(name, (rx, tx))
            d_rx = max(0, rx - prev_rx)
            d_tx = max(0, tx - prev_tx)
            dl = round(mbps(d_rx, dt), 3)
            ul = round(mbps(d_tx, dt), 3)
            total = round(ul + dl, 3)
            print(
                f"[throughput] iface={name} dt={dt:.3f}s "
                f"ul={ul:.3f} dl={dl:.3f} total={total:.3f} Mbps "
                f"dTX={d_tx} dRX={d_rx}",
                flush=True,
            )
            try:
                publish(name, ul, dl, total)
            except Exception as exc:
                print(f"[throughput] influx write error iface={name}: {exc}", flush=True)
        last = curr


if __name__ == "__main__":
    main()
