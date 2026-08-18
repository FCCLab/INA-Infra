#!/usr/bin/env python3
"""List deployed UEs and download last-N-minute DL / UL / RTT series from Influx.

Writes under ``data/<ue_id>/`` (gitignored):

  dl.csv    throughput_dl_mbps
  ul.csv    throughput_ul_mbps
  rtt.csv   app_ue_rtt_ms (fallback: rtt_ms)

Default (no args): list edge ``oai-ue-slice-*`` UEs, then pull the last 10 minutes.

Lab defaults (override with env):
  INFLUX_URL=http://10.1.137.104:8086
  INFLUX_TOKEN=ina-infra-influxdb-token
  INFLUX_ORG=ina-infra
  INFLUX_BUCKET=default
  INFLUX_MEASUREMENT=application_metrics
  INFLUX_TZ=Asia/Taipei
  KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "data"
DEFAULT_NS = "ina-infra"
DEFAULT_CONTEXT = "edge@edge"
DEFAULT_KUBECONFIG = os.path.expanduser(
    "~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge"
)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Ue:
    deploy: str
    ue_id: str  # Influx ue_id / APP_NAME, e.g. slice1-cctv-client-1
    app_type: str
    slice_id: str
    ready: str


def flux_query(
    *,
    url: str,
    token: str,
    org: str,
    flux: str,
    timeout: float = 120.0,
) -> str:
    endpoint = f"{url.rstrip('/')}/api/v2/query?{urllib.parse.urlencode({'org': org})}"
    req = urllib.request.Request(
        endpoint,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Influx query HTTP {exc.code}: {body}") from exc


def parse_rfc3339(t_s: str) -> datetime:
    """Parse Influx RFC3339 (often ns precision) into aware UTC datetime."""
    s = t_s.strip().replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""
        tz = ""
        for i, ch in enumerate(rest):
            if ch.isdigit():
                frac += ch
            else:
                tz = rest[i:]
                break
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    ts = datetime.fromisoformat(s)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def parse_influx_csv(text: str) -> List[Tuple[datetime, float]]:
    """Parse Flux annotated CSV → [(utc datetime, value), ...] sorted by time."""
    rows: List[Tuple[datetime, float]] = []
    headers: Optional[List[str]] = None
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            if raw.startswith("#datatype") or raw.startswith("#group") or raw.startswith("#default"):
                headers = None
            continue
        cols = next(csv.reader([raw]))
        if headers is None:
            headers = cols
            continue
        if len(cols) < len(headers):
            continue
        row = dict(zip(headers, cols))
        if row.get("result") == "_result" and not row.get("_time"):
            continue
        t_s = row.get("_time") or ""
        v_s = row.get("_value") or ""
        if not t_s or v_s == "":
            continue
        rows.append((parse_rfc3339(t_s), float(v_s)))
    rows.sort(key=lambda x: x[0])
    return rows


def _env_map(container: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in container.get("env") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name and "value" in item:
            out[name] = str(item.get("value") or "")
    return out


def list_ues_kubectl(
    *,
    kubeconfig: str,
    context: str,
    namespace: str,
) -> List[Ue]:
    envp = os.environ.copy()
    envp["KUBECONFIG"] = kubeconfig
    proc = subprocess.run(
        [
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "get",
            "deploy",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=envp,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"kubectl get deploy: {err}")
    raw = json.loads(proc.stdout)
    ues: List[Ue] = []
    for item in raw.get("items") or []:
        meta = item.get("metadata") or {}
        name = str(meta.get("name") or "")
        if not name.startswith("oai-ue-slice-"):
            continue
        containers = (
            ((item.get("spec") or {}).get("template") or {}).get("spec") or {}
        ).get("containers") or []
        env_map: Dict[str, str] = {}
        for ctr in containers:
            if not isinstance(ctr, dict):
                continue
            if str(ctr.get("name") or "") in ("throughput-statistics", "rtt-probe"):
                env_map = _env_map(ctr)
                if env_map.get("APP_NAME"):
                    break
        ue_id = env_map.get("APP_NAME") or name
        status = item.get("status") or {}
        ready = status.get("readyReplicas")
        desired = (item.get("spec") or {}).get("replicas")
        ready_s = f"{ready or 0}/{desired if desired is not None else '?'}"
        ues.append(
            Ue(
                deploy=name,
                ue_id=ue_id,
                app_type=env_map.get("APP_TYPE") or "",
                slice_id=env_map.get("SLICE_ID") or "",
                ready=ready_s,
            )
        )
    ues.sort(key=lambda u: (u.slice_id.zfill(4), u.ue_id, u.deploy))
    return ues


def list_ues_influx(
    *,
    url: str,
    token: str,
    org: str,
    bucket: str,
    measurement: str,
    minutes: int,
) -> List[Ue]:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r._field == "throughput_dl_mbps" or r._field == "throughput_ul_mbps" or r._field == "app_ue_rtt_ms" or r._field == "rtt_ms")
  |> filter(fn: (r) => exists r.ue_id)
  |> keep(columns: ["ue_id", "app_name", "app_type", "slice_id"])
  |> distinct(column: "ue_id")
"""
    text = flux_query(url=url, token=token, org=org, flux=flux)
    seen: Dict[str, Ue] = {}
    headers: Optional[List[str]] = None
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            if raw.startswith("#datatype") or raw.startswith("#group") or raw.startswith("#default"):
                headers = None
            continue
        cols = next(csv.reader([raw]))
        if headers is None:
            headers = cols
            continue
        row = dict(zip(headers, cols))
        ue_id = (row.get("ue_id") or row.get("app_name") or "").strip()
        if not ue_id:
            continue
        if ue_id not in seen:
            seen[ue_id] = Ue(
                deploy="",
                ue_id=ue_id,
                app_type=(row.get("app_type") or "").strip(),
                slice_id=(row.get("slice_id") or "").strip(),
                ready="influx",
            )
    ues = list(seen.values())
    ues.sort(key=lambda u: (u.slice_id.zfill(4), u.ue_id))
    return ues


def query_field(
    *,
    url: str,
    token: str,
    org: str,
    bucket: str,
    measurement: str,
    ue_id: str,
    field: str,
    minutes: int,
) -> List[Tuple[datetime, float]]:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r.ue_id == "{ue_id}")
  |> filter(fn: (r) => r._field == "{field}")
  |> keep(columns: ["_time", "_value"])
  |> sort(columns: ["_time"])
"""
    return parse_influx_csv(flux_query(url=url, token=token, org=org, flux=flux))


def write_csv(path: Path, points: List[Tuple[datetime, float]], tz: ZoneInfo, value_col: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "time_local", "t_unix_s", value_col])
        for ts, val in points:
            local = ts.astimezone(tz)
            w.writerow(
                [
                    ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    local.strftime("%Y-%m-%d %H:%M:%S"),
                    f"{ts.timestamp():.3f}",
                    f"{val:.6f}",
                ]
            )


def print_ues(ues: List[Ue]) -> None:
    print(f"Deployed UEs ({len(ues)}):")
    print(f"{'DEPLOY':<32} {'UE_ID':<36} {'APP':<14} {'SLICE':<6} READY")
    print(f"{'-'*32} {'-'*36} {'-'*14} {'-'*6} -----")
    for u in ues:
        print(
            f"{u.deploy or '—':<32} {u.ue_id:<36} {u.app_type or '—':<14} "
            f"{u.slice_id or '—':<6} {u.ready}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--minutes",
        type=int,
        default=int(env("DOWNLOAD_MINUTES", "10") or "10"),
        help="lookback window in minutes (default: 10)",
    )
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: ./data)")
    p.add_argument("--url", default=env("INFLUX_URL", "http://10.1.137.104:8086"))
    p.add_argument("--token", default=env("INFLUX_TOKEN", "ina-infra-influxdb-token"))
    p.add_argument("--org", default=env("INFLUX_ORG", "ina-infra"))
    p.add_argument("--bucket", default=env("INFLUX_BUCKET", "default"))
    p.add_argument("--measurement", default=env("INFLUX_MEASUREMENT", "application_metrics"))
    p.add_argument("--tz", default=env("INFLUX_TZ", "Asia/Taipei"))
    p.add_argument("--namespace", default=env("UE_NAMESPACE", DEFAULT_NS))
    p.add_argument("--context", default=env("KUBE_CONTEXT", DEFAULT_CONTEXT))
    p.add_argument("--kubeconfig", default=env("KUBECONFIG", DEFAULT_KUBECONFIG))
    p.add_argument(
        "--from-influx",
        action="store_true",
        help="list UEs from Influx tags instead of kubectl",
    )
    p.add_argument("--list-only", action="store_true", help="print UE list and exit")
    args = p.parse_args(argv)

    tz = ZoneInfo(args.tz)
    try:
        if args.from_influx:
            ues = list_ues_influx(
                url=args.url,
                token=args.token,
                org=args.org,
                bucket=args.bucket,
                measurement=args.measurement,
                minutes=args.minutes,
            )
        else:
            try:
                ues = list_ues_kubectl(
                    kubeconfig=args.kubeconfig,
                    context=args.context,
                    namespace=args.namespace,
                )
            except Exception as exc:
                print(f"warning: kubectl list failed ({exc}); falling back to Influx", file=sys.stderr)
                ues = list_ues_influx(
                    url=args.url,
                    token=args.token,
                    org=args.org,
                    bucket=args.bucket,
                    measurement=args.measurement,
                    minutes=args.minutes,
                )
    except Exception as exc:
        print(f"error: list UEs: {exc}", file=sys.stderr)
        return 1

    if not ues:
        print("No UEs found.", file=sys.stderr)
        return 1

    print_ues(ues)
    if args.list_only:
        return 0

    print(
        f"\nInflux {args.url} org={args.org} bucket={args.bucket} "
        f"measurement={args.measurement} last {args.minutes}m → {args.out}"
    )
    args.out.mkdir(parents=True, exist_ok=True)

    for ue in ues:
        ue_dir = args.out / ue.ue_id
        jobs = (
            ("dl.csv", "throughput_dl_mbps", "dl_mbps", ()),
            ("ul.csv", "throughput_ul_mbps", "ul_mbps", ()),
            ("rtt.csv", "app_ue_rtt_ms", "rtt_ms", ("rtt_ms",)),
        )
        print(f"  {ue.ue_id}")
        for fname, field, col, fallbacks in jobs:
            points = query_field(
                url=args.url,
                token=args.token,
                org=args.org,
                bucket=args.bucket,
                measurement=args.measurement,
                ue_id=ue.ue_id,
                field=field,
                minutes=args.minutes,
            )
            used = field
            for alt in fallbacks:
                if points:
                    break
                points = query_field(
                    url=args.url,
                    token=args.token,
                    org=args.org,
                    bucket=args.bucket,
                    measurement=args.measurement,
                    ue_id=ue.ue_id,
                    field=alt,
                    minutes=args.minutes,
                )
                used = alt
            path = ue_dir / fname
            write_csv(path, points, tz, col)
            mean = (sum(v for _, v in points) / len(points)) if points else float("nan")
            print(f"    {fname}: n={len(points)} mean={mean:.3f} ({used})")

    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
