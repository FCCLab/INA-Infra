#!/usr/bin/env python3
"""Exp4 slice 1: SFTP download of the 5 MB payload over the PDU.

Stamps first-byte and last-byte wall time for transfer-time SLA
(T_bar = 20 Mbit/s → 5 MB should finish in ~2 s).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import paramiko


def main() -> None:
    p = argparse.ArgumentParser(description="Exp4 S1 SFTP downlink")
    p.add_argument("--host", default=os.environ.get("SFTP_HOST") or os.environ.get("TARGET_SERVER_IP") or "10.1.137.211")
    p.add_argument("--port", type=int, default=int(os.environ.get("SFTP_PORT", "22")))
    p.add_argument("--user", default=os.environ.get("SFTP_USER", "ina"))
    p.add_argument("--password", default=os.environ.get("SFTP_PASS", "ina"))
    p.add_argument("--remote", default=os.environ.get("SFTP_REMOTE", "download/exp4-5mb.bin"))
    p.add_argument("--local", default=os.environ.get("SFTP_LOCAL", "/tmp/exp4-5mb.bin"))
    args = p.parse_args()

    t_connect = time.time()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, port=args.port, username=args.user, password=args.password, timeout=30)
    sftp = client.open_sftp()
    t_first = None
    nbytes = 0

    def _cb(transferred: int, _total: int) -> None:
        nonlocal t_first, nbytes
        if t_first is None:
            t_first = time.time()
        nbytes = transferred

    dest = Path(args.local)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sftp.get(args.remote, str(dest), callback=_cb)
    t_last = time.time()
    sftp.close()
    client.close()

    first = t_first or t_connect
    dt = max(t_last - first, 1e-6)
    mbit = (nbytes * 8.0) / dt / 1e6
    print(
        f"exp4_sftp host={args.host} bytes={nbytes} "
        f"t_first={first:.6f} t_last={t_last:.6f} "
        f"transfer_s={dt:.4f} goodput_mbit={mbit:.3f}",
        flush=True,
    )
    if nbytes < 5 * 1024 * 1024:
        sys.exit(2)


if __name__ == "__main__":
    main()
