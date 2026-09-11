#!/usr/bin/env python3
"""End-to-end Exp4 runner: GitOps + apps + UEs → stable → capture → plot.

Deploys one scheme at a time on the shared RAN (do not mix exp4-s*).
For each scheme: undeploy a live previous scheme (or leftover apps), push
GitOps, wait RAN/apps Ready, bring up all five UEs, wait PDU + settle TIME
of healthy DL traffic, then ``exp_start`` (capture) and ``exp_plot``.

The last scheme is left running. Pass ``--undeploy`` with ``--schemes`` to
tear the last scheme down after capture. ``--undeploy`` with no ``--schemes``
(or ``--undeploy-only``) only tears down live exp4 schemes and exits.

  python3 paper/exp4/run_experiment.py
  python3 paper/exp4/run_experiment.py --schemes s3 --duration 300
  python3 paper/exp4/run_experiment.py --schemes s3 --undeploy
  python3 paper/exp4/run_experiment.py --undeploy
  python3 paper/exp4/run_experiment.py --undeploy-only
  tmux attach -t exp4
  python3 paper/exp4/run_experiment.py --no-tmux
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
TZ = ZoneInfo("Asia/Taipei")

SCHEME_SHORTS = ("s0", "s1", "s2", "s3")
CLEAN_SHORTS = ("s0", "s1", "s2", "s3", "sx")
CLUSTERS = ("central", "regional", "edge")
KUBE_CONTEXT = {
    "central": "central@central",
    "regional": "regional@regional",
    "edge": "edge@edge",
}
REPO_FOR = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
}
APP_DEPLOY = {
    1: "application-iperf-sftp",
    2: "application-cctv",
    3: "application-ott",
    4: "application-cpu-offload",
    5: "application-iot",
}
EDGE = KUBE_CONTEXT["edge"]
MIN_PDU_RTT_MS = 8.0
STATUS_TIMEOUT = 6.0
DEFAULT_TMUX_SESSION = os.environ.get("EXP4_TMUX_SESSION", "exp4")
GITEA_HOST = os.environ.get("GITEA_HOST", "10.1.132.200")
GITEA_PORT = os.environ.get("GITEA_PORT", "3000")
GITEA_USER = os.environ.get("GITEA_USER", "nephio")
GITEA_PASS = os.environ.get("GITEA_PASS", "secret")
DEFAULT_KUBECONFIG = ":".join(
    str(Path.home() / p)
    for p in (".kube/config", ".kube/config-central", ".kube/config-regional", ".kube/config-edge")
)


def ensure_kubeconfig() -> None:
    os.environ.setdefault("KUBECONFIG", DEFAULT_KUBECONFIG)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _tmux_bin() -> Optional[str]:
    found = shutil.which("tmux")
    if found:
        return found
    local = Path.home() / ".local/bin/tmux"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def _tmux_session_alive(tmux: str, name: str) -> bool:
    has = subprocess.run(
        [tmux, "has-session", "-t", f"={name}"],
        capture_output=True,
        text=True,
    )
    if has.returncode != 0:
        return False
    panes = subprocess.run(
        [tmux, "list-panes", "-t", name, "-F", "#{pane_dead}"],
        capture_output=True,
        text=True,
    )
    if panes.returncode != 0:
        return False
    flags = [(line or "").strip() for line in (panes.stdout or "").splitlines() if line.strip()]
    return bool(flags) and all(f == "0" for f in flags)


def _attach_tmux(tmux: str, session: str) -> None:
    if sys.stdin.isatty() and sys.stdout.isatty():
        os.execvp(tmux, [tmux, "attach-session", "-t", session])
    print(f"tmux session {session} is running; attach with: tmux attach -t {session}", flush=True)
    raise SystemExit(0)


def ensure_tmux(argv: list[str]) -> None:
    """Re-exec inside tmux session ``exp4`` unless already attached or ``--no-tmux``."""
    if os.environ.get("EXP4_NO_TMUX") == "1":
        return
    if os.environ.get("TMUX"):
        return
    if "-h" in argv or "--help" in argv or "--no-tmux" in argv:
        return
    if "--undeploy-only" in argv:
        return
    if "--undeploy" in argv and not any(
        a == "--schemes" or a.startswith("--schemes=") for a in argv
    ):
        return
    session = DEFAULT_TMUX_SESSION
    for i, arg in enumerate(argv):
        if arg == "--tmux-session" and i + 1 < len(argv):
            session = argv[i + 1]
        elif arg.startswith("--tmux-session="):
            session = arg.split("=", 1)[1]
    tmux = _tmux_bin()
    if tmux is None:
        print("warn: tmux not found; running in foreground (install tmux or use ~/.local/bin/tmux)", flush=True)
        return
    if _tmux_session_alive(tmux, session):
        print(f"tmux session {session} already running; attaching", flush=True)
        _attach_tmux(tmux, session)
    subprocess.run([tmux, "kill-session", "-t", f"={session}"], capture_output=True)
    inner = [sys.executable, "-u", str(Path(__file__).resolve()), "--no-tmux", *argv]
    log = HERE / "run_experiment.log"
    cmd = (
        f"cd {shlex.quote(str(REPO_ROOT))} && "
        f"export KUBECONFIG={shlex.quote(DEFAULT_KUBECONFIG)} PYTHONUNBUFFERED=1 && "
        f"{shlex.join(inner)} 2>&1 | tee -a {shlex.quote(str(log))}"
    )
    print(f"starting tmux session {session} …", flush=True)
    created = subprocess.run(
        [tmux, "new-session", "-d", "-s", session, "-n", "run", "bash", "-lc", cmd]
    )
    if created.returncode != 0:
        print("warn: tmux new-session failed; running in foreground", flush=True)
        return
    subprocess.run([tmux, "set-option", "-t", session, "mouse", "on"], capture_output=True)
    subprocess.run([tmux, "set-option", "-t", session, "remain-on-exit", "on"], capture_output=True)
    _attach_tmux(tmux, session)


def load_scheme(short: str):
    path = HERE / short / "scheme.py"
    if not path.is_file():
        raise SystemExit(f"missing scheme package: {path}")
    spec = importlib.util.spec_from_file_location(f"exp4_{short}_scheme", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def banner(msg: str) -> None:
    print("=" * 64, flush=True)
    print(f" {msg}", flush=True)
    print("=" * 64, flush=True)


def run(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    check: bool = False,
    capture: bool = True,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        check=check,
        capture_output=capture,
        text=True,
        env=merged,
    )


def kubectl(ctx: str, args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return run(
        ["kubectl", f"--context={ctx}", "--request-timeout=20s", *args],
        timeout=timeout,
    )


def scheme_has_workloads(short: str) -> bool:
    """True if the scheme namespace still has application or UE pods."""
    ns = load_scheme(short).NAMESPACE
    names = tuple(APP_DEPLOY.values()) + tuple(f"oai-ue-slice-{i}-client" for i in APP_DEPLOY)
    for cluster in CLUSTERS:
        listed = kubectl(
            KUBE_CONTEXT[cluster],
            ["-n", ns, "get", "pods", "--no-headers", "-o", "custom-columns=NAME:.metadata.name"],
            timeout=20,
        )
        for line in (listed.stdout or "").splitlines():
            name = (line or "").strip()
            if not name or name.startswith("No resources"):
                continue
            if any(name.startswith(n) for n in names):
                return True
    return False


def py_script(path: Path, extra: list[str], *, timeout: Optional[float] = None) -> int:
    cmd = [sys.executable, "-u", str(path), *extra]
    print(f"+ {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), timeout=timeout)
    return int(res.returncode)


def git_auth(args: list[str], *, cwd: Path, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    instead = (
        f"url.http://{GITEA_USER}:{GITEA_PASS}@{GITEA_HOST}:{GITEA_PORT}/"
        f".insteadOf=http://{GITEA_HOST}:{GITEA_PORT}/"
    )
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return run(["git", "-c", instead, *args], cwd=cwd, timeout=timeout, env=env)


def push_gitea_only(message: str, clusters: tuple[str, ...] = CLUSTERS) -> None:
    """Commit dirty GitOps trees and push Gitea only (Config Sync; skip GitHub)."""
    for cluster in clusters:
        src = REPO_ROOT / "repos" / REPO_FOR[cluster]
        if not src.exists():
            print(f"  skip push: missing {src}", flush=True)
            continue
        add = run(["git", "add", "-A"], cwd=src, timeout=30)
        if add.returncode != 0:
            print(f"  warn: git add {src.name}: {(add.stderr or add.stdout).strip()}", flush=True)
            continue
        staged = run(["git", "diff", "--staged", "--quiet"], cwd=src, timeout=15)
        if staged.returncode != 0:
            commit = run(
                [
                    "git",
                    "-c",
                    "user.name=nephio-gitops",
                    "-c",
                    "user.email=nephio@nephio.org",
                    "commit",
                    "-m",
                    message,
                ],
                cwd=src,
                timeout=30,
            )
            if commit.returncode != 0:
                print(f"  warn: commit {src.name}: {(commit.stderr or commit.stdout).strip()}", flush=True)
        print(f"  pushing gitea {src.name} …", flush=True)
        pull = git_auth(["pull", "--no-edit", "--no-rebase", "gitea", "main"], cwd=src, timeout=45)
        if pull.returncode != 0:
            print(f"  warn: gitea pull {src.name}: {(pull.stderr or pull.stdout).strip()}", flush=True)
            run(["git", "merge", "--abort"], cwd=src, timeout=15)
        push = git_auth(["push", "gitea", "HEAD:main"], cwd=src, timeout=60)
        if push.returncode != 0:
            raise SystemExit(
                f"gitea push failed for {src.name}:\n{(push.stderr or push.stdout).strip()}"
            )
        print(f"  gitea {src.name} ok", flush=True)


def strip_finalizers(ctx: str, ns: str) -> None:
    for resource in ("nfdeployment", "nfconfig"):
        listed = kubectl(ctx, ["-n", ns, "get", resource, "-o", "name"], timeout=25)
        for line in (listed.stdout or "").splitlines():
            obj = line.strip()
            if not obj:
                continue
            kubectl(
                ctx,
                ["-n", ns, "patch", obj, "--type", "merge", "-p", '{"metadata":{"finalizers":[]}}'],
                timeout=25,
            )


def wait_ns_gone(ctx: str, ns: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = kubectl(ctx, ["get", "ns", ns, "-o", "jsonpath={.status.phase}"], timeout=25)
        out = (res.stdout or "").strip()
        if res.returncode != 0 or not out:
            print(f"  {ctx}: ns {ns} gone", flush=True)
            return
        if out == "Terminating":
            strip_finalizers(ctx, ns)
            kubectl(
                ctx,
                ["patch", "ns", ns, "--type", "merge", "-p", '{"metadata":{"finalizers":[]}}'],
                timeout=25,
            )
        time.sleep(4)
    print(f"  warn: {ctx} ns {ns} still present after {timeout_s:.0f}s", flush=True)


def undeploy_scheme(short: str, *, no_push: bool, wait: bool = True) -> None:
    script = HERE / short / "undeploy.py"
    extra = ["--no-push"] if no_push else []
    banner(f"Undeploy {short}")
    rc = py_script(script, extra, timeout=240)
    if rc != 0:
        print(f"  warn: undeploy {short} rc={rc}", flush=True)
    scheme = load_scheme(short)
    ns = scheme.NAMESPACE
    for cluster in CLUSTERS:
        ctx = KUBE_CONTEXT[cluster]
        strip_finalizers(ctx, ns)
        kubectl(
            ctx,
            [
                "delete",
                "namespace",
                ns,
                "--ignore-not-found=true",
                "--grace-period=0",
                "--wait=false",
            ],
            timeout=30,
        )
        if wait:
            wait_ns_gone(ctx, ns, timeout_s=120)


def wait_undeployed(shorts: list[str], timeout_s: float = 120.0) -> None:
    for short in shorts:
        ns = load_scheme(short).NAMESPACE
        for cluster in CLUSTERS:
            wait_ns_gone(KUBE_CONTEXT[cluster], ns, timeout_s=timeout_s)


def undeploy_only(shorts: list[str], *, no_push: bool) -> int:
    banner("Undeploy only")
    print(f"  schemes={shorts} no_push={no_push}", flush=True)
    for short in shorts:
        undeploy_scheme(short, no_push=True, wait=False)
    if not no_push:
        push_gitea_only("exp4: undeploy all schemes")
    wait_undeployed(shorts)
    print("Done.", flush=True)
    return 0


def deploy_gitops(short: str, *, no_push: bool, gitea_only: bool) -> None:
    script = HERE / short / "deploy.py"
    extra = ["--no-push"] if (no_push or gitea_only) else []
    banner(f"Deploy GitOps {short}")
    rc = py_script(script, extra, timeout=180)
    if rc != 0:
        raise SystemExit(f"deploy.py {short} failed (rc={rc})")
    if gitea_only and not no_push:
        scheme = load_scheme(short)
        push_gitea_only(
            f"exp4: deploy {scheme.SCHEME_ID} ({scheme.SCHEME_NAME}) to {scheme.NAMESPACE}"
        )


def check_configsync() -> None:
    script = REPO_ROOT / "scripts" / "check-configsync.sh"
    if not script.is_file():
        return
    print("+ ./scripts/check-configsync.sh", flush=True)
    try:
        res = run([str(script), "central", "regional", "edge"], cwd=REPO_ROOT, timeout=90)
        sys.stdout.write(res.stdout or "")
        if res.returncode != 0:
            print(f"  warn: check-configsync rc={res.returncode}", flush=True)
            sys.stderr.write(res.stderr or "")
    except subprocess.TimeoutExpired:
        print("  warn: check-configsync timed out", flush=True)


def _deploy_ready(item: dict) -> bool:
    spec_r = int(item.get("spec", {}).get("replicas") or 0)
    if spec_r <= 0:
        return True
    status = item.get("status") or {}
    ready = int(status.get("readyReplicas") or 0)
    avail = int(status.get("availableReplicas") or 0)
    return ready >= spec_r and avail >= spec_r


def _required_deploys(scheme: Any) -> list[tuple[str, str]]:
    req = [(EDGE, "oai-cu-cp"), (EDGE, "oai-du")]
    if int(getattr(scheme, "XAPP_REPLICAS", 0) or 0) > 0:
        req.append((EDGE, "oai-flexric"))
        req.append((EDGE, "nws-xapp"))
    for sid, spec in scheme.SLICES.items():
        req.append((KUBE_CONTEXT[spec["cu"]], f"oai-cu-up-{sid}"))
        req.append((KUBE_CONTEXT[spec["app"]], APP_DEPLOY[sid]))
    return req


def _count_upf_ready(scheme: Any) -> int:
    ns = scheme.NAMESPACE
    ready = 0
    for cluster in CLUSTERS:
        pods = kubectl(KUBE_CONTEXT[cluster], ["-n", ns, "get", "pods", "-o", "json"], timeout=30)
        if pods.returncode != 0:
            continue
        try:
            items = json.loads(pods.stdout or "{}").get("items") or []
        except json.JSONDecodeError:
            continue
        for item in items:
            name = (item.get("metadata") or {}).get("name") or ""
            if "upf-slice-" not in name.lower():
                continue
            phase = (item.get("status") or {}).get("phase")
            ready_cs = [
                c for c in (item.get("status") or {}).get("containerStatuses") or [] if c.get("ready")
            ]
            if phase == "Running" and ready_cs:
                ready += 1
    return ready


def wait_network_apps(scheme: Any, timeout_s: float) -> None:
    """Wait RAN (CU-CP/DU/CU-UP), UPF pods, and application servers."""
    ns = scheme.NAMESPACE
    needed = _required_deploys(scheme)
    n_upf = len(scheme.SLICES)
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        pending: list[str] = []
        by_ctx: dict[str, dict[str, dict]] = {}
        for ctx, _name in needed:
            if ctx in by_ctx:
                continue
            res = kubectl(ctx, ["-n", ns, "get", "deploy", "-o", "json"], timeout=30)
            if res.returncode != 0:
                by_ctx[ctx] = {}
                continue
            try:
                items = json.loads(res.stdout or "{}").get("items") or []
            except json.JSONDecodeError:
                by_ctx[ctx] = {}
                continue
            by_ctx[ctx] = {(it.get("metadata") or {}).get("name"): it for it in items}
        for ctx, name in needed:
            items = by_ctx.get(ctx) or {}
            item = items.get(name)
            if item is None:
                pending.append(f"{ctx}/{name}")
            elif not _deploy_ready(item):
                pending.append(f"{ctx}/{name}")
        upf_ready = _count_upf_ready(scheme)
        if upf_ready < n_upf:
            pending.append(f"upf {upf_ready}/{n_upf}")
        msg = ", ".join(pending[:12]) if pending else "Ready"
        if msg != last:
            print(f"  wait RAN/apps: {msg}", flush=True)
            last = msg
        if not pending:
            print("  RAN + application deployments Ready", flush=True)
            return
        time.sleep(8)
    raise SystemExit(f"{scheme.SCHEME_ID}: RAN/apps not Ready after {timeout_s:.0f}s ({last})")


def wait_ue_ready(ns: str, sid: int, timeout_s: float) -> None:
    name = f"oai-ue-slice-{sid}-client-1"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = kubectl(
            EDGE,
            ["-n", ns, "get", "deploy", name, "-o", "jsonpath={.status.readyReplicas}"],
            timeout=25,
        )
        if (res.stdout or "").strip() == "1":
            print(f"  slice {sid}: UE pod Ready", flush=True)
            return
        time.sleep(3)
    raise SystemExit(f"UE {sid} not Ready after {timeout_s:.0f}s")


def _ue_exec(ns: str, sid: int, bash: str) -> str:
    res = run(
        [
            "kubectl",
            f"--context={EDGE}",
            "--request-timeout=20s",
            "-n",
            ns,
            "exec",
            f"deploy/oai-ue-slice-{sid}-client-1",
            "-c",
            "ue",
            "--",
            "bash",
            "-c",
            bash,
        ],
        timeout=25,
    )
    return (res.stdout or "") + (res.stderr or "")


def _ping_rtt_ms(text: str) -> Optional[float]:
    m = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", text)
    if m:
        return float(m.group(1))
    m = re.search(r"time[=<]([\d.]+)\s*ms", text)
    if m:
        return float(m.group(1))
    return None


def wait_pdu(ns: str, sid: int, app_ip: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = _ue_exec(
            ns,
            sid,
            "ip -4 addr show oaitun_ue1 2>/dev/null | awk '/inet /{print $2}'; "
            "ip route get " + app_ip + " 2>/dev/null | head -1; "
            f"ping -c 1 -W 3 {app_ip} 2>&1 | tail -3",
        )
        rtt = _ping_rtt_ms(last)
        via_tun = "oaitun_ue1" in last or "10.140." in last
        if via_tun and rtt is not None and rtt >= MIN_PDU_RTT_MS:
            print(f"  slice {sid}: PDU {app_ip} rtt={rtt:.0f} ms", flush=True)
            return
        extra = f" rtt={rtt:.1f}ms" if rtt is not None else ""
        print(f"  slice {sid}: waiting PDU{extra} …", flush=True)
        time.sleep(4)
    raise SystemExit(f"UE {sid} PDU to {app_ip} not ready:\n{last}")


def fetch_status(console_ip: str) -> Optional[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://{console_ip}:8090/api/status", timeout=STATUS_TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def start_app(sid: int, console_ip: str) -> None:
    if sid == 2:
        return
    last = ""
    for _ in range(10):
        try:
            req = urllib.request.Request(
                f"http://{console_ip}:8090/api/app/start", method="POST", data=b""
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
            print(f"  slice {sid}: POST /api/app/start", flush=True)
            return
        except Exception as exc:
            last = str(exc)
            time.sleep(3)
    print(f"  slice {sid}: app/start not ready ({last})", flush=True)


def traffic_ok(sid: int, d: dict[str, Any], prev: Optional[dict[str, Any]]) -> tuple[bool, str]:
    app = d.get("app") or {}
    if sid in (1, 4):
        success = int(d.get("success") or 0)
        running = bool(app.get("running"))
        if running and success > 0:
            return True, f"sftp running success={success}"
        return False, f"sftp wanted={app.get('wanted')} running={running} success={success}"
    if sid == 2:
        pdu = bool(d.get("pdu_ready"))
        mbps = float(d.get("throughput_mbps") or 0.0)
        mtx = d.get("mtx") or {}
        bytes_rx = int(mtx.get("bytesReceived") or 0)
        prev_b = int(((prev or {}).get("mtx") or {}).get("bytesReceived") or 0)
        e2e = float(d.get("e2e_ms") or 0.0)
        if pdu and (mbps > 0.5 or bytes_rx > prev_b + 50_000):
            return True, f"yolo pdu mbps={mbps:.1f} e2e={e2e:.0f} mtx_B={bytes_rx}"
        return False, f"yolo pdu={pdu} mbps={mbps:.1f} mtx_B={bytes_rx}"
    if sid == 3:
        socks = d.get("socks") or {}
        running = bool(app.get("running") or socks.get("running"))
        down = int(socks.get("bytes_down") or 0)
        prev_d = int(((prev or {}).get("socks") or {}).get("bytes_down") or 0)
        if running and down > prev_d + 100_000:
            return True, f"ott socks down={down}"
        return False, f"ott running={running} down={down}"
    if sid == 5:
        mqtt = bool(d.get("mqtt_connected"))
        dl = float((d.get("stats") or {}).get("dl_mbps") or 0.0)
        if mqtt and dl > 0.2:
            return True, f"mqtt dl={dl:.2f} Mbps"
        return False, f"mqtt={mqtt} dl={dl:.2f}"
    return False, "unknown slice"


def wait_all_stable(scheme: Any, settle_s: float, timeout_s: float) -> None:
    """Require settle_s of consecutive healthy traffic on every slice."""
    slices = scheme.SLICES
    deadline = time.time() + timeout_s
    ok_since: dict[int, Optional[float]] = {sid: None for sid in slices}
    prev: dict[int, Optional[dict[str, Any]]] = {sid: None for sid in slices}
    done: set[int] = set()
    while time.time() < deadline:
        now = time.time()
        for sid, spec in slices.items():
            if sid in done:
                continue
            d = fetch_status(spec["ue_console_ip"])
            if d is None:
                print(f"  slice {sid}: console not up …", flush=True)
                ok_since[sid] = None
                continue
            good, why = traffic_ok(sid, d, prev[sid])
            prev[sid] = d
            if good:
                if ok_since[sid] is None:
                    ok_since[sid] = now
                held = now - ok_since[sid]
                print(f"  slice {sid}: stable {held:.0f}/{settle_s:.0f}s ({why})", flush=True)
                if held >= settle_s:
                    print(f"  slice {sid}: stabilized", flush=True)
                    done.add(sid)
            else:
                ok_since[sid] = None
                print(f"  slice {sid}: not stable ({why})", flush=True)
        if done >= set(slices):
            print("  all slices stable", flush=True)
            return
        time.sleep(3)
    missing = sorted(set(slices) - done)
    raise SystemExit(f"slices {missing} did not stabilize in {timeout_s:.0f}s")


def deploy_ues(short: str) -> None:
    banner(f"Deploy UEs {short}")
    rc = py_script(HERE / short / "deploy_ue.py", [], timeout=180)
    if rc != 0:
        raise SystemExit(f"deploy_ue.py {short} failed (rc={rc})")


def capture_and_plot(short: str, duration: str) -> str:
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    try:
        dur_s = int(float(duration))
        run_id = f"{stamp}_{dur_s}s"
    except ValueError:
        run_id = f"{stamp}_{duration.replace(' ', '')}"
    banner(f"exp_start {short} run_id={run_id}")
    rc = py_script(
        HERE / "exp_start.py",
        ["--scheme", short, "--duration", duration, "--run-id", run_id, "--no-plot"],
        timeout=None,
    )
    if rc != 0:
        raise SystemExit(f"exp_start {short} failed (rc={rc})")
    banner(f"exp_plot {short} run_id={run_id}")
    rc = py_script(
        HERE / "exp_plot.py",
        ["--scheme", short, "--run-id", run_id],
        timeout=120,
    )
    if rc != 0:
        print(f"  warn: exp_plot {short} rc={rc}", flush=True)
    return run_id


def run_one(
    short: str,
    *,
    duration: str,
    settle_s: float,
    pdu_timeout: float,
    stable_timeout: float,
    gitops_timeout: float,
    no_push: bool,
    gitea_only: bool,
    undeploy_first: list[str],
    keep: bool,
) -> str:
    scheme = load_scheme(short)
    banner(
        f"{scheme.SCHEME_NAME} ns={scheme.NAMESPACE}  "
        f"PL={scheme.PL_ENABLED} PM={scheme.PM_ENABLED} PS={scheme.PS_ENABLED}"
    )
    if undeploy_first:
        for other in undeploy_first:
            undeploy_scheme(other, no_push=no_push or gitea_only, wait=False)
        if gitea_only and not no_push:
            push_gitea_only(f"exp4: clear live schemes before {scheme.SCHEME_ID}")
        wait_undeployed(undeploy_first)
    deploy_gitops(short, no_push=no_push, gitea_only=gitea_only)
    check_configsync()
    wait_network_apps(scheme, timeout_s=gitops_timeout)
    deploy_ues(short)
    for sid, spec in scheme.SLICES.items():
        wait_ue_ready(scheme.NAMESPACE, sid, timeout_s=pdu_timeout)
    for sid, spec in scheme.SLICES.items():
        wait_pdu(scheme.NAMESPACE, sid, spec["app_ip"], timeout_s=pdu_timeout)
    for sid, spec in scheme.SLICES.items():
        start_app(sid, spec["ue_console_ip"])
    wait_all_stable(scheme, settle_s=settle_s, timeout_s=stable_timeout)
    run_id = capture_and_plot(short, duration)
    if not keep:
        undeploy_scheme(short, no_push=no_push or gitea_only, wait=False)
        if gitea_only and not no_push:
            push_gitea_only(f"exp4: undeploy {scheme.SCHEME_ID} after capture")
        wait_undeployed([short])
    return run_id


def compare_runs(run_ids: dict[str, str], out: Path) -> None:
    extra: list[str] = ["--schemes", *run_ids.keys(), "--out", str(out)]
    for short, rid in run_ids.items():
        extra += [f"--{short}-run-id", rid]
    banner("compare_schemes")
    rc = py_script(HERE / "compare_schemes.py", extra, timeout=180)
    if rc != 0:
        print(f"  warn: compare_schemes rc={rc}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--schemes", nargs="+", default=list(SCHEME_SHORTS), help="s0 s1 s2 s3 (default all)")
    p.add_argument("--duration", default="300", help="capture window after stable (default 300s)")
    p.add_argument("--settle", type=float, default=30.0, help="seconds of healthy traffic before capture")
    p.add_argument("--pdu-timeout", type=float, default=180.0, help="UE Ready + PDU timeout seconds")
    p.add_argument("--stable-timeout", type=float, default=300.0, help="max wait for all slices to stabilize")
    p.add_argument("--gitops-timeout", type=float, default=900.0, help="RAN/app Ready timeout seconds")
    p.add_argument("--no-push", action="store_true", help="do not push Gitea (GitOps already applied)")
    p.add_argument(
        "--full-push",
        action="store_true",
        help="use deploy.py Gitea+GitHub push (default: Gitea-only)",
    )
    p.add_argument(
        "--undeploy",
        action="store_true",
        help="with --schemes: tear down the last scheme after capture; "
        "without --schemes: same as --undeploy-only",
    )
    p.add_argument(
        "--undeploy-only",
        action="store_true",
        help="tear down exp4 schemes and exit (no deploy/capture). Default: s0 s1 s2 s3 sx",
    )
    p.add_argument("--skip-undeploy", action="store_true", help="do not undeploy other/current schemes")
    p.add_argument("--keep-last", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--skip-compare", action="store_true", help="do not run compare_schemes.py")
    p.add_argument(
        "--compare-out",
        default="",
        help="compare_schemes output dir (default plots/s0_vs_s1_vs_s2_vs_s3)",
    )
    p.add_argument(
        "--no-tmux",
        action="store_true",
        help="run in the current terminal (default: re-exec in tmux session exp4)",
    )
    p.add_argument(
        "--tmux-session",
        default=DEFAULT_TMUX_SESSION,
        help="tmux session name (default exp4)",
    )
    args = p.parse_args()
    ensure_kubeconfig()

    schemes: list[str] = []
    for raw in args.schemes:
        for part in re.split(r"[,\s]+", raw.strip().lower()):
            if not part:
                continue
            part = part.replace("exp4-", "")
            if part not in SCHEME_SHORTS:
                print(f"error: unknown scheme {part!r} (expected s0 s1 s2 s3)", file=sys.stderr)
                return 2
            schemes.append(part)
    schemes = list(dict.fromkeys(schemes))
    schemes_explicit = any(
        a == "--schemes" or a.startswith("--schemes=") for a in sys.argv[1:]
    )
    teardown_only = bool(args.undeploy_only) or (bool(args.undeploy) and not schemes_explicit)
    if teardown_only:
        shorts = schemes if schemes_explicit else list(CLEAN_SHORTS)
        return undeploy_only(shorts, no_push=bool(args.no_push))
    if not schemes:
        print("error: no schemes", file=sys.stderr)
        return 2

    gitea_only = not args.full_push
    run_ids: dict[str, str] = {}
    print(
        f"Exp4 runner schemes={schemes} duration={args.duration}s settle={args.settle:.0f}s "
        f"gitea_only={gitea_only} undeploy={bool(args.undeploy)} "
        f"kubeconfig={os.environ.get('KUBECONFIG')}",
        flush=True,
    )
    for i, short in enumerate(schemes):
        last = i == len(schemes) - 1
        keep = bool(args.skip_undeploy) or (not args.undeploy and last)
        if args.skip_undeploy:
            undeploy_first: list[str] = []
        elif i == 0:
            if args.undeploy:
                undeploy_first = list(CLEAN_SHORTS)
            else:
                undeploy_first = [
                    s for s in CLEAN_SHORTS if s == short or scheme_has_workloads(s)
                ]
                if short not in undeploy_first:
                    undeploy_first.append(short)
        else:
            undeploy_first = [schemes[i - 1]]
        run_ids[short] = run_one(
            short,
            duration=str(args.duration),
            settle_s=float(args.settle),
            pdu_timeout=float(args.pdu_timeout),
            stable_timeout=float(args.stable_timeout),
            gitops_timeout=float(args.gitops_timeout),
            no_push=bool(args.no_push),
            gitea_only=gitea_only,
            undeploy_first=undeploy_first,
            keep=keep,
        )
        print(f"  {short} run_id={run_ids[short]}", flush=True)

    print("\nCaptured runs:", flush=True)
    for short, rid in run_ids.items():
        print(f"  {short}: {HERE / short / 'data' / rid}", flush=True)

    if not args.skip_compare and len(run_ids) >= 2:
        out = Path(args.compare_out) if args.compare_out else HERE / "plots" / "_".join(f"{s}" for s in schemes)
        if not args.compare_out and schemes == list(SCHEME_SHORTS):
            out = HERE / "plots" / "s0_vs_s1_vs_s2_vs_s3"
        compare_runs(run_ids, out)
    return 0


if __name__ == "__main__":
    try:
        ensure_tmux(sys.argv[1:])
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
    except subprocess.TimeoutExpired as exc:
        print(f"error: timeout: {exc}", file=sys.stderr)
        sys.exit(1)
