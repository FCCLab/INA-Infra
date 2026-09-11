#!/usr/bin/env python3
"""SX: deploy one UE at a time, wait until the slice is stable, then measure.

GitOps (core + RAN + all five apps) must already be up in ``exp4-sx``.
Only UEs rotate so each slice has the cell to itself.

  python3 paper/exp4/sx/measure.py --duration 300
  python3 paper/exp4/sx/measure.py --ue 1 --settle 45
  python3 paper/exp4/sx/measure.py --ue 2,3 --duration 5m
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))  # scheme dir last so deploy_ue is sx/deploy_ue.py

from scheme import KUBE_CONTEXT, NAMESPACE, SLICES  # noqa: E402
from ue_teardown import parse_ue_ids, undeploy_ues  # noqa: E402

import deploy_ue  # noqa: E402
import exp_start  # noqa: E402

EDGE = KUBE_CONTEXT["edge"]
TZ = ZoneInfo("Asia/Taipei")
# LAN leak is <2 ms; 5G N6 ping is typically tens–hundreds of ms.
MIN_PDU_RTT_MS = 8.0
STATUS_TIMEOUT = 6.0


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _ue_exec(sid: int, bash: str) -> str:
    res = _run(
        [
            "kubectl",
            f"--context={EDGE}",
            "-n",
            NAMESPACE,
            "exec",
            f"deploy/oai-ue-slice-{sid}-client-1",
            "-c",
            "ue",
            "--",
            "bash",
            "-c",
            bash,
        ]
    )
    return (res.stdout or "") + (res.stderr or "")


def wait_ue_ready(sid: int, timeout_s: float) -> None:
    name = f"oai-ue-slice-{sid}-client-1"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = _run(
            [
                "kubectl",
                f"--context={EDGE}",
                "-n",
                NAMESPACE,
                "get",
                "deploy",
                name,
                "-o",
                "jsonpath={.status.readyReplicas}",
            ]
        )
        if (res.stdout or "").strip() == "1":
            print(f"  slice {sid}: UE pod Ready")
            return
        time.sleep(3)
    raise SystemExit(f"UE {sid} not Ready after {timeout_s:.0f}s")


def _ping_rtt_ms(text: str) -> Optional[float]:
    m = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", text)
    if m:
        return float(m.group(1))
    m = re.search(r"time[=<]([\d.]+)\s*ms", text)
    if m:
        return float(m.group(1))
    return None


def wait_pdu(sid: int, timeout_s: float) -> None:
    """oaitun_ue1 up and N6 ping over 5G (not console LAN)."""
    app_ip = SLICES[sid]["app_ip"]
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = _ue_exec(
            sid,
            "ip -4 addr show oaitun_ue1 2>/dev/null | awk '/inet /{print $2}'; "
            "ip route get " + app_ip + " 2>/dev/null | head -1; "
            f"ping -c 1 -W 3 {app_ip} 2>&1 | tail -3",
        )
        rtt = _ping_rtt_ms(last)
        via_tun = "oaitun_ue1" in last or "10.140." in last
        if via_tun and rtt is not None and rtt >= MIN_PDU_RTT_MS:
            print(f"  slice {sid}: PDU {app_ip} rtt={rtt:.0f} ms")
            return
        extra = f" rtt={rtt:.1f}ms" if rtt is not None else ""
        print(f"  slice {sid}: waiting PDU{extra} …")
        time.sleep(4)
    raise SystemExit(f"UE {sid} PDU to {app_ip} not ready:\n{last}")


def fetch_status(sid: int) -> Optional[dict[str, Any]]:
    ip = SLICES[sid]["ue_console_ip"]
    try:
        with urllib.request.urlopen(f"http://{ip}:8090/api/status", timeout=STATUS_TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def start_app(sid: int) -> None:
    """Kick the DL workload. YOLO UL publish stays off (annotated pull is enough)."""
    if sid == 2:
        return
    ip = SLICES[sid]["ue_console_ip"]
    last = ""
    for _ in range(10):
        try:
            req = urllib.request.Request(
                f"http://{ip}:8090/api/app/start", method="POST", data=b""
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
            print(f"  slice {sid}: POST /api/app/start")
            return
        except Exception as exc:
            last = str(exc)
            time.sleep(3)
    print(f"  slice {sid}: app/start not ready ({last})")


def traffic_ok(sid: int, d: dict[str, Any], prev: Optional[dict[str, Any]]) -> tuple[bool, str]:
    """True when this slice is producing DL traffic over the PDU."""
    app = d.get("app") or {}
    if sid in (1, 4):
        success = int(d.get("success") or 0)
        prev_s = int((prev or {}).get("success") or 0)
        running = bool(app.get("running"))
        if running and success > prev_s:
            return True, f"sftp running success={success}"
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


def wait_stable(sid: int, settle_s: float, timeout_s: float) -> None:
    """Require ``settle_s`` of consecutive healthy traffic before measuring."""
    deadline = time.time() + timeout_s
    ok_since: Optional[float] = None
    prev: Optional[dict[str, Any]] = None
    while time.time() < deadline:
        d = fetch_status(sid)
        if d is None:
            print(f"  slice {sid}: console not up …")
            ok_since = None
            time.sleep(3)
            continue
        good, why = traffic_ok(sid, d, prev)
        prev = d
        now = time.time()
        if good:
            if ok_since is None:
                ok_since = now
            held = now - ok_since
            print(f"  slice {sid}: stable {held:.0f}/{settle_s:.0f}s ({why})")
            if held >= settle_s:
                print(f"  slice {sid}: stabilized")
                return
        else:
            ok_since = None
            print(f"  slice {sid}: not stable ({why})")
        time.sleep(3)
    raise SystemExit(f"UE {sid} did not stabilize in {timeout_s:.0f}s")


def collect_slice(sid: int, duration: str, settle_s: float, pdu_timeout: float, stable_timeout: float) -> Path:
    name = SLICES[sid]["name"]
    print("=" * 64)
    print(f" SX slice {sid} {name}: deploy UE → wait stable → collect {duration}")
    print("=" * 64)
    undeploy_ues(namespace=NAMESPACE, context=EDGE, sids=sorted(SLICES))
    time.sleep(5)
    deploy_ue.main(["--ue", str(sid)])
    wait_ue_ready(sid, timeout_s=pdu_timeout)
    wait_pdu(sid, timeout_s=pdu_timeout)
    start_app(sid)
    wait_stable(sid, settle_s=settle_s, timeout_s=stable_timeout)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    run_id = f"slice{sid}_{stamp}_{duration.replace(' ', '')}"
    rc = exp_start.main(
        [
            "--scheme",
            "sx",
            "--duration",
            duration,
            "--slices",
            str(sid),
            "--run-id",
            run_id,
        ]
    )
    if rc != 0:
        raise SystemExit(f"exp_start failed for slice {sid} (rc={rc})")
    return HERE / "data" / run_id


def write_requirements(rows: list[dict]) -> Path:
    out_dir = HERE / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    summary = {
        "scheme": "exp4-sx",
        "note": "uncontended per-slice DL goodput (one UE at a time, after stable)",
        "collected_utc": stamp,
        "slices": rows,
    }
    js = out_dir / "traffic_requirements.json"
    js.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    csv_path = out_dir / "traffic_requirements.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "slice",
                "name",
                "run_id",
                "mean_delay_ms",
                "mean_throughput_mbps",
                "samples",
                "t_bar_mbps",
            ],
        )
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"\nTraffic requirements → {js}")
    print(f"{'SLICE':<6} {'NAME':<8} {'d̄ ms':>8} {'T Mbps':>8}  run")
    for row in rows:
        d = row["mean_delay_ms"]
        t = row["mean_throughput_mbps"]
        print(
            f"{row['slice']:<6} {row['name']:<8} "
            f"{(f'{d:.1f}' if d is not None else '—'):>8} "
            f"{(f'{t:.2f}' if t is not None else '—'):>8}  {row['run_id']}"
        )
    return js


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ue", action="append", default=[], metavar="ID", help="slice ids (default all 1–5)")
    p.add_argument("ues", nargs="*", help="slice ids (same as --ue)")
    p.add_argument("--duration", default="300", help="Influx window after stable (default 300s)")
    p.add_argument(
        "--settle",
        type=float,
        default=30.0,
        help="seconds of consecutive healthy traffic before collect (default 30)",
    )
    p.add_argument("--pdu-timeout", type=float, default=180.0, help="PDU attach timeout seconds")
    p.add_argument(
        "--stable-timeout",
        type=float,
        default=300.0,
        help="max wait for traffic to stabilize (default 300s)",
    )
    p.add_argument(
        "--skip-undeploy",
        action="store_true",
        help="leave the last UE running after its collection",
    )
    args = p.parse_args()
    sids = parse_ue_ids(list(args.ues) + list(args.ue))
    rows: list[dict] = []
    for sid in sids:
        run_dir = collect_slice(
            sid,
            args.duration,
            settle_s=float(args.settle),
            pdu_timeout=float(args.pdu_timeout),
            stable_timeout=float(args.stable_timeout),
        )
        summary_path = run_dir / "summary.json"
        per: dict = {}
        if summary_path.is_file():
            per = json.loads(summary_path.read_text()).get("per_slice", {}).get(str(sid), {})
        rows.append(
            {
                "slice": sid,
                "name": SLICES[sid]["name"],
                "run_id": run_dir.name,
                "mean_delay_ms": per.get("mean_delay_ms"),
                "mean_throughput_mbps": per.get("mean_throughput_mbps"),
                "samples": per.get("samples"),
                "t_bar_mbps": SLICES[sid]["t_bar"],
            }
        )
        if not args.skip_undeploy:
            undeploy_ues(namespace=NAMESPACE, context=EDGE, sids=[sid])
            time.sleep(3)
    write_requirements(rows)


if __name__ == "__main__":
    main()
