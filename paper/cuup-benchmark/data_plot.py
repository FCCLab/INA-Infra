#!/usr/bin/env python3
"""Plot mean iperf3 throughput vs CU-UP CPU from ``data_<tag>/summary.npz``.

Default (no args): plot **all** ``data_*/summary.npz``.
Single run: ``--tag dl_tcp``.

Writes under ``plots/`` (gitignored):
  plots/throughput_vs_cpu_dl_udp.png
  plots/throughput_vs_cpu_dl_udp.pdf

Client (UE receive) and server (UPF send) are shown with distinct styles; a
small panel plots server−client so the two reports stay distinguishable even
when absolute Mbps nearly match.
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

CLIENT_COLOR = "#1f4e79"
SERVER_COLOR = "#c45c26"


def list_plot_tags(tag: str) -> list[str]:
    if tag != "all":
        return [tag]
    tags = sorted(
        p.name[len("data_") :]
        for p in HERE.glob("data_*")
        if p.is_dir() and (p / "summary.npz").is_file()
    )
    return tags


def traffic_label(tag: str) -> tuple[str, str, str]:
    """``ul_tcp`` → (``UL TCP``, client legend, server legend)."""
    parts = [p for p in (tag or "").lower().replace("-", "_").split("_") if p]
    direction = "DL"
    proto = ""
    for p in parts:
        if p in ("ul", "uplink"):
            direction = "UL"
        elif p in ("dl", "downlink"):
            direction = "DL"
        elif p in ("tcp", "udp"):
            proto = p.upper()
    kind = f"{direction} {proto}".strip()
    if direction == "UL":
        return kind, "client (UE send)", "server (UPF receive)"
    return kind, "client (UE receive)", "server (UPF send)"


def plot_one(tag: str, summary: Path, out: Path, metric: str, no_server: bool) -> int:
    if not summary.is_file():
        print(
            f"error: missing {summary} — run data_download.py --tag {tag} first",
            file=sys.stderr,
        )
        return 1

    s = np.load(summary, allow_pickle=True)
    cpu = np.asarray(s["cpu_millis"], dtype=np.float64)
    kind, client_leg, server_leg = traffic_label(tag)
    if metric == "mean":
        client = np.asarray(s["client_mean_mbps"], dtype=np.float64)
        server = np.asarray(s["server_mean_mbps"], dtype=np.float64)
        ylabel = "Average throughput (Mbps)"
        title = f"CU-UP CPU vs average {kind} throughput"
    else:
        client = np.asarray(s["client_p50_mbps"], dtype=np.float64)
        server = np.asarray(s["server_p50_mbps"], dtype=np.float64)
        ylabel = "Median throughput (Mbps)"
        title = f"CU-UP CPU vs median {kind} throughput"

    order = np.argsort(cpu)
    cpu = cpu[order]
    client = client[order]
    server = server[order]

    out.mkdir(parents=True, exist_ok=True)
    if no_server:
        fig, ax = plt.subplots(figsize=(8.0, 4.5), constrained_layout=True)
        axes_main = ax
        ax_delta = None
    else:
        fig, (ax, ax_delta) = plt.subplots(
            2,
            1,
            figsize=(8.0, 5.6),
            sharex=True,
            gridspec_kw={"height_ratios": [3.2, 1.0], "hspace": 0.08},
            constrained_layout=True,
        )
        axes_main = ax

    if not no_server:
        axes_main.plot(
            cpu,
            server,
            marker="s",
            markersize=5.0,
            markerfacecolor="white",
            markeredgewidth=1.4,
            linewidth=1.5,
            linestyle="--",
            label=server_leg,
            color=SERVER_COLOR,
            zorder=2,
        )
    axes_main.plot(
        cpu,
        client,
        marker="o",
        markersize=4.5,
        linewidth=1.8,
        label=client_leg,
        color=CLIENT_COLOR,
        zorder=3,
    )

    axes_main.set_ylabel(ylabel)
    axes_main.set_title(title)
    axes_main.set_xlim(left=0)
    axes_main.set_ylim(bottom=0)
    axes_main.grid(True, which="major", linestyle=":", linewidth=0.7, alpha=0.7)
    axes_main.legend(loc="lower right", frameon=False)
    axes_main.set_xticks(cpu[:: max(1, len(cpu) // 10)])

    if ax_delta is not None:
        delta = server - client
        ax_delta.axhline(0.0, color="#888888", linewidth=0.8)
        ax_delta.plot(
            cpu,
            delta,
            marker="D",
            markersize=3.5,
            linewidth=1.2,
            color="#555555",
            label="server − client",
        )
        ax_delta.set_ylabel("Δ Mbps")
        ax_delta.set_xlabel("CU-UP CPU limit/request (millicores)")
        ax_delta.grid(True, which="major", linestyle=":", linewidth=0.7, alpha=0.7)
        ax_delta.legend(loc="best", frameon=False, fontsize=9)
        # Symmetric-ish y so small gaps are visible.
        lim = float(np.nanmax(np.abs(delta))) if delta.size else 1.0
        lim = max(lim * 1.25, 1.0)
        ax_delta.set_ylim(-lim, lim)
    else:
        axes_main.set_xlabel("CU-UP CPU limit/request (millicores)")

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
        help="omit server series and delta panel",
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
