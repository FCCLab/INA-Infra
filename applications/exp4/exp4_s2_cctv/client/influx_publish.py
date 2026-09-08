#!/usr/bin/env python3
"""Exp4 → InfluxDB metrics.

Server (origin=server): absolute CPU millicores, RAM MiB, GPU %, VRAM MiB.
  cpu_m: 1000m = 100% of one CPU; gpu_pct: 0–100% of one GPU (container PIDs).
Client (origin=client): DL throughput on TO_SERVER_IFACE (RX), application E2E
  latency (t_recv - t_send; t_send stamped on the app server before work).

Env:
  EXP4_METRICS_ORIGIN   server|client  (default: server)
  INFLUXDB_URL / TOKEN / ORG / BUCKET
  SLICE_ID, EXP4_APP_TYPE, APP_NAME, SCHEME_ID, CLUSTER
  TO_CLIENT_IFACE / TO_SERVER_IFACE
  TARGET_SERVER_IP / E2E_PROBE_HOST  (application /api/e2e host)
  EXP4_LATENCY_MODE  app (default) | ping
  INTERVAL_S
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import subprocess
import time
import urllib.request


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


INFLUX_URL = (_env("INFLUXDB_URL") or _env("INFLUX_URL") or "http://influxdb.influxdb.svc:8086").rstrip("/")
INFLUX_TOKEN = _env("INFLUXDB_TOKEN") or _env("INFLUX_TOKEN") or "ina-infra-influxdb-token"
INFLUX_ORG = _env("INFLUXDB_ORG") or _env("INFLUX_ORG") or "ina-infra"
INFLUX_BUCKET = _env("INFLUXDB_BUCKET") or _env("INFLUX_BUCKET") or "default"
MEASUREMENT = _env("INFLUXDB_MEASUREMENT") or "application_metrics"
SLICE_ID = _env("SLICE_ID", "1")
APP_TYPE = _env("EXP4_APP_TYPE") or f"exp4-s{SLICE_ID}"
APP_NAME = _env("APP_NAME") or APP_TYPE
SCHEME_ID = _env("SCHEME_ID") or _env("INA_LAB_SCHEME") or "exp4"
CLUSTER = _env("TARGET_CLUSTER") or _env("CLUSTER") or "unknown"
ORIGIN = (_env("EXP4_METRICS_ORIGIN") or _env("ORIGIN") or "server").lower()
if ORIGIN not in ("server", "client"):
    ORIGIN = "server"
INTERVAL_S = float(_env("INTERVAL_S") or "1")
WRITE_URL = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns"
HEADERS = {
    "Authorization": f"Token {INFLUX_TOKEN}",
    "Content-Type": "text/plain; charset=utf-8",
}

if ORIGIN == "client":
    IFACE = _env("TO_SERVER_IFACE") or _env("PDU_IFACE") or "net1"
    PROBE_HOST = (
        _env("TARGET_SERVER_IP")
        or _env("E2E_PROBE_HOST")
        or _env("SFTP_HOST")
        or _env("IPERF_HOST")
        or _env("BROKER_HOST")
        or ""
    )
else:
    IFACE = _env("TO_CLIENT_IFACE") or _env("OTA_IFACE") or "net1"
    PROBE_HOST = ""


def _tag(value: str) -> str:
    return value.replace(" ", "_").replace(",", "_").replace("=", "_").replace("\n", "_")


def _read(path: str) -> str | None:
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return None


def _cgroup_usage_usec() -> int | None:
    raw = _read("/sys/fs/cgroup/cpu.stat")
    if raw:
        for line in raw.splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    raw = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    if raw:
        return int(raw.strip()) // 1000
    return None


def cpu_m(prev: tuple[int, float] | None) -> tuple[float, tuple[int, float] | None]:
    """Absolute CPU usage in millicores (1000m = 1 full CPU)."""
    usage = _cgroup_usage_usec()
    now = time.monotonic()
    if usage is None:
        return 0.0, prev
    if prev is None:
        return 0.0, (usage, now)
    prev_u, prev_t = prev
    du = max(0, usage - prev_u)
    dt = max(1e-3, now - prev_t)
    # usage_usec / 1e6 = CPU-seconds; / dt = CPUs; * 1000 = millicores
    millicores = 1000.0 * (du / 1e6) / dt
    return max(millicores, 0.0), (usage, now)


def _cgroup_mem_current() -> int | None:
    raw = _read("/sys/fs/cgroup/memory.current")
    if raw:
        return int(raw.strip())
    raw = _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if raw:
        return int(raw.strip())
    return None


def mem_mb() -> float:
    """Absolute RAM used by this container (MiB)."""
    used = _cgroup_mem_current()
    if used is None:
        return 0.0
    return used / (1024 * 1024)


def _cgroup_pids() -> set[int]:
    for path in (
        "/sys/fs/cgroup/cgroup.procs",
        "/sys/fs/cgroup/memory/cgroup.procs",
        "/sys/fs/cgroup/cpu/cgroup.procs",
    ):
        raw = _read(path)
        if raw:
            return {int(x) for x in raw.split() if x.isdigit()}
    return {os.getpid()}


def _smi(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["nvidia-smi", *args],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


# NVML process-utilization watermark (ns). Shared across samples in this process.
_NVML_LAST_TS = 0


def _pid_in_my_cgroup(pid: int) -> bool:
    """True if pid shares this container's cgroup (host or container PID view)."""
    try:
        mine = open("/proc/self/cgroup", encoding="utf-8").read()
        theirs = open(f"/proc/{pid}/cgroup", encoding="utf-8").read()
    except OSError:
        return False
    my_paths = {ln.split(":", 2)[-1].strip() for ln in mine.splitlines() if ln.strip()}
    their_paths = {ln.split(":", 2)[-1].strip() for ln in theirs.splitlines() if ln.strip()}
    return bool(my_paths & their_paths)


def _nvml_sm_for_pids(mine: set[int]) -> float | None:
    """Per-process SM util (%) for PIDs in mine via libnvidia-ml. None if unavailable."""
    global _NVML_LAST_TS
    try:
        lib = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError:
        try:
            lib = ctypes.CDLL("libnvidia-ml.so")
        except OSError:
            return None

    class Sample(ctypes.Structure):
        _fields_ = [
            ("pid", ctypes.c_uint),
            ("timeStamp", ctypes.c_ulonglong),
            ("smUtil", ctypes.c_uint),
            ("memUtil", ctypes.c_uint),
            ("encUtil", ctypes.c_uint),
            ("decUtil", ctypes.c_uint),
        ]

    lib.nvmlInit_v2.restype = ctypes.c_int
    if lib.nvmlInit_v2() != 0:
        return None
    try:
        count = ctypes.c_uint(0)
        lib.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetCount_v2.restype = ctypes.c_int
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0 or count.value < 1:
            return None

        sm_vals: list[float] = []
        last_ts = ctypes.c_ulonglong(_NVML_LAST_TS)
        for idx in range(int(count.value)):
            handle = ctypes.c_void_p()
            lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
            lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
            if lib.nvmlDeviceGetHandleByIndex_v2(idx, ctypes.byref(handle)) != 0:
                continue
            n = ctypes.c_uint(64)
            buf = (Sample * 64)()
            # First call may only set watermark; second (next interval) yields samples.
            rc = lib.nvmlDeviceGetProcessUtilization(
                handle, buf, ctypes.byref(n), last_ts
            )
            # NVML_ERROR_INSUFFICIENT_SIZE = 7 → grow once
            if rc == 7:
                n = ctypes.c_uint(256)
                buf = (Sample * 256)()
                rc = lib.nvmlDeviceGetProcessUtilization(
                    handle, buf, ctypes.byref(n), last_ts
                )
            if rc != 0:
                continue
            for i in range(int(n.value)):
                s = buf[i]
                if _NVML_LAST_TS < s.timeStamp:
                    _NVML_LAST_TS = int(s.timeStamp)
                pid = int(s.pid)
                if pid not in mine and not _pid_in_my_cgroup(pid):
                    continue
                sm_vals.append(float(s.smUtil))
        if not sm_vals:
            return None
        # One process: its SM%; several: sum capped at 100 (one GPU)
        if len(sm_vals) == 1:
            return min(100.0, sm_vals[0])
        return min(100.0, sum(sm_vals))
    except Exception:
        return None
    finally:
        try:
            lib.nvmlShutdown()
        except Exception:
            pass


def _pmon_sm_for_pids(mine: set[int]) -> float | None:
    """Per-process SM util from nvidia-smi pmon for PIDs in this container only."""
    pmon = _smi("pmon", "-c", "1", "-s", "u")
    if not pmon:
        return None
    sm_vals: list[float] = []
    for line in pmon.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split()
        if len(cols) < 4 or not cols[1].isdigit():
            continue
        pid = int(cols[1])
        if pid not in mine and not _pid_in_my_cgroup(pid):
            continue
        try:
            sm_vals.append(float(cols[3]))
        except ValueError:
            continue  # pmon often prints "-" when SM is unavailable
    if not sm_vals:
        return None
    if len(sm_vals) == 1:
        return min(100.0, sm_vals[0])
    return min(100.0, sum(sm_vals))


def gpu_stats() -> tuple[float, float]:
    """Container-only GPU % + VRAM MiB (never whole-device util).

    gpu_pct: 0–100% of one GPU attributable to this cgroup's PIDs (NVML/SM).
    vram_mb: used GPU memory for this container's compute PIDs only.
    """
    mine = _cgroup_pids()
    apps = _smi("--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits")
    vram_mb = 0.0
    if apps:
        for line in apps.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid not in mine and not _pid_in_my_cgroup(pid):
                continue
            try:
                vram_mb += float(parts[1])
            except ValueError:
                continue

    # Prefer NVML per-PID SM; fall back to pmon. Never use device utilization.gpu.
    gpu_pct = _nvml_sm_for_pids(mine)
    if gpu_pct is None:
        gpu_pct = _pmon_sm_for_pids(mine)
    if gpu_pct is None:
        gpu_pct = 0.0

    return min(max(gpu_pct, 0.0), 100.0), max(vram_mb, 0.0)


def iface_rx_bytes(name: str) -> int | None:
    try:
        return int(open(f"/sys/class/net/{name}/statistics/rx_bytes", encoding="utf-8").read())
    except (OSError, ValueError):
        return None


def latency_ms() -> float | None:
    """Application E2E one-way delay (ms): client_recv - t_send.

    Prefer a fresh sample written by the client app (s4 download, s5 MQTT,
    s2 camera+YOLO+RTSP/HLS). HTTP /api/e2e is used for s1/s3 only; s2 never
    uses HTTP OWD (EXP4_LATENCY_HTTP=0 / exp4-s2).
    """
    mode = (_env("EXP4_LATENCY_MODE") or "app").lower()
    if mode in ("ping", "icmp"):
        return _ping_ms()
    from_file = _e2e_from_file()
    if from_file is not None:
        return from_file
    # Slice 2: video E2E only (camera + YOLO + RTSP/HLS). Never HTTP OWD.
    if _use_http_e2e():
        http = _e2e_from_http()
        if http is not None:
            return http
    if mode == "app":
        return None
    return _ping_ms()


def _use_http_e2e() -> bool:
    flag = (_env("EXP4_LATENCY_HTTP") or "").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    app = (_env("EXP4_APP_TYPE") or APP_TYPE).lower()
    if app in ("exp4-s2", "exp4_s2", "cctv"):
        return False
    return True


def _e2e_from_file(max_age_s: float = 3.0) -> float | None:
    path = _env("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms"
    try:
        st = os.stat(path)
        if (time.time() - st.st_mtime) > max_age_s:
            return None
        return _clip_latency_ms(float(open(path, encoding="utf-8").read().strip().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def _clip_latency_ms(ms: float) -> float | None:
    """Drop epoch mistakes (t_send=0 → ~56 years) and other garbage."""
    if ms < 0.0 or ms > 60_000.0:
        return None
    return ms


def _e2e_from_http() -> float | None:
    if not PROBE_HOST:
        return None
    port = int(_env("E2E_HTTP_PORT") or "8080")
    path = _env("E2E_HTTP_PATH") or "/api/e2e"
    src = _env("SIM5G_IP") or _env("MULTUS_IP") or ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        if src:
            sock.bind((src, 0))
        sock.connect((PROBE_HOST, port))
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {PROBE_HOST}\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(req.encode())
        buf = b""
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buf += chunk
        sock.close()
    except Exception:
        return None
    t_recv = time.time()
    try:
        _hdr, body = buf.split(b"\r\n\r\n", 1)
        data = json.loads(body.decode("utf-8", "replace"))
        pre = data.get("e2e_ms")
        if pre is not None:
            return _clip_latency_ms(float(pre))
        t_send = data.get("t_send")
        if t_send is None:
            return None
        t_send = float(t_send)
    except Exception:
        return None
    # Unix seconds; 0 or NTP-vs-unix mix produces years of fake delay.
    if t_send < 1_000_000_000.0:
        return None
    return _clip_latency_ms((t_recv - t_send) * 1000.0)


def _ping_ms() -> float | None:
    """ICMP RTT client → server via TO_SERVER_IFACE (opt-in)."""
    if not PROBE_HOST:
        return None
    cmd = ["ping", "-c", "1", "-W", "1", "-I", IFACE, PROBE_HOST]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=2, stderr=subprocess.STDOUT)
    except Exception:
        return None
    m = re.search(r"time[=<]([\d.]+)\s*ms", out)
    if not m:
        return None
    return float(m.group(1))


def publish(fields: dict[str, float]) -> None:
    tags = (
        f"profile_name=exp4,slice_id={_tag(SLICE_ID)},app_type={_tag(APP_TYPE)},"
        f"app_name={_tag(APP_NAME)},cluster={_tag(CLUSTER)},origin={_tag(ORIGIN)},"
        f"scheme={_tag(SCHEME_ID)},iface={_tag(IFACE)}"
    )
    fl = ",".join(f"{k}={v:.6f}" for k, v in fields.items())
    body = f"{MEASUREMENT},{tags} {fl} {time.time_ns()}\n"
    req = urllib.request.Request(WRITE_URL, data=body.encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=4):
        pass


def loop_server() -> None:
    print(
        f"[exp4-influx] server {APP_TYPE} slice={SLICE_ID} "
        f"CPU/RAM/GPU/VRAM -> {INFLUX_URL} every {INTERVAL_S}s",
        flush=True,
    )
    cpu_prev = None
    while True:
        time.sleep(max(0.2, INTERVAL_S))
        cpu, cpu_prev = cpu_m(cpu_prev)
        gpu_pct, vram_mb = gpu_stats()
        fields = {
            "cpu_m": cpu,
            "mem_mb": mem_mb(),
            "gpu_pct": gpu_pct,
            "vram_mb": vram_mb,
        }
        try:
            publish(fields)
        except Exception as exc:
            print(f"[exp4-influx] write error: {exc}", flush=True)


def loop_client() -> None:
    print(
        f"[exp4-influx] client {APP_TYPE} slice={SLICE_ID} "
        f"iface={IFACE} probe={PROBE_HOST or '-'} "
        f"DL+latency -> {INFLUX_URL} every {INTERVAL_S}s",
        flush=True,
    )
    last_rx = iface_rx_bytes(IFACE)
    last_t = time.monotonic()
    while True:
        time.sleep(max(0.2, INTERVAL_S))
        now = time.monotonic()
        dt = max(0.2, now - last_t)
        last_t = now
        rx = iface_rx_bytes(IFACE)
        dl_mbps = 0.0
        if rx is not None and last_rx is not None:
            dl_mbps = max(0.0, (rx - last_rx) * 8.0 / dt / 1e6)
        last_rx = rx if rx is not None else last_rx
        fields: dict[str, float] = {"throughput_dl_mbps": dl_mbps}
        lat = latency_ms()
        if lat is not None:
            fields["latency_ms"] = lat
        try:
            publish(fields)
        except Exception as exc:
            print(f"[exp4-influx] write error: {exc}", flush=True)


def main() -> None:
    if ORIGIN == "client":
        loop_client()
    else:
        loop_server()


if __name__ == "__main__":
    main()
