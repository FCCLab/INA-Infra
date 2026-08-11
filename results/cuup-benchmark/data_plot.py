#!/usr/bin/env python3
"""Plot mean iperf3 throughput vs CU-UP CPU from ``data_<tag>/summary.npz``.

Default (no args): plot **all** ``data_*/summary.npz``.
Single run: ``--tag dl_tcp``.

Writes under ``plots/`` (gitignored):
  plots/throughput_vs_cpu_dl_udp.png
  plots/throughput_vs_cpu_dl_udp.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_TAG = "all"
DEFAULT_OUT = HERE / "plots"


def list_plot_tags(tag: str) -> list[str]:
    if tag != "all":
        return [tag]
    tags = sorted(
        p.name[len("data_") :]
        for p in HERE.glob("data_*")
        if p.is_dir() and (p / "summary.npz").is_file()
    )
    return tags


def plot_one(tag: str, summary: Path, out: Path, metric: str, no_server: bool) -> int:
    if not summary.is_file():
        print(
            f"error: missing {summary} — run data_download.py --tag {tag} first",
            file=sys.stderr,
        )
        return 1

    s = np.load(summary, allow_pickle=True)
    cpu = np.asarray(s["cpu_millis"], dtype=np.float64)
    if metric == "mean":
        client = np.asarray(s["client_mean_mbps"], dtype=np.float64)
        server = np.asarray(s["server_mean_mbps"], dtype=np.float64)
        ylabel = "Average throughput (Mbps)"
        title = f"CU-UP CPU vs average DL throughput ({tag})"
    else:
        client = np.asarray(s["client_p50_mbps"], dtype=np.float64)
        server = np.asarray(s["server_p50_mbps"], dtype=np.float64)
        ylabel = "Median throughput (Mbps)"
        title = f"CU-UP CPU vs median DL throughput ({tag})"

    order = np.argsort(cpu)
    cpu = cpu[order]
    client = client[order]
    server = server[order]

    out.mkdir(parents=True, exist_ok=True)
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
    if not no_server:
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

    stem = f"throughput_vs_cpu_{tag}"
    png = out / f"{stem}.png"
    pdf = out / f"{stem}.pdf"
    fig.savefig(png, dpi=160)
    fig.savefig(pdf)
    plt.close(fig)
    print(f"Wrote {png}")
    print(f"Wrote {pdf}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help="run postfix, or 'all' (default) for every data_*/summary.npz",
    )
    p.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="single summary.npz (overrides --tag)",
    )
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

    if args.summary is not None:
        tag = args.tag if args.tag != "all" else "custom"
        return plot_one(tag, args.summary, args.out, args.metric, args.no_server)

    tags = list_plot_tags(args.tag)
    if not tags:
        print(f"error: no data_*/summary.npz under {HERE}", file=sys.stderr)
        return 1

    rc = 0
    for tag in tags:
        code = plot_one(
            tag,
            HERE / f"data_{tag}" / "summary.npz",
            args.out,
            args.metric,
            args.no_server,
        )
        if code:
            rc = code
    return rc


if __name__ == "__main__":
    sys.exit(main())
