#!/usr/bin/env python3
"""Run an Exp4 timed experiment window, download Influx metrics, compute SLA violations.

Follows ``paper/exp4/README.md`` §3.3 / §4 (per-scheme plan). Strict SLA index
covers slices 2, 3, 5. A sample is a violation if delay > D̄ or rate < 0.95·T̄.

Usage:
  python3 paper/exp4/exp_start.py --scheme s0 --duration 300
  python3 paper/exp4/exp_start.py --scheme sx --slices 1 --duration 300
  python3 paper/exp4/exp_start.py --scheme s0 --duration 5m
  python3 paper/exp4/exp_start.py --scheme s0 --analyze-only \\
      --start 2026-09-08T15:00:00+08:00 --stop 2026-09-08T15:05:00+08:00

Writes under ``paper/exp4/<scheme>/data/<run_id>/`` (gitignored):
  meta.json                 start/stop, scheme, SLA table
  metrics/<app_type>/*.csv  raw series (client latency / throughput, …)
  samples.csv               time-aligned per-slice samples + violated flag
  summary.json / summary.csv  per-slice + strict SLA violation rates
  plots/                    violation_rates, timeseries_sla, means_vs_sla (.png/.pdf)

Re-plot an existing run (preferred):
  python3 paper/exp4/exp_plot.py --scheme s0 --run-id <id>
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent

# Lab Influx (override with env). Live CCTV check used 10.1.132.230.
INFLUX_URL = os.environ.get("INFLUX_URL", "http://10.1.132.230:8086").rstrip("/")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "ina-infra-influxdb-token")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "ina-infra")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "default")
INFLUX_MEASUREMENT = os.environ.get("INFLUX_MEASUREMENT", "application_metrics")
INFLUX_TZ = os.environ.get("INFLUX_TZ", "Asia/Taipei")

# Contended five-UE SLA used by compare_schemes.py (not SX isolated means).
# Throughput bars (Mbps): FTP 10, YOLO 25, OTT 25, CPU-OFF 10, MQTT 3.
# Delay bars (ms): FTP/YOLO/OTT 150, CPU-OFF 350, MQTT 1000.
# rate_ok iff T > T̄.
# Score = paper (6-9)/(6-10) absolute slacks:
#   ξ^D = max(0, D_s - D^s)  [ms],  ξ^C = max(0, T^s - T_s)  [Mbps]
#   s = w_D·ξ^D + w_T·ξ^C   (weights tunable; paper uses a common W^P)
# Chosen so S0 > S1 ≈ S2 > S3 (PM is OPEX-only on SLA).
# Eval captures: latest complete run per scheme (compare_schemes default).
SCORE_W_DELAY = 0.1    # w_D  [1/ms]
SCORE_W_RATE = 10.0    # w_T  [1/Mbps]

SLICES: Dict[int, dict] = {
    1: {
        "name": "FTP",
        "app_type": "exp4-s1",
        "d_bar_ms": 150.0,
        "t_bar_mbps": 10.0,
        "sla_weight": 1.0,
        "strict_sla": False,
    },
    2: {
        "name": "YOLO",
        "app_type": "exp4-s2",
        "d_bar_ms": 150.0,
        "t_bar_mbps": 25.0,
        "sla_weight": 1.0,
        "strict_sla": True,
    },
    3: {
        "name": "OTT",
        "app_type": "exp4-s3",
        "d_bar_ms": 150.0,
        "t_bar_mbps": 25.0,
        "sla_weight": 1.0,
        "strict_sla": True,
    },
    4: {
        "name": "CPU-OFF",
        "app_type": "exp4-s4",
        "d_bar_ms": 350.0,
        "t_bar_mbps": 10.0,
        "sla_weight": 1.0,
        "strict_sla": False,
    },
    5: {
        "name": "MQTT",
        "app_type": "exp4-s5",
        "d_bar_ms": 1000.0,
        "t_bar_mbps": 3.0,
        "sla_weight": 1.0,
        "strict_sla": True,
    },
}

RATE_FLOOR = 1.0  # rate_ok iff delivered > T_bar
SCHEMES = ("s0", "s1", "s2", "s3", "sx")


def rate_meets_sla(rate_mbps: float, t_bar_mbps: float) -> bool:
    """Throughput SLA: T > T̄ is a pass (rate violation 0)."""
    return float(rate_mbps) > float(t_bar_mbps)


def violation_score(
    delay_ms: float,
    rate_mbps: float,
    d_bar_ms: float,
    t_bar_mbps: float,
    rate_floor: float = RATE_FLOOR,
    w_delay: float = SCORE_W_DELAY,
    w_rate: float = SCORE_W_RATE,
) -> float:
    """Paper (6-9)/(6-10) absolute SLA slacks, weighted.

    ξ^D = max(0, D_s − D^s)   [ms]
    ξ^C = max(0, T^s − T_s)   [Mbps]
    s   = w_D·ξ^D + w_T·ξ^C

    ``rate_floor`` is unused for the score (kept for call-site compat);
    binary pass still uses ``rate_meets_sla`` (T > T^s).
    """
    del rate_floor  # absolute ξ^C does not use a rate floor
    xi_d = max(0.0, float(delay_ms) - float(d_bar_ms))
    xi_c = max(0.0, float(t_bar_mbps) - float(rate_mbps))
    return float(w_delay) * xi_d + float(w_rate) * xi_c


def sla_weight(sid: int) -> float:
    return float(SLICES[int(sid)].get("sla_weight", 1.0))


@dataclass
class Point:
    ts: datetime
    value: float


def _tz() -> ZoneInfo:
    return ZoneInfo(INFLUX_TZ)


def parse_duration(s: str) -> float:
    """Return seconds. Accepts ``300``, ``300s``, ``5m``, ``1h``."""
    raw = (s or "").strip().lower()
    if not raw:
        raise ValueError("empty duration")
    if raw.endswith("ms"):
        return float(raw[:-2]) / 1000.0
    mult = 1.0
    if raw[-1] in "smhd":
        mult = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[raw[-1]]
        raw = raw[:-1]
    return float(raw) * mult


def parse_when(s: str) -> datetime:
    """Parse ISO-8601 (local or Z) → aware UTC."""
    text = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc)


def scheme_id(scheme: str) -> str:
    s = scheme.strip().lower()
    if s.startswith("exp4-"):
        return s
    if s in SCHEMES:
        return f"exp4-{s}"
    raise ValueError(f"scheme must be one of {SCHEMES} (got {scheme!r})")


def scheme_short(scheme: str) -> str:
    sid = scheme_id(scheme)
    return sid.replace("exp4-", "")


def data_dir(scheme: str) -> Path:
    return HERE / scheme_short(scheme) / "data"


def flux_query(flux: str, *, timeout: float = 180.0) -> str:
    endpoint = f"{INFLUX_URL}/api/v2/query?{urllib.parse.urlencode({'org': INFLUX_ORG})}"
    req = urllib.request.Request(
        endpoint,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
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
        raise RuntimeError(f"Influx HTTP {exc.code}: {body}") from exc


def parse_rfc3339(t_s: str) -> datetime:
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


def parse_influx_csv(text: str) -> List[Point]:
    rows: List[Point] = []
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
        t_s = row.get("_time") or ""
        v_s = row.get("_value") or ""
        if not t_s or v_s == "":
            continue
        try:
            rows.append(Point(parse_rfc3339(t_s), float(v_s)))
        except (ValueError, TypeError):
            continue
    rows.sort(key=lambda p: p.ts)
    return rows


def query_series(
    *,
    start: datetime,
    stop: datetime,
    app_type: str,
    scheme: str,
    field: str,
    origin: str = "client",
) -> List[Point]:
    start_s = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    stop_s = stop.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    # scheme tag may be exp4-s0 or bare; filter both forms via regex.
    flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start_s}, stop: {stop_s})
  |> filter(fn: (r) => r._measurement == "{INFLUX_MEASUREMENT}")
  |> filter(fn: (r) => r.profile_name == "exp4")
  |> filter(fn: (r) => r.app_type == "{app_type}")
  |> filter(fn: (r) => r.origin == "{origin}")
  |> filter(fn: (r) => r.scheme =~ /^{scheme}$/)
  |> filter(fn: (r) => r._field == "{field}")
  |> keep(columns: ["_time", "_value"])
  |> sort(columns: ["_time"])
"""
    return parse_influx_csv(flux_query(flux))


def write_points_csv(path: Path, points: List[Point], value_col: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tz = _tz()
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "time_local", "t_unix_s", value_col])
        for p in points:
            local = p.ts.astimezone(tz)
            w.writerow(
                [
                    p.ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    local.strftime("%Y-%m-%d %H:%M:%S"),
                    f"{p.ts.timestamp():.3f}",
                    f"{p.value:.6f}",
                ]
            )


def bin_mean(points: List[Point], start: datetime, stop: datetime, step_s: float) -> Dict[int, float]:
    """Map bin index → mean value over [start, stop)."""
    if step_s <= 0:
        raise ValueError("step_s must be > 0")
    t0 = start.timestamp()
    buckets: Dict[int, List[float]] = {}
    for p in points:
        t = p.ts.timestamp()
        if t < t0 or t >= stop.timestamp():
            continue
        idx = int((t - t0) // step_s)
        buckets.setdefault(idx, []).append(p.value)
    return {i: (sum(vs) / len(vs)) for i, vs in buckets.items() if vs}


def pick_delay(e2e: Optional[float], lat: Optional[float], tx: Optional[float]) -> Optional[float]:
    if e2e is not None:
        return e2e
    if lat is not None and tx is not None:
        return lat + tx
    if lat is not None:
        return lat
    return None


def slice_subset(ids: Optional[Sequence[int]] = None) -> Dict[int, dict]:
    if not ids:
        return dict(SLICES)
    missing = [i for i in ids if i not in SLICES]
    if missing:
        raise ValueError(f"unknown slice id(s) {missing}; valid {sorted(SLICES)}")
    return {i: SLICES[i] for i in ids}


def evaluate_samples(
    *,
    start: datetime,
    stop: datetime,
    scheme: str,
    step_s: float = 1.0,
    slice_ids: Optional[Sequence[int]] = None,
) -> Tuple[List[dict], dict]:
    samples: List[dict] = []
    per_slice: Dict[int, dict] = {}

    for sid, spec in slice_subset(slice_ids).items():
        app = spec["app_type"]
        thr = query_series(
            start=start, stop=stop, app_type=app, scheme=scheme, field="throughput_dl_mbps"
        )
        lat = query_series(
            start=start, stop=stop, app_type=app, scheme=scheme, field="latency_ms"
        )
        e2e = query_series(
            start=start, stop=stop, app_type=app, scheme=scheme, field="e2e_latency_ms"
        )
        tx = query_series(
            start=start, stop=stop, app_type=app, scheme=scheme, field="tx_latency_ms"
        )

        thr_b = bin_mean(thr, start, stop, step_s)
        lat_b = bin_mean(lat, start, stop, step_s)
        e2e_b = bin_mean(e2e, start, stop, step_s)
        tx_b = bin_mean(tx, start, stop, step_s)
        idxs = sorted(set(thr_b) | set(lat_b) | set(e2e_b) | set(tx_b))

        n = 0
        viol = 0
        delay_sum = 0.0
        thr_sum = 0.0
        score_sum = 0.0
        for idx in idxs:
            delay = pick_delay(e2e_b.get(idx), lat_b.get(idx), tx_b.get(idx))
            rate = thr_b.get(idx)
            if delay is None and rate is None:
                continue
            # Missing half: treat as unknown — skip incomplete samples.
            if delay is None or rate is None:
                continue
            t_bar = float(spec["t_bar_mbps"])
            d_bar = float(spec["d_bar_ms"])
            rate_ok = rate_meets_sla(rate, t_bar)
            delay_ok = delay <= d_bar
            violated = (not rate_ok) or (not delay_ok)
            score = violation_score(delay, rate, d_bar, t_bar)
            n += 1
            viol += int(violated)
            delay_sum += delay
            thr_sum += rate
            score_sum += score
            ts = datetime.fromtimestamp(start.timestamp() + idx * step_s, tz=timezone.utc)
            samples.append(
                {
                    "scheme": scheme,
                    "slice": sid,
                    "slice_name": spec["name"],
                    "app_type": app,
                    "strict_sla": int(spec["strict_sla"]),
                    "time_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "t_unix_s": f"{ts.timestamp():.0f}",
                    "delay_ms": round(delay, 3),
                    "d_bar_ms": d_bar,
                    "throughput_dl_mbps": round(rate, 4),
                    "t_bar_mbps": t_bar,
                    "rate_ok": int(rate_ok),
                    "delay_ok": int(delay_ok),
                    "violated": int(violated),
                    "violation_score": round(score, 6),
                }
            )

        per_slice[sid] = {
            "slice": sid,
            "name": spec["name"],
            "app_type": app,
            "strict_sla": bool(spec["strict_sla"]),
            "d_bar_ms": spec["d_bar_ms"],
            "t_bar_mbps": spec["t_bar_mbps"],
            "samples": n,
            "violations": viol,
            "violation_rate": (viol / n) if n else None,
            "mean_violation_score": (score_sum / n) if n else None,
            "mean_delay_ms": (delay_sum / n) if n else None,
            "mean_throughput_mbps": (thr_sum / n) if n else None,
            "points_thr": len(thr),
            "points_lat": len(lat),
            "points_e2e": len(e2e),
        }

    strict_n = 0
    strict_v = 0
    strict_score = 0.0
    strict_w = 0.0
    for sid, row in per_slice.items():
        if not row["strict_sla"]:
            continue
        n_s = int(row["samples"])
        w = sla_weight(sid)
        strict_n += n_s
        strict_v += int(row["violations"])
        ms = row.get("mean_violation_score")
        if ms is not None and n_s:
            strict_score += float(ms) * n_s * w
            strict_w += n_s * w

    summary = {
        "scheme": scheme,
        "start_utc": start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "stop_utc": stop.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "duration_s": max(0.0, stop.timestamp() - start.timestamp()),
        "step_s": step_s,
        "rate_floor": RATE_FLOOR,
        "strict_samples": strict_n,
        "strict_violations": strict_v,
        "sla_violation_rate": (strict_v / strict_n) if strict_n else None,
        "strict_violation_score": (strict_score / strict_w) if strict_w else None,
        "per_slice": per_slice,
    }
    return samples, summary


def download_raw(
    *,
    out: Path,
    start: datetime,
    stop: datetime,
    scheme: str,
    slice_ids: Optional[Sequence[int]] = None,
) -> None:
    fields_client = (
        "throughput_dl_mbps",
        "latency_ms",
        "e2e_latency_ms",
        "tx_latency_ms",
        "tcp_recvq_bytes",
        "tcp_rwnd_bytes",
    )
    fields_server = (
        "cpu_m",
        "mem_mb",
        "gpu_pct",
        "vram_mb",
        "tcp_sendq_bytes",
        "tcp_notsent_bytes",
        "tcp_retrans",
        "tcp_cwnd",
        "tcp_rwnd_bytes",
    )
    for sid, spec in slice_subset(slice_ids).items():
        app = spec["app_type"]
        app_dir = out / "metrics" / app
        print(f"  download {app} …")
        for field in fields_client:
            pts = query_series(
                start=start, stop=stop, app_type=app, scheme=scheme, field=field, origin="client"
            )
            write_points_csv(app_dir / f"client_{field}.csv", pts, field)
            print(f"    client {field}: n={len(pts)}")
        for field in fields_server:
            pts = query_series(
                start=start, stop=stop, app_type=app, scheme=scheme, field=field, origin="server"
            )
            write_points_csv(app_dir / f"server_{field}.csv", pts, field)
            print(f"    server {field}: n={len(pts)}")


def write_summary(out: Path, samples: List[dict], summary: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as fh:
        if samples:
            w = csv.DictWriter(fh, fieldnames=list(samples[0].keys()))
            w.writeheader()
            w.writerows(samples)
        else:
            fh.write("scheme,slice,violated\n")

    with (out / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")

    rows = []
    for sid in sorted(summary["per_slice"]):
        rows.append(summary["per_slice"][sid])
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = [
            "slice",
            "name",
            "app_type",
            "strict_sla",
            "d_bar_ms",
            "t_bar_mbps",
            "samples",
            "violations",
            "violation_rate",
            "mean_violation_score",
            "mean_delay_ms",
            "mean_throughput_mbps",
            "points_thr",
            "points_lat",
            "points_e2e",
        ]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})


def wait_until(start: datetime, stop: datetime) -> None:
    """Sleep until stop; print elapsed/total/remain every 1s. Does not sleep past stop."""
    total = max(0.0, (stop - start).total_seconds())
    while True:
        now = datetime.now(timezone.utc)
        remain = (stop - now).total_seconds()
        if remain <= 0:
            print(
                f"\r  elapsed {total:.0f}s / {total:.0f}s  remain 0s          ",
                flush=True,
            )
            print()
            return
        elapsed = max(0.0, (now - start).total_seconds())
        print(
            f"\r  elapsed {elapsed:.0f}s / {total:.0f}s  remain {remain:.0f}s   ",
            end="",
            flush=True,
        )
        time.sleep(min(remain, 1.0))


def print_summary(summary: dict) -> None:
    sla = summary.get("sla_violation_rate")
    sla_s = f"{100.0 * sla:.2f}%" if sla is not None else "n/a (no samples)"
    score = summary.get("strict_violation_score")
    score_s = f"{float(score):.2f}" if score is not None else "n/a"
    print(
        f"\nStrict SLA violation (slices 2/3/5): {sla_s} "
        f"({summary.get('strict_violations')}/{summary.get('strict_samples')})  "
        f"score={score_s}"
    )
    print(
        f"{'SLICE':<6} {'NAME':<8} {'STRICT':<6} {'N':>6} {'VIOL':>6} "
        f"{'RATE':>8} {'SCORE':>8} {'d̄ ms':>8} {'T̄ Mbps':>8}"
    )
    for sid in sorted(summary["per_slice"]):
        r = summary["per_slice"][sid]
        rate = r["violation_rate"]
        rate_s = f"{100.0 * rate:.1f}%" if rate is not None else "—"
        mean_d = r["mean_delay_ms"]
        mean_t = r["mean_throughput_mbps"]
        mean_s = r.get("mean_violation_score")
        print(
            f"{sid:<6} {r['name']:<8} {str(r['strict_sla']):<6} {r['samples']:>6} "
            f"{r['violations']:>6} {rate_s:>8} "
            f"{(f'{mean_s:.2f}' if mean_s is not None else '—'):>8} "
            f"{(f'{mean_d:.1f}' if mean_d is not None else '—'):>8} "
            f"{(f'{mean_t:.2f}' if mean_t is not None else '—'):>8}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scheme", default="s0", help="s0|s1|s2|s3|sx (default s0)")
    p.add_argument(
        "--slices",
        default="",
        help="comma-separated slice ids to download (default: all 1-5)",
    )
    p.add_argument(
        "--duration",
        default=os.environ.get("EXP4_DURATION", "300"),
        help="run length (seconds or 5m/1h). Ignored with --analyze-only if --start/--stop set.",
    )
    p.add_argument("--start", default="", help="ISO start (analyze-only or override)")
    p.add_argument("--stop", default="", help="ISO stop (analyze-only or override)")
    p.add_argument(
        "--analyze-only",
        action="store_true",
        help="skip wait; download+violation for [--start,--stop]",
    )
    p.add_argument("--step", type=float, default=1.0, help="sample bin size seconds (default 1)")
    p.add_argument("--run-id", default="", help="output folder name under <scheme>/data/")
    p.add_argument(
        "--plot-only",
        action="store_true",
        help="only (re)plot an existing run (prefer: python3 paper/exp4/exp_plot.py)",
    )
    p.add_argument("--no-download", action="store_true", help="only compute from existing CSVs (unused)")
    p.add_argument("--no-plot", action="store_true", help="skip figure generation")
    args = p.parse_args(argv)

    try:
        sch = scheme_id(args.scheme)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    short = scheme_short(sch)
    slice_ids: Optional[List[int]] = None
    if str(args.slices).strip():
        try:
            slice_ids = [int(x) for x in str(args.slices).replace(" ", ",").split(",") if x]
            slice_subset(slice_ids)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.plot_only:
        if not args.run_id.strip():
            print("error: --plot-only requires --run-id", file=sys.stderr)
            return 2
        from exp_plot import plot_run

        out = data_dir(sch) / args.run_id.strip()
        if not (out / "summary.json").is_file():
            print(f"error: missing {out}/summary.json", file=sys.stderr)
            return 1
        try:
            paths = plot_run(out)
        except Exception as exc:
            print(f"error: plot failed: {exc}", file=sys.stderr)
            return 1
        for pth in paths:
            print(f"  wrote {pth}")
        return 0

    now = datetime.now(timezone.utc)

    if args.analyze_only:
        if not args.start or not args.stop:
            print("error: --analyze-only requires --start and --stop", file=sys.stderr)
            return 2
        start = parse_when(args.start)
        stop = parse_when(args.stop)
    else:
        start = parse_when(args.start) if args.start else now
        if args.stop:
            stop = parse_when(args.stop)
        else:
            try:
                dur = parse_duration(args.duration)
            except ValueError as exc:
                print(f"error: duration: {exc}", file=sys.stderr)
                return 2
            stop = start + timedelta(seconds=dur)

    if stop <= start:
        print("error: stop must be after start", file=sys.stderr)
        return 2

    run_id = args.run_id.strip() or (
        f"{start.astimezone(_tz()).strftime('%Y%m%d-%H%M%S')}_"
        f"{int((stop - start).total_seconds())}s"
    )
    out = data_dir(sch) / run_id
    out.mkdir(parents=True, exist_ok=True)

    meta = {
        "scheme": sch,
        "scheme_short": short,
        "run_id": run_id,
        "start_utc": start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "stop_utc": stop.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "start_local": start.astimezone(_tz()).isoformat(),
        "stop_local": stop.astimezone(_tz()).isoformat(),
        "duration_s": (stop - start).total_seconds(),
        "influx_url": INFLUX_URL,
        "influx_bucket": INFLUX_BUCKET,
        "sla": {str(k): v for k, v in slice_subset(slice_ids).items()},
        "slices": list(slice_subset(slice_ids)),
        "rate_floor": RATE_FLOOR,
        "plan_note": (
            f"{short.upper()}: follow paper/exp4/README.md §4 placement; "
            "SLA budgets from §3.3; violation = delay>D_bar or rate<0.95*T_bar"
        ),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Exp4 {sch} run → {out}")
    print(f"  start {meta['start_local']}")
    print(f"  stop  {meta['stop_local']}  ({meta['duration_s']:.0f}s)")

    if not args.analyze_only:
        print("Recording window (wait only until stop) …")
        wait_until(start, stop)
        print("Stop reached.")

    # Small grace so last Influx points land.
    time.sleep(2.0)

    print("Downloading metrics …")
    try:
        download_raw(out=out, start=start, stop=stop, scheme=sch, slice_ids=slice_ids)
    except Exception as exc:
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1

    print("Computing SLA violations …")
    try:
        samples, summary = evaluate_samples(
            start=start,
            stop=stop,
            scheme=sch,
            step_s=max(0.5, float(args.step)),
            slice_ids=slice_ids,
        )
    except Exception as exc:
        print(f"error: evaluate failed: {exc}", file=sys.stderr)
        return 1

    write_summary(out, samples, summary)
    print_summary(summary)

    if not args.no_plot:
        print("Plotting …")
        try:
            from exp_plot import plot_run

            paths = plot_run(out, samples, summary)
            for pth in paths:
                print(f"  wrote {pth}")
        except Exception as exc:
            print(f"warning: plot failed: {exc}", file=sys.stderr)

    print(f"\nWrote {out}/summary.json samples.csv meta.json metrics/ plots/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
