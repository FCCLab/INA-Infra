#!/usr/bin/env python3
"""Exp4 slice 4: SFTP download of a queued encrypted 1 MB file over the PDU.

Latency is last-byte receive minus t_send (stamped in the remote filename
at generate start, before encrypt).
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from pathlib import Path

import paramiko

FILE_RE = re.compile(r"^q-(\d+)-([0-9]+(?:\.[0-9]+)?)\.bin$")


def main() -> None:
    p = argparse.ArgumentParser(description="Exp4 S4 SFTP downlink (encrypted 1 MB)")
    p.add_argument("--host", default=os.environ.get("SFTP_HOST") or os.environ.get("TARGET_SERVER_IP") or "10.140.4.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("SFTP_PORT", "22")))
    p.add_argument("--user", default=os.environ.get("SFTP_USER", "ina"))
    p.add_argument("--password", default=os.environ.get("SFTP_PASS", "ina"))
    p.add_argument("--remote-dir", default=os.environ.get("SFTP_REMOTE_DIR", "download"))
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    src = os.environ.get("SIM5G_IP") or os.environ.get("MULTUS_IP") or ""
    if src:
        sock.bind((src, 0))
    sock.connect((args.host, args.port))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        timeout=30,
        sock=sock,
    )
    sftp = client.open_sftp()
    queued: list[tuple[int, str, float]] = []
    for name in sftp.listdir(args.remote_dir):
        m = FILE_RE.match(name)
        if m:
            queued.append((int(m.group(1)), name, float(m.group(2))))
    queued.sort(key=lambda x: x[0])
    if not queued:
        sftp.close()
        client.close()
        print("no queued file ready", file=sys.stderr)
        sys.exit(2)
    _seq, name, t_send = queued[0]
    remote = f"{args.remote_dir}/{name}"
    dest = Path("/tmp") / name
    nbytes = 0

    def _cb(transferred: int, _total: int) -> None:
        nonlocal nbytes
        nbytes = transferred

    sftp.get(remote, str(dest), callback=_cb)
    t_recv = time.time()
    try:
        sftp.remove(remote)
    except OSError:
        pass
    sftp.close()
    client.close()
    dest.unlink(missing_ok=True)
    e2e_ms = max(0.0, (t_recv - t_send) * 1000.0)
    dt = max(t_recv - t_send, 1e-6)
    mbit = (nbytes * 8.0) / dt / 1e6
    print(
        f"exp4_sftp host={args.host} file={name} bytes={nbytes} "
        f"e2e_ms={e2e_ms:.1f} t_send={t_send:.6f} t_recv={t_recv:.6f} "
        f"transfer_s={dt:.4f} goodput_mbit={mbit:.3f} deleted",
        flush=True,
    )


if __name__ == "__main__":
    main()
