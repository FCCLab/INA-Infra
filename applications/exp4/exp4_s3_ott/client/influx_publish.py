#!/usr/bin/env python3
"""Exp4 → InfluxDB metrics.

Server (origin=server): absolute CPU millicores, RAM MiB, GPU %, VRAM MiB,
  plus DL TCP Send-Q / notsent / outstanding retrans / cwnd / rwnd on TO_CLIENT_IFACE (net1).
  cpu_m: 1000m = 100% of one CPU; gpu_pct: 0–100% of one GPU (container PIDs).
Client (origin=client): DL throughput on TO_SERVER_IFACE (RX), application
  latency (t_recv - t_send), transmission latency (ICMP RTT), E2E
  (application + transmission), plus TCP Recv-Q / rwnd on TO_SERVER_IFACE.

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
import fcntl
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request

for _p in ("/app/backend", "/usr/local/bin", "/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import to_server as _ts
except ImportError:
    _ts = None  # type: ignore


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


_SKIP_IFACES = frozenset({"lo", "eth0", "eth1", "net2"})


def to_client_iface() -> str | None:
    """Server DL iface (TO_CLIENT_IFACE / net1). Never lo or cluster eth0."""
    name = (_env("TO_CLIENT_IFACE") or _env("OTA_IFACE") or IFACE or "net1").strip()
    if not name or name in _SKIP_IFACES:
        return None
    return name


def iface_ipv4(name: str) -> str | None:
    """IPv4 address on this iface only (no cluster/console NIC)."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out.stdout or "")
        if m:
            ip = m.group(1)
            if ip and not ip.startswith("127.") and not ip.startswith("0."):
                return ip
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        packed = struct.pack("256s", name[:15].encode())
        addr = fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24]
        s.close()
        ip = socket.inet_ntoa(addr)
        if ip and not ip.startswith("127.") and not ip.startswith("0."):
            return ip
    except OSError:
        pass
    return None


def _ss_local_on_ip(local: str, ip: str) -> bool:
    """True if ss local address is ``ip`` (IPv4 or IPv4-mapped IPv6)."""
    loc = local.lower()
    ip_l = ip.lower()
    if loc.startswith(ip_l + ":") or loc.startswith(f"[{ip_l}]:"):
        return True
    mapped = f"::ffff:{ip_l}"
    return loc.startswith(mapped + ":") or loc.startswith(f"[{mapped}]:")


def _parse_ss_queues(
    stdout: str, local_ip: str | None = None
) -> tuple[int, int, int, int, int]:
    """Sum ESTAB Send-Q, notsent, outstanding retrans, cwnd, peer rwnd.

    ``cwnd`` is segments; peer window is ``snd_wnd`` (bytes) from client ACKs
    (ss may also label it ``rwnd``). If ``local_ip`` is set, keep sockets on that IP.
    """
    sendq = 0
    notsent = 0
    retrans = 0
    cwnd = 0
    rwnd = 0
    matched = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        state = parts[0].upper() if parts else ""
        if state in ("STATE", "NETID"):
            continue
        offset = 0
        if state in ("TCP", "U_STR"):
            offset = 1
            state = parts[1].upper() if len(parts) > 1 else ""
        if state in ("ESTAB", "ESTABLISHED") and len(parts) >= offset + 5:
            try:
                sq = int(parts[offset + 2])
            except ValueError:
                matched = False
                continue
            local = parts[offset + 3]
            if local_ip is None or _ss_local_on_ip(local, local_ip):
                sendq += max(sq, 0)
                matched = True
            else:
                matched = False
            continue
        if matched and (
            "notsent:" in line
            or "retrans:" in line
            or "cwnd:" in line
            or "rwnd:" in line
            or line.startswith(("cubic", "reno", "bbr", "vegas"))
        ):
            m = re.search(r"notsent:(\d+)", line)
            if m:
                notsent += int(m.group(1))
            rm = re.search(r"(?:^|\s)retrans:(\d+)(?:/\d+)?", line)
            if rm:
                retrans += int(rm.group(1))
            cm = re.search(r"(?:^|\s)cwnd:(\d+)", line)
            if cm:
                cwnd += int(cm.group(1))
            # Peer advertised window (from client ACKs): ss prints snd_wnd, not rwnd.
            # (rwnd_limited: is a time share, not the window size.)
            wm = re.search(r"(?:^|\s)(?:snd_wnd|rwnd):(\d+)", line)
            if wm:
                rwnd += int(wm.group(1))
            matched = False
    return sendq, notsent, retrans, cwnd, rwnd


def _parse_ss_recv(stdout: str, local_ip: str | None = None) -> tuple[int, int]:
    """Sum ESTAB Recv-Q and advertised rwnd (rcv_space / rwnd).

    Client DL side: sockets on TO_SERVER_IFACE receiving from the app server.
    """
    recvq = 0
    rwnd = 0
    matched = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        state = parts[0].upper() if parts else ""
        if state in ("STATE", "NETID"):
            continue
        offset = 0
        if state in ("TCP", "U_STR"):
            offset = 1
            state = parts[1].upper() if len(parts) > 1 else ""
        if state in ("ESTAB", "ESTABLISHED") and len(parts) >= offset + 5:
            try:
                rq = int(parts[offset + 1])
            except ValueError:
                matched = False
                continue
            local = parts[offset + 3]
            if local_ip is None or _ss_local_on_ip(local, local_ip):
                recvq += max(rq, 0)
                matched = True
            else:
                matched = False
            continue
        if matched and (
            "rcv_space:" in line
            or "rwnd:" in line
            or "cwnd:" in line
            or line.startswith(("cubic", "reno", "bbr", "vegas"))
        ):
            m = re.search(r"(?:rcv_space|rwnd):(\d+)", line)
            if m:
                rwnd += int(m.group(1))
            matched = False
    return recvq, rwnd


def _ss_run(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except FileNotFoundError:
        raise
    except (OSError, subprocess.TimeoutExpired):
        return None
    err = (proc.stderr or "").lower()
    if proc.returncode != 0 or "unknown" in err or "cannot parse" in err:
        return None
    return proc.stdout or ""


def _ss_dl_queues(
    local_ip: str | None, iface: str
) -> tuple[int, int, int, int, int] | None:
    """Send-Q / notsent / retrans / cwnd / rwnd on TO_CLIENT_IFACE.

    ``dev`` is the kernel's bound/used device (net1). Fall back to a full
    dump filtered by that iface's IPv4 if this ``ss`` has no ``dev``
    predicate or the socket is not bound to the NIC (common for iperf ``:::``).
    """
    try:
        idx = (_read(f"/sys/class/net/{iface}/ifindex") or "").strip()
    except Exception:
        idx = ""
    dev_cmds = [
        ["ss", "-H", "-tni", "state", "established", "dev", iface],
        ["ss", "-H", "-tni", "dev", iface],
        ["ss", "-tni", "dev", iface],
    ]
    if idx.isdigit():
        dev_cmds.extend(
            (
                ["ss", "-H", "-tni", "state", "established", "dev", idx],
                ["ss", "-H", "-tni", "dev", idx],
            )
        )
    best = (0, 0, 0, 0, 0)
    saw_ss = False

    def _better(
        a: tuple[int, int, int, int, int], b: tuple[int, int, int, int, int]
    ) -> bool:
        return any(a[i] > b[i] for i in range(5))

    for cmd in dev_cmds:
        try:
            out = _ss_run(cmd)
        except FileNotFoundError:
            return None
        if out is None:
            continue
        saw_ss = True
        parsed = _parse_ss_queues(out, local_ip=None)
        if _better(parsed, best):
            best = parsed
        if parsed != (0, 0, 0, 0, 0):
            return parsed
    dump_cmds = (
        ["ss", "-H", "-tni"],
        ["ss", "-tni"],
        ["ss", "-H", "-4", "-tni"],
        ["ss", "-4", "-tni"],
    )
    for cmd in dump_cmds:
        try:
            out = _ss_run(cmd)
        except FileNotFoundError:
            return None
        if out is None:
            continue
        saw_ss = True
        if not local_ip:
            continue
        parsed = _parse_ss_queues(out, local_ip)
        if _better(parsed, best):
            best = parsed
        if parsed != (0, 0, 0, 0, 0):
            return parsed
    if saw_ss:
        return best
    return None


def _proc_tcp_sendq(local_ip: str) -> int:
    """Send-Q from /proc/net/tcp{,6} for ESTABLISHED sockets on ``local_ip``."""
    total = 0
    want = socket.inet_aton(local_ip)[::-1].hex()
    mapped6 = "0000000000000000ffff0000" + want
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        raw = _read(path)
        if not raw:
            continue
        for line in raw.splitlines()[1:]:
            cols = line.split()
            if len(cols) < 5:
                continue
            try:
                st = int(cols[3], 16)
            except ValueError:
                continue
            if st != 1:
                continue
            lip, _lp = cols[1].split(":")
            key = lip.lower()
            if path.endswith("tcp6"):
                if key != mapped6:
                    continue
            elif key != want:
                continue
            try:
                tx = int(cols[4].split(":")[0], 16)
            except ValueError:
                continue
            total += tx
    return total


def dl_tcp_queues(
    iface: str | None = None,
) -> tuple[float, float, float, float, float]:
    """Server DL TCP Send-Q, notsent, retrans, cwnd, rwnd on TO_CLIENT_IFACE.

    Prefer ``ss -tni dev <iface>``. Fallback: ESTAB sockets on that iface's
    IPv4 (including IPv4-mapped tcp6). Never lo / eth0.
    """
    name = iface or to_client_iface()
    if not name:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    ip = iface_ipv4(name)
    if ip and ip.startswith("127."):
        ip = None
    parsed = _ss_dl_queues(ip, name)
    if parsed is not None:
        return (
            float(parsed[0]),
            float(parsed[1]),
            float(parsed[2]),
            float(parsed[3]),
            float(parsed[4]),
        )
    if ip:
        return float(_proc_tcp_sendq(ip)), 0.0, 0.0, 0.0, 0.0
    return 0.0, 0.0, 0.0, 0.0, 0.0


def _ss_ul_recv(local_ip: str | None, iface: str) -> tuple[int, int] | None:
    """Recv-Q / rwnd on TO_SERVER_IFACE (client DL receive path)."""
    try:
        idx = (_read(f"/sys/class/net/{iface}/ifindex") or "").strip()
    except Exception:
        idx = ""
    cmds: list[list[str]] = [
        ["ss", "-H", "-tni", "state", "established", "dev", iface],
        ["ss", "-H", "-tni", "dev", iface],
        ["ss", "-H", "-tni"],
        ["ss", "-tni"],
    ]
    if idx.isdigit():
        cmds.insert(0, ["ss", "-H", "-tni", "state", "established", "dev", idx])
    best = (0, 0)
    saw = False
    for cmd in cmds:
        try:
            out = _ss_run(cmd)
        except FileNotFoundError:
            return None
        if out is None:
            continue
        saw = True
        # ``dev`` filter: no IP filter. Dump: require local IP on to-server iface.
        use_ip = None if ("dev" in cmd) else local_ip
        if use_ip is None and "dev" not in cmd and not local_ip:
            continue
        parsed = _parse_ss_recv(out, use_ip)
        if parsed[0] > best[0] or parsed[1] > best[1]:
            best = parsed
        if parsed != (0, 0):
            return parsed
    if saw:
        return best
    return None


def client_tcp_recv(iface: str | None = None) -> tuple[float, float]:
    """Client DL TCP Recv-Q and rwnd (bytes) on TO_SERVER_IFACE only."""
    name = iface or resolve_client_iface()
    if not name or name in _SKIP_IFACES:
        return 0.0, 0.0
    ip = iface_ipv4(name)
    if ip and ip.startswith("127."):
        ip = None
    parsed = _ss_ul_recv(ip, name)
    if parsed is not None:
        return float(parsed[0]), float(parsed[1])
    return 0.0, 0.0


def latency_ms() -> float | None:
    """Application E2E one-way delay (ms): client_recv - t_send.

    Only when the to-server (5G) iface is up. Prefer a fresh sample written by
    the client app. HTTP /api/e2e is opt-in (EXP4_LATENCY_HTTP=1) and is bound
    to the 5G source IP so it cannot use the console macvlan.
    """
    if not _to_server_path_ready():
        return None
    mode = (_env("EXP4_LATENCY_MODE") or "app").lower()
    if mode in ("ping", "icmp"):
        return _ping_ms()
    from_app = _e2e_from_file(_env("EXP4_APP_LATENCY_FILE") or "/tmp/exp4_app_latency_ms")
    if from_app is not None:
        return from_app
    from_file = _e2e_from_file()
    if from_file is not None:
        return from_file
    if _use_http_e2e():
        http = _e2e_from_http()
        if http is not None:
            return http
    if mode == "app":
        return None
    return _ping_ms()


def _to_server_path_ready() -> bool:
    """True when the simulated-5G / PDU iface exists (not the console macvlan)."""
    if _ts is not None:
        return _ts.path_ready()
    no5g = _env("SCHEME_ID") == "exp4-no5g" or _env("EXP4_NO5G").lower() in (
        "1",
        "true",
        "yes",
    )
    try:
        nets = set(os.listdir("/sys/class/net"))
    except OSError:
        return False
    if no5g:
        want = _env("TO_SERVER_IFACE") or _env("PDU_IFACE") or "net1"
        return want in nets
    return any(n.startswith("oaitun") for n in nets)


def _use_http_e2e() -> bool:
    flag = (_env("EXP4_LATENCY_HTTP") or "").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # Default off: app file (SFTP/MQTT/CCTV/OTT probe) is the latency sample.
    return False


def _e2e_from_file(path: str | None = None, max_age_s: float = 180.0) -> float | None:
    """Last app sample from a latency file."""
    path = path or _env("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms"
    try:
        age = _env("EXP4_E2E_MAX_AGE_S")
        if age:
            max_age_s = float(age)
        st = os.stat(path)
        if (time.time() - st.st_mtime) > max_age_s:
            return None
        return _clip_latency_ms(float(open(path, encoding="utf-8").read().strip().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def _clip_latency_ms(ms: float) -> float | None:
    """Drop epoch mistakes (t_send=0 → ~56 years). Allow multi-minute SFTP OWD."""
    if ms < 0.0 or ms > 600_000.0:
        return None
    return ms


def _e2e_from_http() -> float | None:
    if not PROBE_HOST:
        return None
    port = int(_env("E2E_HTTP_PORT") or "8080")
    path = _env("E2E_HTTP_PATH") or "/api/e2e"
    try:
        if _ts is not None:
            sock = _ts.connect_tcp(PROBE_HOST, port, timeout=1.5)
        else:
            src = _env("SIM5G_IP") or _env("MULTUS_IP") or ""
            if not src:
                return None
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.5)
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
    """ICMP RTT client → app server via the live to-server iface."""
    if not PROBE_HOST:
        return None
    iface = resolve_client_iface()
    if not iface:
        return None
    cmd = ["ping", "-c", "1", "-W", "1", "-I", iface, PROBE_HOST]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=2, stderr=subprocess.STDOUT)
    except Exception:
        return None
    m = re.search(r"time[=<]([\d.]+)\s*ms", out)
    if not m:
        return None
    return float(m.group(1))


def _tx_probe_ports() -> list[int]:
    ports: list[int] = []
    raw = _env("EXP4_TX_PORT") or _env("TX_PROBE_PORT")
    if raw:
        try:
            ports.append(int(raw))
        except ValueError:
            pass
    for key in (
        "IPERF_PORT",
        "SFTP_PORT",
        "MQTT_PORT",
        "BROKER_PORT",
        "MTX_SOURCE_RTSP_PORT",
        "E2E_HTTP_PORT",
    ):
        v = _env(key)
        if not v:
            continue
        try:
            ports.append(int(v))
        except ValueError:
            continue
    at = APP_TYPE.lower()
    if at.endswith("-s1") or at.endswith("-s4"):
        ports.extend([5201, 22])
    elif at.endswith("-s2"):
        ports.append(8555)
    elif at.endswith("-s3"):
        ports.extend([80, 8080])
    elif at.endswith("-s5"):
        ports.append(1883)
    else:
        ports.extend([8080, 80, 22, 1883, 8555, 5201])
    out: list[int] = []
    for p in ports:
        if p not in out:
            out.append(p)
    return out


def _tcp_connect_ms() -> float | None:
    """TCP handshake time to the app server, bound to the to-server IPv4."""
    if not PROBE_HOST:
        return None
    for port in _tx_probe_ports():
        t0 = time.monotonic()
        sock = None
        try:
            if _ts is not None:
                sock = _ts.connect_tcp(PROBE_HOST, port, timeout=1.5)
            else:
                src = _env("SIM5G_IP") or _env("MULTUS_IP") or ""
                if not src:
                    return None
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.5)
                sock.bind((src, 0))
                sock.connect((PROBE_HOST, port))
            ms = (time.monotonic() - t0) * 1000.0
        except Exception:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        return _clip_latency_ms(ms)
    return None


def tx_latency_ms() -> float | None:
    """Transmission delay on the to-server path (ICMP RTT, else TCP connect)."""
    if not _to_server_path_ready():
        return None
    ping = _ping_ms()
    if ping is not None:
        return _clip_latency_ms(ping)
    return _tcp_connect_ms()


def publish(fields: dict[str, float], iface: str | None = None) -> None:
    iface = iface or IFACE
    tags = (
        f"profile_name=exp4,slice_id={_tag(SLICE_ID)},app_type={_tag(APP_TYPE)},"
        f"app_name={_tag(APP_NAME)},cluster={_tag(CLUSTER)},origin={_tag(ORIGIN)},"
        f"scheme={_tag(SCHEME_ID)},iface={_tag(iface)}"
    )
    fl = ",".join(f"{k}={v:.6f}" for k, v in fields.items())
    body = f"{MEASUREMENT},{tags} {fl} {time.time_ns()}\n"
    req = urllib.request.Request(WRITE_URL, data=body.encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=4):
        pass


def loop_server() -> None:
    dl_iface = to_client_iface() or "net1"
    print(
        f"[exp4-influx] server {APP_TYPE} slice={SLICE_ID} "
        f"CPU/RAM/GPU/VRAM + TCP Send-Q/notsent/retrans/cwnd/rwnd on {dl_iface} "
        f"(TO_CLIENT_IFACE) + optional app latency -> {INFLUX_URL} every {INTERVAL_S}s",
        flush=True,
    )
    cpu_prev = None
    while True:
        time.sleep(max(0.2, INTERVAL_S))
        cpu, cpu_prev = cpu_m(cpu_prev)
        gpu_pct, vram_mb = gpu_stats()
        sendq, notsent, retrans, cwnd, rwnd = dl_tcp_queues(dl_iface)
        fields = {
            "cpu_m": cpu,
            "mem_mb": mem_mb(),
            "gpu_pct": gpu_pct,
            "vram_mb": vram_mb,
            "tcp_sendq_bytes": sendq,
            "tcp_notsent_bytes": notsent,
            "tcp_retrans": retrans,
            "tcp_cwnd": cwnd,
            "tcp_rwnd_bytes": rwnd,
        }
        lat = _e2e_from_file()
        if lat is not None:
            fields["latency_ms"] = lat
        try:
            publish(fields)
        except Exception as exc:
            print(f"[exp4-influx] write error: {exc}", flush=True)


def resolve_client_iface() -> str | None:
    """Live to-server iface (5G oaitun* / sim5G net1). Never the console macvlan."""
    if _ts is not None:
        return _ts.detect_to_server_iface()
    names: list[str] = []
    for n in (_env("TO_SERVER_IFACE"), _env("PDU_IFACE"), IFACE):
        if n and n not in names:
            names.append(n)
    no5g = _env("SCHEME_ID") == "exp4-no5g" or _env("EXP4_NO5G").lower() in (
        "1",
        "true",
        "yes",
    )
    if not no5g:
        try:
            for name in sorted(os.listdir("/sys/class/net")):
                if name.startswith("oaitun") and name not in names:
                    names.append(name)
        except OSError:
            pass
    for name in names:
        if name in ("net2", "eth0", "eth1", "lo"):
            continue
        if iface_rx_bytes(name) is not None:
            return name
    return None


def loop_client() -> None:
    hint = (_ts.configured_to_server_name() if _ts is not None else None) or IFACE
    print(
        f"[exp4-influx] client {APP_TYPE} slice={SLICE_ID} "
        f"iface={hint} (auto-detect oaitun after PDU) probe={PROBE_HOST or '-'} "
        f"DL+app/tx latency + Recv-Q/rwnd on to-server only -> {INFLUX_URL} every {INTERVAL_S}s",
        flush=True,
    )
    if _ts is not None:
        _ts.ensure_pin_watch()
    iface = resolve_client_iface()
    last_rx = iface_rx_bytes(iface) if iface else None
    last_t = time.monotonic()
    while True:
        time.sleep(max(0.2, INTERVAL_S))
        now = time.monotonic()
        dt = max(0.2, now - last_t)
        last_t = now
        live = resolve_client_iface()
        if live is None:
            fields = {
                "throughput_dl_mbps": 0.0,
                "tcp_recvq_bytes": 0.0,
                "tcp_rwnd_bytes": 0.0,
            }
            try:
                publish(fields, iface=hint)
            except Exception as exc:
                print(f"[exp4-influx] write error: {exc}", flush=True)
            last_rx = None
            iface = None
            continue
        if live != iface:
            iface = live
            last_rx = iface_rx_bytes(iface)
            print(f"[exp4-influx] to-server iface -> {iface}", flush=True)
            continue
        rx = iface_rx_bytes(iface)
        dl_mbps = 0.0
        if rx is not None and last_rx is not None:
            dl_mbps = max(0.0, (rx - last_rx) * 8.0 / dt / 1e6)
        last_rx = rx if rx is not None else last_rx
        recvq, rwnd = client_tcp_recv(iface)
        fields: dict[str, float] = {
            "throughput_dl_mbps": dl_mbps,
            "tcp_recvq_bytes": recvq,
            "tcp_rwnd_bytes": rwnd,
        }
        lat = latency_ms()
        if lat is not None:
            fields["latency_ms"] = lat
        tx = tx_latency_ms()
        if tx is not None:
            fields["tx_latency_ms"] = tx
        combo = _e2e_from_file(_env("EXP4_COMBO_E2E_FILE") or "/tmp/exp4_combo_e2e_latency_ms")
        if combo is not None:
            fields["e2e_latency_ms"] = combo
        elif lat is not None and tx is not None:
            fields["e2e_latency_ms"] = lat + tx
        try:
            publish(fields, iface=iface)
        except Exception as exc:
            print(f"[exp4-influx] write error: {exc}", flush=True)


def main() -> None:
    # One publisher per origin: outer wrapper may start /exp4 while entrypoint
    # still launches the baked path.
    lock_path = f"/tmp/exp4-influx-{ORIGIN}.lock"
    try:
        lock_fd = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.seek(0)
        lock_fd.truncate()
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except OSError:
        print(f"[exp4-influx] another {ORIGIN} publisher holds {lock_path}; exit", flush=True)
        return
    if ORIGIN == "client":
        loop_client()
    else:
        loop_server()


if __name__ == "__main__":
    main()
