#!/usr/bin/env python3
"""Download oai-benchmark iperf3 series from InfluxDB into numpy files.

PRB max% sweep (near-RT RIC). Naming (direction_proto postfix):
  timestamps_dl_udp.csv
  data_dl_udp/

Default (no args): process **all** ``timestamps_*.csv`` → matching ``data_<tag>/``.
Single run: ``--tag dl_udp`` or ``--csv timestamps_dl_udp.csv``.

Existing ``step_*.npz`` / ``summary.npz`` are left untouched (skipped).
Use ``--force`` to re-query Influx and overwrite.

Writes under ``data_<tag>/`` (gitignored):

  data_dl_udp/step_01_5pct.npz  # t_unix_s, client_mbps, server_mbps, …
  data_dl_udp/summary.npz       # per-step aggregates

Lab defaults (override with env):
  INFLUX_URL=http://10.1.137.104:8086
  INFLUX_TOKEN=ina-infra-influxdb-token
  INFLUX_ORG=ina-infra
  INFLUX_BUCKET=default
  INFLUX_MEASUREMENT=iperf3
  INFLUX_TZ=Asia/Taipei   # timestamps CSV wall clock (no TZ in file)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_TAG = "all"
DEFAULT_CSV = HERE / "timestamps_dl_udp.csv"
DEFAULT_OUT = HERE / "data_dl_udp"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def tag_from_timestamps_csv(path: Path) -> str:
    """``timestamps_dl_udp.csv`` → ``dl_udp``; bare stem otherwise."""
    stem = path.stem
    if stem.startswith("timestamps_"):
        return stem[len("timestamps_") :]
    return stem


def list_timestamp_csvs(here: Path = HERE) -> List[Path]:
    return sorted(here.glob("timestamps_*.csv"))


def resolve_download_jobs(
    *,
    tag: str,
    csv: Optional[Path],
    out: Optional[Path],
) -> List[Tuple[Path, Path]]:
    """Return ``(timestamps.csv, data_dir)`` jobs. Default tag ``all`` = every CSV."""
    if csv is not None:
        return [(csv, out or (HERE / f"data_{tag_from_timestamps_csv(csv)}"))]
    if tag != "all":
        path = HERE / f"timestamps_{tag}.csv"
        return [(path, out or (HERE / f"data_{tag}"))]
    if out is not None:
        raise ValueError("--out requires a single --tag or --csv (not --tag all)")
    jobs = [
        (path, HERE / f"data_{tag_from_timestamps_csv(path)}")
        for path in list_timestamp_csvs()
    ]
    if not jobs:
        raise FileNotFoundError(f"no timestamps_*.csv under {HERE}")
    return jobs

@dataclass(frozen=True)
class StepWindow:
    step: int
    prb: str  # max PRB ratio label, e.g. "5%" or "d/m/M=0/0/5"
    phase: str
    start: datetime  # aware UTC
    stop: datetime  # aware UTC

    @property
    def stem(self) -> str:
        pct = parse_prb_pct(self.prb)
        return f"step_{self.step:02d}_{pct:g}pct"


def parse_prb_pct(label: str) -> float:
    """Parse max PRB% from ``5%`` / ``5`` / ``d/m/M=0/0/5``."""
    import re as _re
    s = label.strip()
    m = _re.search(r"d/m/M\s*=\s*[0-9.]+/[0-9.]+/([0-9.]+)", s, _re.I)
    if m:
        return float(m.group(1))
    if s.endswith("%"):
        return float(s[:-1])
    return float(s)


def parse_local_ts(text: str, tz: ZoneInfo) -> datetime:
    """Parse ``YYYY-MM-DD HH:MM:SS`` or UI ``M/D/YYYY, H:MM:SS AM/PM`` as local."""
    s = text.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y, %I:%M:%S %p",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognized timestamp: {text!r}")


def load_steps(csv_path: Path, tz: ZoneInfo) -> List[StepWindow]:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{csv_path}: empty CSV")
        # Tolerate tab-separated legacy dumps without a header.
        fields = [f.strip().lstrip("\ufeff") for f in reader.fieldnames]
        rows = list(reader)

    out: List[StepWindow] = []
    # Headered form: #,CPU,Phase,Start,Stop
    if any(f.lower() in ("#", "cpu", "start") for f in fields):
        key = {f.lower(): f for f in fields}

        def col(*names: str) -> str:
            for n in names:
                if n in key:
                    return key[n]
            raise KeyError(names)

        for r in rows:
            out.append(
                StepWindow(
                    step=int(r[col("#", "step")].strip()),
                    prb=r[col("prb", "cpu", "level")].strip(),
                    phase=r.get(col("phase"), "done").strip() if "phase" in key else "done",
                    start=parse_local_ts(r[col("start")], tz),
                    stop=parse_local_ts(r[col("stop")], tz),
                )
            )
        return out

    # Fallback: tab/comma rows without header — # CPU Phase Start Stop
    fh_text = csv_path.read_text(encoding="utf-8")
    for line in fh_text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("#,") or line.lower().startswith("#\t"):
            # skip pure header-ish; bare "#" alone is not a row
            parts_check = re.split(r"[\t,]+", line)
            if len(parts_check) >= 2 and parts_check[1].lower() == "cpu":
                continue
        parts = re.split(r"[\t,]+", line)
        if len(parts) < 5:
            continue
        out.append(
            StepWindow(
                step=int(parts[0]),
                prb=parts[1],
                phase=parts[2],
                start=parse_local_ts(parts[3], tz),
                stop=parse_local_ts(parts[4], tz),
            )
        )
    if not out:
        raise ValueError(f"{csv_path}: no step rows parsed")
    return out


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
    # datetime.fromisoformat accepts at most microseconds (6 digits).
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


def parse_influx_csv(text: str) -> Tuple[np.ndarray, np.ndarray]:
    """Parse Flux annotated CSV → (t_unix_s float64, mbits_per_second float64)."""
    times: List[float] = []
    values: List[float] = []
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
        # Skip Flux table separators / empty result tables.
        if row.get("result") == "_result" and not row.get("_time"):
            continue
        t_s = row.get("_time") or ""
        v_s = row.get("_value") or row.get("mbits_per_second") or ""
        if not t_s or v_s == "":
            continue
        ts = parse_rfc3339(t_s)
        times.append(ts.timestamp())
        values.append(float(v_s))
    if not times:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    order = np.argsort(times)
    t = np.asarray(times, dtype=np.float64)[order]
    y = np.asarray(values, dtype=np.float64)[order]
    return t, y


def query_role_mbps(
    *,
    url: str,
    token: str,
    org: str,
    bucket: str,
    measurement: str,
    role: str,
    start: datetime,
    stop: datetime,
) -> Tuple[np.ndarray, np.ndarray]:
    # Inclusive window: Flux stop is exclusive — nudge +1s.
    start_rfc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_excl = stop.astimezone(timezone.utc).timestamp() + 1.0
    stop_rfc = datetime.fromtimestamp(stop_excl, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: {start_rfc}, stop: {stop_rfc})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r.role == "{role}")
  |> filter(fn: (r) => r._field == "mbits_per_second")
  |> keep(columns: ["_time", "_value"])
  |> sort(columns: ["_time"])
"""
    raw = flux_query(url=url, token=token, org=org, flux=flux)
    return parse_influx_csv(raw)


def align_series(
    t_a: np.ndarray,
    y_a: np.ndarray,
    t_b: np.ndarray,
    y_b: np.ndarray,
    *,
    max_skew_s: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nearest-neighbor join of A onto B times (or union) → t, ya, yb.

    Uses client times as the primary axis when present; else server.
    """
    if t_a.size == 0 and t_b.size == 0:
        empty = np.array([], dtype=np.float64)
        return empty, empty, empty
    if t_a.size == 0:
        return t_b, np.full_like(t_b, np.nan), y_b
    if t_b.size == 0:
        return t_a, y_a, np.full_like(t_a, np.nan)

    t = t_a
    client = y_a
    # For each client sample, take nearest server sample within max_skew_s.
    server = np.full(t.shape, np.nan, dtype=np.float64)
    j = 0
    for i, ti in enumerate(t):
        while j + 1 < t_b.size and abs(t_b[j + 1] - ti) <= abs(t_b[j] - ti):
            j += 1
        if abs(t_b[j] - ti) <= max_skew_s:
            server[i] = y_b[j]
    return t, client, server


def save_step(out_dir: Path, step: StepWindow, t: np.ndarray, client: np.ndarray, server: np.ndarray) -> Path:
    path = out_dir / f"{step.stem}.npz"
    np.savez_compressed(
        path,
        t_unix_s=t,
        client_mbps=client,
        server_mbps=server,
        step=np.int32(step.step),
        prb_pct=np.float64(parse_prb_pct(step.prb)),
        prb=np.asarray(step.prb),
        start_unix_s=np.float64(step.start.timestamp()),
        stop_unix_s=np.float64(step.stop.timestamp()),
    )
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help="run postfix, or 'all' (default) for every timestamps_*.csv",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="single timestamps CSV (overrides --tag)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory for a single run (default: data_<tag>/)",
    )
    p.add_argument(
        "--url",
        default=env("INFLUX_URL", "http://10.1.137.104:8086"),
        help="InfluxDB base URL",
    )
    p.add_argument("--token", default=env("INFLUX_TOKEN", "ina-infra-influxdb-token"))
    p.add_argument("--org", default=env("INFLUX_ORG", "ina-infra"))
    p.add_argument("--bucket", default=env("INFLUX_BUCKET", "default"))
    p.add_argument("--measurement", default=env("INFLUX_MEASUREMENT", "iperf3"))
    p.add_argument(
        "--tz",
        default=env("INFLUX_TZ", "Asia/Taipei"),
        help="timezone for naive timestamps in CSV",
    )
    p.add_argument(
        "--roles",
        default="client_agg,server_agg",
        help="comma-separated Influx role tags to pull",
    )
    p.add_argument("--step", type=int, default=0, help="only download this step # (0=all)")
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing step_*.npz / summary.npz (default: skip)",
    )
    args = p.parse_args(argv)

    try:
        jobs = resolve_download_jobs(tag=args.tag, csv=args.csv, out=args.out)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    want_client = "client_agg" in roles
    want_server = "server_agg" in roles
    if not (want_client or want_server):
        print("error: --roles must include client_agg and/or server_agg", file=sys.stderr)
        return 1

    tz = ZoneInfo(args.tz)
    print(
        f"Influx {args.url} org={args.org} bucket={args.bucket} "
        f"measurement={args.measurement} tz={args.tz}"
    )

    for csv_path, out_dir in jobs:
        if not csv_path.is_file():
            print(f"error: missing {csv_path}", file=sys.stderr)
            return 1
        steps = load_steps(csv_path, tz)
        if args.step:
            steps = [s for s in steps if s.step == args.step]
            if not steps:
                print(f"error: step {args.step} not in {csv_path}", file=sys.stderr)
                return 1

        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"CSV {csv_path.name} → {out_dir} ({len(steps)} steps)")

        downloaded = 0
        skipped = 0
        for step in steps:
            path = out_dir / f"{step.stem}.npz"
            if path.is_file() and not args.force:
                skipped += 1
                print(f"  {path.name}: skip (exists)")
                continue

            t_c = y_c = t_s = y_s = np.array([], dtype=np.float64)
            if want_client:
                t_c, y_c = query_role_mbps(
                    url=args.url,
                    token=args.token,
                    org=args.org,
                    bucket=args.bucket,
                    measurement=args.measurement,
                    role="client_agg",
                    start=step.start,
                    stop=step.stop,
                )
            if want_server:
                t_s, y_s = query_role_mbps(
                    url=args.url,
                    token=args.token,
                    org=args.org,
                    bucket=args.bucket,
                    measurement=args.measurement,
                    role="server_agg",
                    start=step.start,
                    stop=step.stop,
                )
            t, client, server = align_series(t_c, y_c, t_s, y_s)
            path = save_step(out_dir, step, t, client, server)
            downloaded += 1
            c_mean = (
                float(np.nanmean(client))
                if client.size and np.any(~np.isnan(client))
                else float("nan")
            )
            s_mean = (
                float(np.nanmean(server))
                if server.size and np.any(~np.isnan(server))
                else float("nan")
            )
            print(
                f"  {path.name}: n={t.size} "
                f"client_mean={c_mean:.2f} Mbps server_mean={s_mean:.2f} Mbps "
                f"({step.start.astimezone(tz):%H:%M:%S}–{step.stop.astimezone(tz):%H:%M:%S})"
            )

        summary_path = out_dir / "summary.npz"
        if summary_path.is_file() and not args.force:
            print(
                f"  summary.npz: skip (exists; "
                f"downloaded {downloaded}, skipped {skipped})"
            )
        else:
            summary_rows: List[Dict[str, float]] = []
            prb_labels: List[str] = []
            for step in steps:
                path = out_dir / f"{step.stem}.npz"
                if not path.is_file():
                    continue
                z = np.load(path)
                client = z["client_mbps"]
                server = z["server_mbps"]
                c_mean = (
                    float(np.nanmean(client))
                    if client.size and np.any(~np.isnan(client))
                    else float("nan")
                )
                s_mean = (
                    float(np.nanmean(server))
                    if server.size and np.any(~np.isnan(server))
                    else float("nan")
                )
                prb_labels.append(step.prb)
                summary_rows.append(
                    {
                        "step": step.step,
                        "prb_pct": parse_prb_pct(step.prb),
                        "n": float(z["t_unix_s"].size),
                        "client_mean_mbps": c_mean,
                        "client_p50_mbps": float(np.nanmedian(client))
                        if client.size
                        else float("nan"),
                        "server_mean_mbps": s_mean,
                        "server_p50_mbps": float(np.nanmedian(server))
                        if server.size
                        else float("nan"),
                        "start_unix_s": float(z["start_unix_s"]),
                        "stop_unix_s": float(z["stop_unix_s"]),
                    }
                )
            if summary_rows:
                keys = list(summary_rows[0].keys())
                packed = {
                    k: np.asarray([row[k] for row in summary_rows]) for k in keys
                }
                np.savez_compressed(
                    summary_path, prb=np.asarray(prb_labels), **packed
                )
                print(f"Wrote {summary_path}")
            else:
                print("  summary.npz: skip (no step files)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
