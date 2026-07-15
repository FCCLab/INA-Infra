#!/usr/bin/env python3
"""
Ping from oai-ue-{N} pods (K8s oai-slice-deployment) toward mgmt-0.

Default target: 10.1.132.200 (mgmt-0), via oaitun_ue*.
Use --dnn for per-slice DNN GW 10.1.{N}.1 instead.

Examples:
  ./scripts/test_ping.py
  ./scripts/test_ping.py --ue1 --ue3 --count 10
  ./scripts/test_ping.py --host 10.1.132.11 --ue1
  ./scripts/test_ping.py --dnn
  ./scripts/test_ping.py --tmux
  ./scripts/test_ping.py --tmux --ue1 --ue2 --session oai_ping
  ./scripts/test_ping.py --list-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_CONFIG = REPO_ROOT / "utils" / "ssh_config" / "config"
DEFAULT_EDGE_HOST = "edge-0"
DEFAULT_UE_NS = "oai-slice-deployment"
DEFAULT_COUNT = 5
DEFAULT_HOST = os.environ.get("OAI_TEST_HOST", "10.1.132.200")  # mgmt-0
UE_LABEL_FMT = "app.kubernetes.io/name=oai-ue-{n}"
OAITUN_CANDIDATES = ("oaitun_ue1", "oaitun_ue0")


def run(argv: list[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def ssh_run(
    host: str,
    remote: str,
    *,
    ssh_config: Path,
    timeout: Optional[float] = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
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
    if capture:
        return run(argv, timeout=timeout)
    return subprocess.run(argv, timeout=timeout)


def remote_kubectl(
    host: str,
    ns: str,
    args: list[str],
    *,
    ssh_config: Path,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    remote = " ".join(shlex.quote(a) for a in ["kubectl", "-n", ns, *args])
    return ssh_run(host, remote, ssh_config=ssh_config, timeout=timeout)


def dnn_gw(slice_n: int) -> str:
    return f"10.1.{slice_n}.1"


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
            ["get", "pods", "-l", UE_LABEL_FMT.format(n=n), "-o", "json"],
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
        found.append((n, running[0]["metadata"]["name"]))
    return found


def detect_oaitun_and_ip(
    edge: str,
    ue_ns: str,
    pod: str,
    *,
    ssh_config: Path,
) -> tuple[Optional[str], Optional[str]]:
    remote = " ".join(
        shlex.quote(a)
        for a in [
            "kubectl",
            "-n",
            ue_ns,
            "exec",
            pod,
            "--",
            "ip",
            "-4",
            "-o",
            "addr",
            "show",
        ]
    )
    r = ssh_run(edge, remote, ssh_config=ssh_config, timeout=20)
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


def ping_ue(
    edge: str,
    ue_ns: str,
    pod: str,
    host: str,
    count: int,
    iface: str,
    *,
    ssh_config: Path,
) -> int:
    remote = " ".join(
        shlex.quote(a)
        for a in [
            "kubectl",
            "-n",
            ue_ns,
            "exec",
            pod,
            "--",
            "ping",
            "-c",
            str(count),
            "-W",
            "2",
            "-I",
            iface,
            host,
        ]
    )
    print(f"=== {pod} -> {host} via {iface} ({count} packets) ===")
    # Stream live (no capture)
    return ssh_run(edge, remote, ssh_config=ssh_config, timeout=count * 3 + 30, capture=False).returncode


def _tmux_session_alive(session: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
        ).returncode
        == 0
    )


def forever_ping_cmd(
    *,
    edge: str,
    ssh_config: Path,
    ue_ns: str,
    pod: str,
    host: str,
    iface: str,
    label: str,
    retry_delay: float,
    session: str,
) -> list[str]:
    """Pane argv: forever ping via ssh+kubectl, retry on exit; Ctrl-C kills session."""
    # No -it / ssh -t: tmux panes background the job; a forced TTY makes kubectl
    # abort ("Unable to use a TTY") and ping never starts.
    k_remote = " ".join(
        shlex.quote(a)
        for a in [
            "kubectl",
            "-n",
            ue_ns,
            "exec",
            pod,
            "--",
            "ping",
            "-I",
            iface,
            host,
        ]
    )
    ssh_q = " ".join(
        shlex.quote(a)
        for a in [
            "ssh",
            "-F",
            str(ssh_config),
            "-o",
            "BatchMode=yes",
            edge,
            k_remote,
        ]
    )
    sess = shlex.quote(session)
    script = (
        f"stop_all() {{ "
        f"trap - EXIT INT TERM; "
        f'[ -n "${{pid:-}}" ] && kill "$pid" 2>/dev/null; '
        f'wait "$pid" 2>/dev/null; '
        f"tmux kill-session -t {sess} 2>/dev/null || true; "
        f"exit 130; "
        f"}}; "
        f"trap stop_all INT TERM; "
        f"echo '=== {label} ping {host} via {iface} forever "
        f"(Ctrl-C stops all) ==='; "
        f"while true; do "
        f"{ssh_q} & "
        f"pid=$!; "
        f'wait "$pid"; '
        f"rc=$?; "
        f"pid=; "
        f'if [ "$rc" -eq 130 ] || [ "$rc" -gt 128 ]; then '
        f'echo "=== {label} interrupted (rc=$rc); stopping ==="; '
        f"stop_all; "
        f"fi; "
        f'echo "=== {label} ping exited rc=$rc; retry in {retry_delay}s ==="; '
        f"sleep {retry_delay}; "
        f"done"
    )
    return ["bash", "-lc", script]


def open_tmux(
    targets: list[tuple[int, str, str, str, str]],
    *,
    edge: str,
    ssh_config: Path,
    ue_ns: str,
    session: str,
    retry_delay: float,
) -> int:
    if not shutil.which("tmux"):
        print("tmux not found; install tmux or run without --tmux", file=sys.stderr)
        return 1
    if _tmux_session_alive(session):
        print(f"Killing existing tmux session {session}")
        subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    cmds = [
        forever_ping_cmd(
            edge=edge,
            ssh_config=ssh_config,
            ue_ns=ue_ns,
            pod=pod,
            host=host,
            iface=iface,
            label=f"ue{idx}",
            retry_delay=retry_delay,
            session=session,
        )
        for idx, pod, iface, _pdu, host in targets
    ]
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-n", "ue", *cmds[0]],
        check=True,
    )
    target = f"{session}:ue"
    for cmd in cmds[1:]:
        subprocess.run(["tmux", "split-window", "-t", target, *cmd], check=True)
        subprocess.run(["tmux", "select-layout", "-t", target, "tiled"], check=False)
    subprocess.run(["tmux", "select-layout", "-t", target, "tiled"], check=False)
    subprocess.run(["tmux", "set-option", "-t", session, "mouse", "on"], check=False)

    print(
        f"tmux session: {session}  |  {len(targets)} UE pane(s), ping forever"
    )
    print("Detach: Ctrl-b then d  |  Kill: Ctrl-C or "
          f"tmux kill-session -t {session}")

    def _stop(_signum=None, _frame=None) -> None:
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    prev_int = signal.signal(signal.SIGINT, _stop)
    prev_term = signal.signal(signal.SIGTERM, _stop)
    try:
        return subprocess.call(["tmux", "attach", "-t", session])
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Ping mgmt-0 (10.1.132.200) from oai-ue-* pods via oaitun",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Ping target for all UEs (default: mgmt-0)",
    )
    ap.add_argument(
        "--dnn",
        action="store_true",
        help="Ping per-slice DNN GW 10.1.N.1 instead of --host",
    )
    ap.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help="ping -c N (ignored with --tmux)",
    )
    ap.add_argument(
        "--tmux",
        action="store_true",
        help="One tmux pane per UE; ping forever (auto-retry)",
    )
    ap.add_argument("--session", default="oai_ping", help="tmux session name")
    ap.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Seconds between forever-ping retries in tmux",
    )
    ap.add_argument(
        "--iface",
        default=None,
        help="Force oaitun iface (default: auto-detect)",
    )
    ap.add_argument("--list-only", action="store_true", help="Only list UEs with PDU")
    ap.add_argument(
        "--ssh-config",
        default=os.environ.get("SSH_CFG", str(DEFAULT_SSH_CONFIG)),
        help="SSH config path",
    )
    ap.add_argument("--edge-host", default=DEFAULT_EDGE_HOST, help="SSH host for edge kubectl")
    ap.add_argument("--ue-ns", default=DEFAULT_UE_NS, help="UE namespace")
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

    targets: list[tuple[int, str, str, str, str]] = []  # idx, pod, iface, pdu, host
    print(f"Selected UEs ({len(ue_pods)}):")
    for idx, pod in ue_pods:
        iface, pdu = detect_oaitun_and_ip(
            args.edge_host, args.ue_ns, pod, ssh_config=ssh_config
        )
        if args.iface:
            iface = args.iface
        if not iface or not pdu:
            print(
                f"  oai-ue-{idx} ({pod}): WARNING — no oaitun/PDU; skipping",
                file=sys.stderr,
            )
            continue
        host = dnn_gw(idx) if args.dnn else args.host
        targets.append((idx, pod, iface, pdu, host))
        print(f"  ue{idx}: pod={pod} {iface}={pdu} -> {host}")

    if not targets:
        print("ERROR: no UEs with active PDU; aborting.", file=sys.stderr)
        return 1
    if args.list_only:
        return 0

    if args.tmux:
        return open_tmux(
            targets,
            edge=args.edge_host,
            ssh_config=ssh_config,
            ue_ns=args.ue_ns,
            session=args.session,
            retry_delay=args.retry_delay,
        )

    failed: list[str] = []
    for idx, pod, iface, _pdu, host in targets:
        rc = ping_ue(
            args.edge_host,
            args.ue_ns,
            pod,
            host,
            args.count,
            iface,
            ssh_config=ssh_config,
        )
        if rc != 0:
            failed.append(f"ue{idx}")

    if failed:
        print(f"FAIL: ping failed for: {', '.join(failed)}")
        return 1
    print(f"OK: all {len(targets)} UE(s) pinged ({args.count} packets each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
