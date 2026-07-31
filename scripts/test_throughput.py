#!/usr/bin/env python3
"""
iperf3 throughput for the Nephio oai-slice-deployment (K8s).

Clients: oai-ue-{N} pods on edge (usrp), PDU via oaitun_ue*.
Servers: iperf3 on mgmt-0 bound to 10.1.132.200 (default), one port per UE
         (5201 + N - 1). Use --dnn for the old UPF DNN-GW server mode.

Default: list UEs with active PDU, start servers, sequential UL then DL.

Examples:
  ./scripts/test_throughput.py
  ./scripts/test_throughput.py --dir ul --ue1 --ue2
  ./scripts/test_throughput.py --dir both --mode parallel --time 20
  ./scripts/test_throughput.py -u --bitrate 10M
  ./scripts/test_throughput.py --tmux --dir ul
  ./scripts/test_throughput.py --dnn --dir ul
  ./scripts/test_throughput.py --list-only
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_CONFIG = REPO_ROOT / "utils" / "ssh_config" / "config"
DEFAULT_EDGE_HOST = "edge-0"
DEFAULT_CENTRAL_HOST = "central-0"
DEFAULT_REGIONAL_HOST = "regional-0"
DEFAULT_MGMT_HOST = "mgmt-0"
DEFAULT_UE_NS = "oai-slice-deployment"
DEFAULT_UPF_NS = "oai-upf"
DEFAULT_PORT = 5201
DEFAULT_TIME = 20
DEFAULT_TCP_STREAMS = 1
DEFAULT_UDP_STREAMS = 5
DEFAULT_UDP_BITRATE = "10M"
DEFAULT_TARGET_HOST = os.environ.get("OAI_TEST_HOST", "10.1.132.200")  # mgmt-0
UE_LABEL_FMT = "app.kubernetes.io/name=oai-ue-{n}"
OAITUN_CANDIDATES = ("oaitun_ue1", "oaitun_ue0")
# Slice → cluster hosting co-located UPF + CU-UP
SLICE_SITE = {1: "central", 2: "regional", 3: "edge", 4: "edge", 5: "edge"}


def upf_ssh_host(slice_n: int, *, central: str, regional: str, edge: str) -> str:
    site = SLICE_SITE.get(slice_n, "edge")
    if site == "central":
        return central
    if site == "regional":
        return regional
    return edge


def dnn_gw(slice_n: int) -> str:
    return f"10.1.{slice_n}.1"

_MBITS_RE = re.compile(
    r"^\[[\s\d]+\]\s+[\d.]+\s*-\s*([\d.]+)\s+sec\s+.*?([\d.]+)\s*Mbits/sec"
    r"(?:\s+\d+)?\s+(sender|receiver)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_MBITS_ANY_RE = re.compile(r"([\d.]+)\s*Mbits/sec")
_print_lock = threading.Lock()


@dataclass
class UeTarget:
    index: int
    pod: str
    iface: str
    pdu_ip: str
    server_ip: str
    server_host: str  # SSH host running iperf3 -s (mgmt-0 or UPF node)
    upf_pod: str  # empty when server is on mgmt
    upf_host: str
    port: int
    dnn_mode: bool = False


def run(argv: list[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def ssh_run(
    host: str,
    remote: str,
    *,
    ssh_config: Path,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "ssh",
            "-F",
            str(ssh_config),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            remote,
        ],
        timeout=timeout,
    )


def kubectl_argv(ns: str, args: list[str]) -> list[str]:
    return ["kubectl", "-n", ns, *args]


def remote_kubectl(
    host: str,
    ns: str,
    args: list[str],
    *,
    ssh_config: Path,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    remote = " ".join(shlex.quote(a) for a in kubectl_argv(ns, args))
    return ssh_run(host, remote, ssh_config=ssh_config, timeout=timeout)


def k_exec(
    host: str,
    ns: str,
    pod: str,
    cmd: list[str],
    *,
    ssh_config: Path,
    container: Optional[str] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    args = ["exec", pod]
    if container:
        args += ["-c", container]
    args += ["--", *cmd]
    return remote_kubectl(host, ns, args, ssh_config=ssh_config, timeout=timeout)


def stream_k_exec(
    host: str,
    ns: str,
    pod: str,
    cmd: list[str],
    *,
    ssh_config: Path,
    prefix: str,
    timeout: float,
    container: Optional[str] = None,
) -> tuple[int, str]:
    args = ["exec", pod]
    if container:
        args += ["-c", container]
    args += ["--", *cmd]
    remote = " ".join(shlex.quote(a) for a in kubectl_argv(ns, args))
    argv = [
        "ssh",
        "-F",
        str(ssh_config),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        host,
        remote,
    ]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        return 1, str(e)

    chunks: list[str] = []
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout

    def _emit(line: str) -> None:
        chunks.append(line)
        with _print_lock:
            sys.stdout.write(f"{prefix}{line}")
            if not line.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                _emit(f"[timeout after {timeout:.0f}s]\n")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return 124, "".join(chunks)
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                _emit(line)
        rest = proc.stdout.read() or ""
        if rest:
            for ln in rest.splitlines(keepends=True):
                _emit(ln)
        return proc.wait(), "".join(chunks)
    except Exception as e:
        proc.kill()
        return 1, "".join(chunks) + f"\n{e}"


def parse_iperf3_text(text: str, *, reverse: bool) -> float:
    want = "receiver" if reverse else "sender"
    best: Optional[float] = None
    best_end = -1.0
    for m in _MBITS_RE.finditer(text or ""):
        end_s, mbps_s, role = m.group(1), m.group(2), m.group(3).lower()
        if role != want:
            continue
        end = float(end_s)
        mbps = float(mbps_s)
        if end >= best_end:
            best_end = end
            best = mbps
    if best is not None:
        return best
    last = None
    for line in (text or "").splitlines():
        if "Mbits/sec" not in line:
            continue
        if "sender" in line.lower() or "receiver" in line.lower() or "SUM" in line:
            mm = _MBITS_ANY_RE.search(line)
            if mm:
                last = float(mm.group(1))
    if last is not None:
        return last
    raise ValueError("no Mbits/sec summary in iperf3 output")


def list_ue_pods(
    edge: str,
    ue_ns: str,
    *,
    ssh_config: Path,
    selected: Optional[set[int]] = None,
) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    want = selected or set(range(1, 6))
    for n in sorted(want):
        r = remote_kubectl(
            edge,
            ue_ns,
            [
                "get",
                "pods",
                "-l",
                UE_LABEL_FMT.format(n=n),
                "-o",
                "json",
            ],
            ssh_config=ssh_config,
            timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"kubectl get oai-ue-{n} failed: {(r.stderr or r.stdout or '').strip()}"
            )
        items = json.loads(r.stdout or "{}").get("items") or []
        running = [
            p
            for p in items
            if (p.get("status") or {}).get("phase") == "Running"
            and not (p.get("metadata") or {}).get("deletionTimestamp")
        ]
        if not running:
            continue
        name = running[0]["metadata"]["name"]
        found.append((n, name))
    return found


def find_upf_pod(host: str, upf_ns: str, slice_n: int, *, ssh_config: Path) -> Optional[str]:
    r = remote_kubectl(
        host,
        upf_ns,
        ["get", "pods", "-o", "json"],
        ssh_config=ssh_config,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"kubectl get upf pods failed: {(r.stderr or r.stdout or '').strip()}")
    prefix = f"upf-slice-{slice_n}-"
    for p in json.loads(r.stdout or "{}").get("items") or []:
        name = (p.get("metadata") or {}).get("name") or ""
        phase = (p.get("status") or {}).get("phase")
        if name.startswith(prefix) and phase == "Running":
            return name
    return None


def detect_oaitun_and_ip(
    edge: str,
    ue_ns: str,
    pod: str,
    *,
    ssh_config: Path,
) -> tuple[Optional[str], Optional[str]]:
    r = k_exec(
        edge,
        ue_ns,
        pod,
        ["ip", "-4", "-o", "addr", "show"],
        ssh_config=ssh_config,
        timeout=20,
    )
    if r.returncode != 0:
        return None, None
    text = r.stdout or ""
    for iface in OAITUN_CANDIDATES:
        m = re.search(
            rf"^\d+:\s+{re.escape(iface)}\s+inet\s+([\d.]+)/",
            text,
            re.MULTILINE,
        )
        if m:
            return iface, m.group(1)
    m = re.search(r"^\d+:\s+(oaitun_ue\d+)\s+inet\s+([\d.]+)/", text, re.MULTILINE)
    if m:
        return m.group(1), m.group(2)
    return None, None


def iperf_listening_upf(
    host: str,
    upf_ns: str,
    upf_pod: str,
    port: int,
    bind_addr: str,
    *,
    ssh_config: Path,
) -> bool:
    r = k_exec(
        host,
        upf_ns,
        upf_pod,
        ["bash", "-c", f"ss -tlnp 2>/dev/null | grep -q '{bind_addr}:{port}'"],
        ssh_config=ssh_config,
        container="debug",
        timeout=15,
    )
    return r.returncode == 0


def iperf_listening_host(
    host: str,
    port: int,
    bind_addr: str,
    *,
    ssh_config: Path,
) -> bool:
    remote = f"ss -tlnp 2>/dev/null | grep -q '{bind_addr}:{port}'"
    r = ssh_run(host, remote, ssh_config=ssh_config, timeout=15)
    return r.returncode == 0


def clear_iperf_ue(
    edge: str,
    ue_ns: str,
    targets: list[UeTarget],
    *,
    ssh_config: Path,
    quiet: bool = False,
) -> None:
    # Use -x (exact name). Do NOT pkill -f a pattern that appears in bash -c itself —
    # that SIGKILLs the exec shell (exit 137).
    for t in targets:
        k_exec(
            edge,
            ue_ns,
            t.pod,
            ["pkill", "-9", "-x", "iperf3"],
            ssh_config=ssh_config,
            timeout=15,
        )
    if not quiet:
        print(f"cleared iperf3 clients on {len(targets)} UE pod(s)")


def clear_iperf_servers(
    upf_ns: str,
    targets: list[UeTarget],
    *,
    ssh_config: Path,
    quiet: bool = False,
) -> None:
    if not targets:
        return
    if targets[0].dnn_mode:
        seen: set[str] = set()
        for t in targets:
            key = f"{t.upf_host}/{t.upf_pod}"
            if key in seen:
                continue
            seen.add(key)
            k_exec(
                t.upf_host,
                upf_ns,
                t.upf_pod,
                ["pkill", "-9", "-x", "iperf3"],
                ssh_config=ssh_config,
                container="debug",
                timeout=15,
            )
        if not quiet:
            print(f"cleared iperf3 servers on {len(seen)} UPF pod(s)")
        return

    host = targets[0].server_host
    ports = " ".join(str(t.port) for t in targets)
    remote = (
        f"for p in {ports}; do "
        f"pkill -9 -f \"iperf3.*-p \$p\" 2>/dev/null || true; "
        f"done"
    )
    ssh_run(host, remote, ssh_config=ssh_config, timeout=20)
    if not quiet:
        print(f"cleared iperf3 servers on {host} ports {[t.port for t in targets]}")


def ensure_iperf_servers(
    upf_ns: str,
    targets: list[UeTarget],
    *,
    ssh_config: Path,
) -> bool:
    if not targets:
        return False

    if targets[0].dnn_mode:
        for t in targets:
            k_exec(
                t.upf_host,
                upf_ns,
                t.upf_pod,
                ["pkill", "-9", "-x", "iperf3"],
                ssh_config=ssh_config,
                container="debug",
                timeout=15,
            )
            r = k_exec(
                t.upf_host,
                upf_ns,
                t.upf_pod,
                ["iperf3", "-s", "-B", t.server_ip, "-p", str(t.port), "-D"],
                ssh_config=ssh_config,
                container="debug",
                timeout=20,
            )
            if r.returncode != 0:
                print(
                    f"ERROR: failed to start iperf3 -s on {t.upf_pod} "
                    f"{t.server_ip}:{t.port}: {(r.stderr or r.stdout or '')[-400:]}",
                    file=sys.stderr,
                )
                return False
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if all(
                iperf_listening_upf(
                    t.upf_host,
                    upf_ns,
                    t.upf_pod,
                    t.port,
                    t.server_ip,
                    ssh_config=ssh_config,
                )
                for t in targets
            ):
                ports = ", ".join(f"{t.server_ip}:{t.port}" for t in targets)
                print(f"iperf3 servers ready: {ports}")
                return True
            time.sleep(0.3)
        missing = [
            f"{t.server_ip}:{t.port}"
            for t in targets
            if not iperf_listening_upf(
                t.upf_host,
                upf_ns,
                t.upf_pod,
                t.port,
                t.server_ip,
                ssh_config=ssh_config,
            )
        ]
        print(f"ERROR: iperf3 not listening on {missing}", file=sys.stderr)
        return False

    host = targets[0].server_host
    # Ensure iperf3 exists on mgmt-0.
    check = ssh_run(host, "command -v iperf3", ssh_config=ssh_config, timeout=15)
    if check.returncode != 0:
        print(
            f"ERROR: iperf3 not found on {host}; install with: "
            f"ssh {host} 'sudo apt-get install -y iperf3'",
            file=sys.stderr,
        )
        return False

    for t in targets:
        ssh_run(
            host,
            f"pkill -9 -f 'iperf3.*-p {t.port}' 2>/dev/null || true",
            ssh_config=ssh_config,
            timeout=15,
        )
        r = ssh_run(
            host,
            f"iperf3 -s -B {shlex.quote(t.server_ip)} -p {t.port} -D",
            ssh_config=ssh_config,
            timeout=20,
        )
        if r.returncode != 0:
            print(
                f"ERROR: failed to start iperf3 -s on {host} "
                f"{t.server_ip}:{t.port}: {(r.stderr or r.stdout or '')[-400:]}",
                file=sys.stderr,
            )
            return False

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        if all(
            iperf_listening_host(
                t.server_host, t.port, t.server_ip, ssh_config=ssh_config
            )
            for t in targets
        ):
            ports = ", ".join(f"{t.server_ip}:{t.port}" for t in targets)
            print(f"iperf3 servers ready on {host}: {ports}")
            return True
        time.sleep(0.3)

    missing = [
        f"{t.server_ip}:{t.port}"
        for t in targets
        if not iperf_listening_host(
            t.server_host, t.port, t.server_ip, ssh_config=ssh_config
        )
    ]
    print(f"ERROR: iperf3 not listening on {missing}", file=sys.stderr)
    return False


def build_iperf_client_cmd(
    *,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    bind_ip: Optional[str],
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float = 1.0,
) -> list[str]:
    cmd = [
        "iperf3",
        "-c",
        server,
        "-p",
        str(port),
        "-t",
        str(duration),
        "-i",
        str(interval),
        "--connect-timeout",
        "5000",
    ]
    if bind_ip:
        cmd.extend(["-B", bind_ip])
    if reverse:
        cmd.append("-R")
    if udp:
        cmd.append("-u")
        if bitrate:
            cmd.extend(["-b", bitrate])
    if streams > 1:
        cmd.extend(["-P", str(streams)])
    return cmd


def run_iperf_one(
    edge: str,
    ue_ns: str,
    target: UeTarget,
    *,
    ssh_config: Path,
    duration: int,
    reverse: bool,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float,
    no_bind: bool,
) -> tuple[str, str, float, Optional[str]]:
    direction = "DL" if reverse else "UL"
    label = f"ue{target.index}"
    cmd = build_iperf_client_cmd(
        server=target.server_ip,
        port=target.port,
        duration=duration,
        reverse=reverse,
        bind_ip=None if no_bind else target.pdu_ip,
        udp=udp,
        bitrate=bitrate,
        streams=streams,
        interval=interval,
    )
    prefix = f"[{label}/{direction}] "
    with _print_lock:
        print(
            f"=== {label} {direction} -> {target.server_ip}:{target.port} "
            f"(t={duration}s{', UDP' if udp else ', TCP'}) ==="
        )
        sys.stdout.flush()
    rc, text = stream_k_exec(
        edge,
        ue_ns,
        target.pod,
        cmd,
        ssh_config=ssh_config,
        prefix=prefix,
        timeout=float(duration + 90),
    )
    if rc != 0:
        err = text.strip()
        return label, direction, 0.0, (err[-500:] if err else f"exit {rc}")
    try:
        mbps = parse_iperf3_text(text, reverse=reverse)
        with _print_lock:
            print(f"{prefix}-> summary {mbps:.2f} Mbps")
            sys.stdout.flush()
        return label, direction, mbps, None
    except ValueError as e:
        return label, direction, 0.0, str(e)


def run_sequential(
    edge: str,
    ue_ns: str,
    targets: list[UeTarget],
    *,
    ssh_config: Path,
    directions: list[str],
    duration: int,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float,
    no_bind: bool,
) -> list[tuple[str, str, float, Optional[str]]]:
    results: list[tuple[str, str, float, Optional[str]]] = []
    for direction in directions:
        reverse = direction == "DL"
        for t in targets:
            results.append(
                run_iperf_one(
                    edge,
                    ue_ns,
                    t,
                    ssh_config=ssh_config,
                    duration=duration,
                    reverse=reverse,
                    udp=udp,
                    bitrate=bitrate,
                    streams=streams,
                    interval=interval,
                    no_bind=no_bind,
                )
            )
    return results


def run_parallel(
    edge: str,
    ue_ns: str,
    targets: list[UeTarget],
    *,
    ssh_config: Path,
    directions: list[str],
    duration: int,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float,
    no_bind: bool,
) -> list[tuple[str, str, float, Optional[str]]]:
    results: list[tuple[str, str, float, Optional[str]]] = []
    for direction in directions:
        reverse = direction == "DL"
        print(f"=== parallel {direction}: {len(targets)} UE(s) ===")
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futs = [
                pool.submit(
                    run_iperf_one,
                    edge,
                    ue_ns,
                    t,
                    ssh_config=ssh_config,
                    duration=duration,
                    reverse=reverse,
                    udp=udp,
                    bitrate=bitrate,
                    streams=streams,
                    interval=interval,
                    no_bind=no_bind,
                )
                for t in targets
            ]
            for fut in as_completed(futs):
                results.append(fut.result())
    results.sort(key=lambda r: (0 if r[1] == "UL" else 1, r[0]))
    return results


def print_summary(results: list[tuple[str, str, float, Optional[str]]]) -> int:
    print("\n=== summary (Mbps) ===")
    print(f"{'UE':<8} {'dir':<4} {'Mbps':>10}  status")
    failed = 0
    for label, direction, mbps, err in results:
        if err:
            failed += 1
            print(f"{label:<8} {direction:<4} {'—':>10}  FAIL: {err.splitlines()[-1][:80]}")
        else:
            print(f"{label:<8} {direction:<4} {mbps:10.2f}  OK")
    for direction in ("UL", "DL"):
        vals = [m for c, d, m, e in results if d == direction and not e]
        if vals:
            print(
                f"  {direction} sum={sum(vals):.2f}  "
                f"median={sorted(vals)[len(vals) // 2]:.2f}  n={len(vals)}"
            )
    return 1 if failed else 0


def _tmux_session_alive(session: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
        ).returncode
        == 0
    )


def _tmux_tiled(session: str, window: str, cmds: list[list[str]]) -> None:
    target = f"{session}:{window}"
    for cmd in cmds[1:]:
        subprocess.run(["tmux", "split-window", "-t", target, *cmd], check=True)
        subprocess.run(["tmux", "select-layout", "-t", target, "tiled"], check=False)
    subprocess.run(["tmux", "select-layout", "-t", target, "tiled"], check=False)


def _ssh_kubectl_q(
    host: str,
    ssh_config: Path,
    ns: str,
    pod: str,
    cmd: list[str],
    *,
    container: Optional[str] = None,
    tty: bool = False,
) -> str:
    # Default no TTY: tmux panes background ssh; kubectl -it then aborts.
    args = ["kubectl", "-n", ns, "exec"]
    if tty:
        args.append("-it")
    args.append(pod)
    if container:
        args += ["-c", container]
    args += ["--", *cmd]
    k_remote = " ".join(shlex.quote(a) for a in args)
    ssh_argv = [
        "ssh",
        "-F",
        str(ssh_config),
        "-o",
        "BatchMode=yes",
        host,
        k_remote,
    ]
    if tty:
        ssh_argv.insert(3, "-t")
    return " ".join(shlex.quote(a) for a in ssh_argv)


def forever_iperf_client_cmd(
    target: UeTarget,
    *,
    edge: str,
    ue_ns: str,
    ssh_config: Path,
    reverse: bool,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    retry_delay: float,
    interval: float,
    no_bind: bool,
    session: str,
) -> list[str]:
    direction = "DL" if reverse else "UL"
    label = f"ue{target.index}"
    client = build_iperf_client_cmd(
        server=target.server_ip,
        port=target.port,
        duration=0,
        reverse=reverse,
        bind_ip=None if no_bind else target.pdu_ip,
        udp=udp,
        bitrate=bitrate,
        streams=streams,
        interval=interval,
    )
    ssh_q = _ssh_kubectl_q(edge, ssh_config, ue_ns, target.pod, client)
    cleanup = (
        f"ssh -F {shlex.quote(str(ssh_config))} -o BatchMode=yes {shlex.quote(edge)} "
        f"{shlex.quote(' '.join(shlex.quote(a) for a in ['kubectl', '-n', ue_ns, 'exec', target.pod, '--', 'pkill', '-9', '-x', 'iperf3']))} "
        f">/dev/null 2>&1 || true"
    )
    sess = shlex.quote(session)
    script = (
        f"cleanup() {{ {cleanup}; }}; "
        f"stop_all() {{ "
        f"trap - EXIT INT TERM; "
        f'[ -n "${{pid:-}}" ] && kill "$pid" 2>/dev/null; '
        f'wait "$pid" 2>/dev/null; '
        f"cleanup; "
        f"tmux kill-session -t {sess} 2>/dev/null || true; "
        f"exit 130; "
        f"}}; "
        f"trap stop_all INT TERM; "
        f"trap cleanup EXIT; "
        f"echo '=== {label} {direction} forever -> {target.server_ip}:{target.port} "
        f"(report every {interval:g}s; Ctrl-C stops all) ==='; "
        f"while true; do "
        f"{ssh_q} & "
        f"pid=$!; "
        f'wait "$pid"; '
        f"rc=$?; "
        f"pid=; "
        f'if [ "$rc" -eq 130 ] || [ "$rc" -gt 128 ]; then '
        f'echo "=== {label} {direction} interrupted (rc=$rc); stopping ==="; '
        f"stop_all; "
        f"fi; "
        f'echo "=== {label} {direction} exited rc=$rc; retry in {retry_delay}s ==="; '
        f"sleep {retry_delay}; "
        f"done"
    )
    return ["bash", "-lc", script]


def forever_iperf_server_cmd(
    target: UeTarget,
    *,
    upf_ns: str,
    ssh_config: Path,
    interval: float,
    retry_delay: float,
    session: str,
) -> list[str]:
    label = f"ue{target.index}"
    server_cmd = [
        "iperf3",
        "-s",
        "-B",
        target.server_ip,
        "-p",
        str(target.port),
        "-i",
        str(interval),
    ]
    if target.dnn_mode:
        ssh_q = _ssh_kubectl_q(
            target.upf_host,
            ssh_config,
            upf_ns,
            target.upf_pod,
            server_cmd,
            container="debug",
        )
        cleanup = (
            f"ssh -F {shlex.quote(str(ssh_config))} -o BatchMode=yes {shlex.quote(target.upf_host)} "
            f"{shlex.quote(' '.join(shlex.quote(a) for a in ['kubectl', '-n', upf_ns, 'exec', target.upf_pod, '-c', 'debug', '--', 'pkill', '-9', '-x', 'iperf3']))} "
            f">/dev/null 2>&1 || true"
        )
        where = f"{target.upf_pod}@{target.upf_host}"
    else:
        remote = " ".join(shlex.quote(a) for a in server_cmd)
        ssh_q = " ".join(
            shlex.quote(a)
            for a in [
                "ssh",
                "-F",
                str(ssh_config),
                "-o",
                "BatchMode=yes",
                target.server_host,
                remote,
            ]
        )
        cleanup = (
            f"ssh -F {shlex.quote(str(ssh_config))} -o BatchMode=yes "
            f"{shlex.quote(target.server_host)} "
            f"{shlex.quote(f'pkill -9 -f iperf3.*-p {target.port} || true')} "
            f">/dev/null 2>&1 || true"
        )
        where = f"{target.server_host}"
    sess = shlex.quote(session)
    script = (
        f"cleanup() {{ {cleanup}; }}; "
        f"stop_all() {{ "
        f"trap - EXIT INT TERM; "
        f'[ -n "${{spid:-}}" ] && kill "$spid" 2>/dev/null; '
        f'wait "$spid" 2>/dev/null; '
        f"cleanup; "
        f"tmux kill-session -t {sess} 2>/dev/null || true; "
        f"exit 130; "
        f"}}; "
        f"trap stop_all INT TERM; "
        f"trap cleanup EXIT; "
        f"echo '=== {label} server on {where} "
        f"{target.server_ip}:{target.port} "
        f"(report every {interval:g}s; Ctrl-C stops all) ==='; "
        f"while true; do "
        f"{ssh_q} & "
        f"spid=$!; "
        f'wait "$spid"; '
        f"rc=$?; "
        f"spid=; "
        f'if [ "$rc" -eq 130 ] || [ "$rc" -gt 128 ]; then '
        f'echo "=== {label} server interrupted (rc=$rc); stopping ==="; '
        f"stop_all; "
        f"fi; "
        f'echo "=== {label} server exited rc=$rc; retry in {retry_delay}s ==="; '
        f"sleep {retry_delay}; "
        f"done"
    )
    return ["bash", "-lc", script]


def open_tmux(
    targets: list[UeTarget],
    *,
    edge: str,
    ue_ns: str,
    upf_ns: str,
    ssh_config: Path,
    direction: str,
    session: str,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    retry_delay: float,
    interval: float,
    no_bind: bool,
) -> int:
    if not shutil.which("tmux"):
        print("tmux not found; install tmux or run without --tmux", file=sys.stderr)
        return 1
    if _tmux_session_alive(session):
        print(f"Killing existing tmux session {session}")
        subprocess.run(["tmux", "kill-session", "-t", session], check=False)
        clear_iperf_ue(edge_ns, targets, ssh_config=ssh_config, quiet=True)
        clear_iperf_servers(upf_ns, targets, ssh_config=ssh_config, quiet=True)

    reverse = direction == "DL"
    server_cmds = [
        forever_iperf_server_cmd(
            t,
            upf_ns=upf_ns,
            ssh_config=ssh_config,
            interval=interval,
            retry_delay=retry_delay,
            session=session,
        )
        for t in targets
    ]
    client_cmds = [
        forever_iperf_client_cmd(
            t,
            edge=edge,
            ue_ns=ue_ns,
            ssh_config=ssh_config,
            reverse=reverse,
            udp=udp,
            bitrate=bitrate,
            streams=streams,
            retry_delay=retry_delay,
            interval=interval,
            no_bind=no_bind,
            session=session,
        )
        for t in targets
    ]

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-n", "server", *server_cmds[0]],
        check=True,
    )
    _tmux_tiled(session, "server", server_cmds)
    time.sleep(0.8)
    subprocess.run(
        ["tmux", "new-window", "-t", session, "-n", "client", *client_cmds[0]],
        check=True,
    )
    _tmux_tiled(session, "client", client_cmds)

    subprocess.run(["tmux", "set-option", "-t", session, "mouse", "on"], check=False)
    for opt, val in (
        ("status", "on"),
        ("status-position", "bottom"),
        ("status-left", f"#[bold]{session} {direction} #[default]| "),
        ("status-right", " #[fg=colour244]Ctrl-B 0/1=server/client"),
    ):
        subprocess.run(["tmux", "set-option", "-t", session, opt, val], check=False)
    subprocess.run(["tmux", "select-window", "-t", f"{session}:server"], check=False)

    print(
        f"tmux session: {session}  |  windows: server | client  "
        f"({len(targets)} pane(s) each), {direction} forever  (-i {interval:g}s)"
    )
    print("Status: 0:server | 1:client — Ctrl-B then 0/1; Ctrl-C stops all")
    print(f"Also: tmux kill-session -t {session}")

    def _stop(_signum=None, _frame=None) -> None:
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    prev_int = signal.signal(signal.SIGINT, _stop)
    prev_term = signal.signal(signal.SIGTERM, _stop)
    try:
        rc = subprocess.call(["tmux", "attach", "-t", session])
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)
        clear_iperf_ue(edge_ns, targets, ssh_config=ssh_config, quiet=False)
        clear_iperf_servers(upf_ns, targets, ssh_config=ssh_config, quiet=False)
    return rc


def parse_directions(value: str) -> list[str]:
    v = value.strip().lower()
    if v in ("ul", "uplink"):
        return ["UL"]
    if v in ("dl", "downlink"):
        return ["DL"]
    if v in ("both", "uldl", "dlul", "all"):
        return ["UL", "DL"]
    raise argparse.ArgumentTypeError("--dir must be ul, dl, or both")


def main() -> int:
    # Keep status lines ordered when stdout is pipe-buffered.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description=(
            "iperf3 throughput for oai-ue-* → mgmt-0 (10.1.132.200) "
            "(default: sequential UL; use --dnn for UPF DNN GW)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--dir",
        type=parse_directions,
        default=parse_directions("ul"),
        help="ul | dl | both",
    )
    ap.add_argument(
        "--mode",
        choices=("sequential", "parallel"),
        default="sequential",
        help="Run UEs one-by-one or all at once",
    )
    ap.add_argument(
        "--host",
        default=DEFAULT_TARGET_HOST,
        help="iperf3 server IP (default: mgmt-0)",
    )
    ap.add_argument(
        "--server-host",
        default=DEFAULT_MGMT_HOST,
        help="SSH host running iperf3 -s (ignored with --dnn)",
    )
    ap.add_argument(
        "--dnn",
        action="store_true",
        help="Server on UPF DNN GW 10.1.N.1 instead of --host",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="iperf3 base port (mgmt mode: port+N-1 per UE; dnn: same port per UPF)",
    )
    ap.add_argument("--time", type=int, default=DEFAULT_TIME, help="iperf3 -t seconds")
    ap.add_argument(
        "--streams",
        type=int,
        default=None,
        help=(
            f"iperf3 -P streams per UE "
            f"(default: {DEFAULT_UDP_STREAMS} UDP / {DEFAULT_TCP_STREAMS} TCP)"
        ),
    )
    proto = ap.add_mutually_exclusive_group()
    proto.add_argument(
        "-u",
        "--udp",
        action="store_const",
        const="udp",
        dest="proto",
        help=f"UDP (default -P {DEFAULT_UDP_STREAMS} -b {DEFAULT_UDP_BITRATE})",
    )
    proto.add_argument(
        "-t",
        "--tcp",
        action="store_const",
        const="tcp",
        dest="proto",
        help="TCP (default)",
    )
    ap.set_defaults(proto="tcp")
    ap.add_argument(
        "--bitrate",
        default=None,
        help=f"UDP -b per stream (default {DEFAULT_UDP_BITRATE} with -u)",
    )
    ap.add_argument("--no-bind-client", action="store_true", help="Do not -B <PDU IP>")
    ap.add_argument("--skip-server", action="store_true", help="Do not (re)start iperf3 -s")
    ap.add_argument(
        "--tmux",
        action="store_true",
        help="Two tmux windows: server then client (UE forever)",
    )
    ap.add_argument("--session", default="oai_iperf", help="tmux session name")
    ap.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Seconds between forever-iperf retries in tmux",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="iperf3 -i report interval (default: 5 with --tmux, else 1)",
    )
    ap.add_argument("--list-only", action="store_true", help="Only list UEs with PDU")
    ap.add_argument(
        "--ssh-config",
        default=os.environ.get("SSH_CFG", str(DEFAULT_SSH_CONFIG)),
        help="SSH config path",
    )
    ap.add_argument("--edge-host", default=DEFAULT_EDGE_HOST, help="SSH host for edge kubectl")
    ap.add_argument(
        "--central-host",
        default=DEFAULT_CENTRAL_HOST,
        help="SSH host for central kubectl (UPF1)",
    )
    ap.add_argument(
        "--regional-host",
        default=DEFAULT_REGIONAL_HOST,
        help="SSH host for regional kubectl (UPF2)",
    )
    ap.add_argument("--ue-ns", default=DEFAULT_UE_NS, help="UE namespace")
    ap.add_argument("--upf-ns", default=DEFAULT_UPF_NS, help="UPF namespace")
    ue_grp = ap.add_argument_group("UE selection (default: all running with PDU)")
    for i in range(1, 6):
        ue_grp.add_argument(f"--ue{i}", action="store_true", help=f"Include oai-ue-{i}")
    ap.add_argument(
        "--ue",
        type=int,
        action="append",
        dest="ue_nums",
        metavar="N",
        choices=range(1, 6),
        help="Include UE N (repeatable)",
    )
    args = ap.parse_args()
    args.udp = args.proto == "udp"
    if args.streams is None:
        args.streams = DEFAULT_UDP_STREAMS if args.udp else DEFAULT_TCP_STREAMS
    if args.udp and args.bitrate is None:
        args.bitrate = DEFAULT_UDP_BITRATE
    interval = args.interval if args.interval is not None else (5.0 if args.tmux else 1.0)

    ssh_config = Path(args.ssh_config)
    if not ssh_config.is_file():
        print(f"ERROR: SSH config not found: {ssh_config}", file=sys.stderr)
        return 1

    selected: set[int] = set()
    for i in range(1, 6):
        if getattr(args, f"ue{i}"):
            selected.add(i)
    if args.ue_nums:
        selected.update(args.ue_nums)
    selected_or_all = selected or None

    directions: list[str] = (
        args.dir if isinstance(args.dir, list) else parse_directions(str(args.dir))
    )

    try:
        ue_pods = list_ue_pods(
            args.edge_host,
            args.ue_ns,
            ssh_config=ssh_config,
            selected=selected_or_all,
        )
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    if not ue_pods:
        print("No running oai-ue-* pods found", file=sys.stderr)
        return 1

    targets: list[UeTarget] = []
    print(f"Selected UEs ({len(ue_pods)}):")
    for idx, pod in ue_pods:
        iface, pdu = detect_oaitun_and_ip(
            args.edge_host, args.ue_ns, pod, ssh_config=ssh_config
        )
        if not iface or not pdu:
            print(
                f"  oai-ue-{idx} ({pod}): WARNING — no oaitun/PDU; skipping",
                file=sys.stderr,
            )
            continue
        upf_host = upf_ssh_host(
            idx,
            central=args.central_host,
            regional=args.regional_host,
            edge=args.edge_host,
        )
        if args.dnn:
            upf = find_upf_pod(upf_host, args.upf_ns, idx, ssh_config=ssh_config)
            if not upf:
                print(
                    f"  oai-ue-{idx}: ERROR — upf-slice-{idx} pod not found on {upf_host}; skipping",
                    file=sys.stderr,
                )
                continue
            server_ip = dnn_gw(idx)
            port = args.port
            t = UeTarget(
                index=idx,
                pod=pod,
                iface=iface,
                pdu_ip=pdu,
                server_ip=server_ip,
                server_host=upf_host,
                upf_pod=upf,
                upf_host=upf_host,
                port=port,
                dnn_mode=True,
            )
            print(
                f"  ue{idx}: pod={pod} {iface}={pdu} → {server_ip}:{port} "
                f"(upf={upf}@{upf_host})"
            )
        else:
            server_ip = args.host
            port = args.port + idx - 1
            t = UeTarget(
                index=idx,
                pod=pod,
                iface=iface,
                pdu_ip=pdu,
                server_ip=server_ip,
                server_host=args.server_host,
                upf_pod="",
                upf_host=upf_host,
                port=port,
                dnn_mode=False,
            )
            print(
                f"  ue{idx}: pod={pod} {iface}={pdu} → {server_ip}:{port} "
                f"(server={args.server_host})"
            )
        targets.append(t)

    if not targets:
        print("ERROR: no UEs with active PDU; aborting.", file=sys.stderr)
        return 1
    if args.list_only:
        return 0

    clear_iperf_ue(
        args.edge_host, args.ue_ns, targets, ssh_config=ssh_config, quiet=True
    )

    session_name = args.session
    if args.session == "oai_iperf":
        direction0 = directions[0] if directions else "UL"
        session_name = f"{args.session}_{direction0}"

    if args.tmux:
        if len(directions) > 1:
            print(
                "NOTE: --tmux uses first --dir only "
                f"({directions[0]}); run UL and DL in separate terminals",
                file=sys.stderr,
            )
        clear_iperf_servers(args.upf_ns, targets, ssh_config=ssh_config, quiet=True)
        where = "UPF debug" if args.dnn else args.server_host
        print(
            f"tmux: window server = iperf3 -s on {where}; "
            f"window client = UE iperf3 ({directions[0]})"
        )
        return open_tmux(
            targets,
            edge=args.edge_host,
            ue_ns=args.ue_ns,
            upf_ns=args.upf_ns,
            ssh_config=ssh_config,
            direction=directions[0],
            session=session_name,
            udp=args.udp,
            bitrate=args.bitrate,
            streams=args.streams,
            retry_delay=args.retry_delay,
            interval=interval,
            no_bind=args.no_bind_client,
        )

    if not args.skip_server:
        clear_iperf_servers(args.upf_ns, targets, ssh_config=ssh_config, quiet=True)
        if not ensure_iperf_servers(args.upf_ns, targets, ssh_config=ssh_config):
            return 1

    cleaned = {"done": False}

    def _cleanup() -> None:
        if cleaned["done"]:
            return
        cleaned["done"] = True
        clear_iperf_ue(
            args.edge_host, args.ue_ns, targets, ssh_config=ssh_config, quiet=False
        )
        if not args.skip_server:
            clear_iperf_servers(args.upf_ns, targets, ssh_config=ssh_config, quiet=False)

    atexit.register(_cleanup)

    print(
        f"Mode={args.mode}  dir={'+'.join(directions)}  time={args.time}s  "
        f"-i={interval:g}s  proto={'UDP' if args.udp else 'TCP'}  "
        f"-P={args.streams}"
        + (f"  -b={args.bitrate}/stream" if args.udp and args.bitrate else "")
    )

    try:
        if args.mode == "parallel":
            results = run_parallel(
                args.edge_host,
                args.ue_ns,
                targets,
                ssh_config=ssh_config,
                directions=directions,
                duration=args.time,
                udp=args.udp,
                bitrate=args.bitrate,
                streams=args.streams,
                interval=interval,
                no_bind=args.no_bind_client,
            )
        else:
            results = run_sequential(
                args.edge_host,
                args.ue_ns,
                targets,
                ssh_config=ssh_config,
                directions=directions,
                duration=args.time,
                udp=args.udp,
                bitrate=args.bitrate,
                streams=args.streams,
                interval=interval,
                no_bind=args.no_bind_client,
            )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    rc = print_summary(results)
    if rc == 0:
        print(f"OK: throughput test finished for {len(targets)} UE(s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
