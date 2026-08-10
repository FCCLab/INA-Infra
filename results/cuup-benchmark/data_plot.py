#!/usr/bin/env python3
"""Plot mean iperf3 throughput vs CU-UP CPU from ``data/summary.npz``.

Writes under ``plots/`` (gitignored):
  plots/throughput_vs_cpu.png
  plots/throughput_vs_cpu.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_SUMMARY = HERE / "data" / "summary.npz"
DEFAULT_OUT = HERE / "plots"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--metric",
        choices=("mean", "p50"),
        default="mean",
        help="client/server aggregate statistic (default: mean)",
    )
    p.add_argument(
        "--no-server",
        action="store_true",
        help="omit server_agg (offered) series",
    )
    args = p.parse_args(argv)

    if not args.summary.is_file():
        print(
            f"error: missing {args.summary} — run data_download.py first",
            file=sys.stderr,
        )
        return 1

    s = np.load(args.summary, allow_pickle=True)
    cpu = np.asarray(s["cpu_millis"], dtype=np.float64)
    if args.metric == "mean":
        client = np.asarray(s["client_mean_mbps"], dtype=np.float64)
        server = np.asarray(s["server_mean_mbps"], dtype=np.float64)
        ylabel = "Average throughput (Mbps)"
        title = "CU-UP CPU vs average DL throughput"
    else:
        client = np.asarray(s["client_p50_mbps"], dtype=np.float64)
        server = np.asarray(s["server_p50_mbps"], dtype=np.float64)
        ylabel = "Median throughput (Mbps)"
        title = "CU-UP CPU vs median DL throughput"

    order = np.argsort(cpu)
    cpu = cpu[order]
    client = client[order]
    server = server[order]

    args.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8.0, 4.5), constrained_layout=True)
    ax.plot(
        cpu,
        client,
        marker="o",
        markersize=4.5,
        linewidth=1.6,
        label="client_agg (UE receive)",
        color="#1f4e79",
    )
    if not args.no_server:
        ax.plot(
            cpu,
            server,
            marker="s",
            markersize=3.5,
            linewidth=1.2,
            linestyle="--",
            label="server_agg (UPF send)",
            color="#8a8a8a",
        )

    ax.set_xlabel("CU-UP CPU limit/request (millicores)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.grid(True, which="major", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.legend(loc="lower right", frameon=False)
    ax.set_xticks(cpu[:: max(1, len(cpu) // 10)])

    png = args.out / "throughput_vs_cpu.png"
    pdf = args.out / "throughput_vs_cpu.pdf"
    fig.savefig(png, dpi=160)
    fig.savefig(pdf)
    plt.close(fig)
    print(f"Wrote {png}")
    print(f"Wrote {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
