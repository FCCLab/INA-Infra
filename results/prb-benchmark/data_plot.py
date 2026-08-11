#!/usr/bin/env python3
"""Plot mean iperf3 throughput vs NS max PRB% from ``data/summary.npz``.

Writes under ``plots/`` (gitignored):
  plots/throughput_vs_prb.png
  plots/throughput_vs_prb.pdf
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
    prb = np.asarray(s["prb_pct"], dtype=np.float64)
    if args.metric == "mean":
        client = np.asarray(s["client_mean_mbps"], dtype=np.float64)
        server = np.asarray(s["server_mean_mbps"], dtype=np.float64)
        ylabel = "Average throughput (Mbps)"
        title = "NS max PRB% vs average DL throughput"
    else:
        client = np.asarray(s["client_p50_mbps"], dtype=np.float64)
        server = np.asarray(s["server_p50_mbps"], dtype=np.float64)
        ylabel = "Median throughput (Mbps)"
        title = "NS max PRB% vs median DL throughput"

    order = np.argsort(prb)
    prb = prb[order]
    client = client[order]
    server = server[order]

    args.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8.0, 4.5), constrained_layout=True)
    ax.plot(
        prb,
        client,
        marker="o",
        markersize=4.5,
        linewidth=1.6,
        label="client_agg (UE receive)",
        color="#1f4e79",
    )
    if not args.no_server:
        ax.plot(
            prb,
            server,
            marker="s",
            markersize=3.5,
            linewidth=1.2,
            linestyle="--",
            label="server_agg (UPF send)",
            color="#8a8a8a",
        )

    ax.set_xlabel("NS max PRB ratio (%)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xlim(0, 105)
    ax.set_ylim(bottom=0)
    ax.grid(True, which="major", linestyle=":", linewidth=0.7, alpha=0.7)
    ax.legend(loc="lower right", frameon=False)
    ax.set_xticks(prb[:: max(1, len(prb) // 10)])

    png = args.out / "throughput_vs_prb.png"
    pdf = args.out / "throughput_vs_prb.pdf"
    fig.savefig(png, dpi=160)
    fig.savefig(pdf)
    plt.close(fig)
    print(f"Wrote {png}")
    print(f"Wrote {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
