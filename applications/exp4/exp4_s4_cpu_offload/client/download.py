#!/usr/bin/env python3
"""Exp4 slice 4: GET /download over the PDU; stamp first/last byte; delete local copy."""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _once(url: str) -> int:
    t0 = time.time()
    t_first = None
    n = 0
    file_id = ""
    proc = ""
    tmp = Path(os.environ.get("EXP4_DOWNLOAD_DIR", "/tmp/exp4-s4-dl")) / f"cli-{time.time_ns()}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as resp:
        proc = resp.headers.get("X-Proc-Ms", "")
        file_id = resp.headers.get("X-File-Id", "")
        with tmp.open("wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                if t_first is None:
                    t_first = time.time()
                n += len(chunk)
                out.write(chunk)
    tmp.unlink(missing_ok=True)
    t_last = time.time()
    first = t_first or t0
    dt = max(t_last - first, 1e-6)
    print(
        f"[ok] exp4_s4_cpu_offload url={url} file_id={file_id} bytes={n} "
        f"x_proc_ms={proc} t_first={first:.6f} t_last={t_last:.6f} "
        f"transfer_s={dt:.4f} deleted",
        flush=True,
    )
    return n


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--url",
        default=os.environ.get("DOWNLOAD_URL")
        or f"http://{os.environ.get('TARGET_SERVER_IP', '10.1.137.214')}/download",
    )
    p.add_argument("--loop", action="store_true", help="download continuously from the ready queue")
    p.add_argument("--idle", type=float, default=0.4, help="sleep when the ready queue is empty")
    args = p.parse_args()
    idle = max(args.idle, 0.05)
    while True:
        try:
            n = _once(args.url)
            if n <= 0:
                sys.exit(2)
        except urllib.error.HTTPError as exc:
            if exc.code == 503 and args.loop:
                time.sleep(idle)
                continue
            print(f"download failed: HTTP {exc.code}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:
            if args.loop:
                print(f"download failed: {exc}", file=sys.stderr, flush=True)
                time.sleep(idle)
                continue
            raise
        if not args.loop:
            return


if __name__ == "__main__":
    main()
