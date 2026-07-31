"""Per-node NIC rates from Prometheus node_exporter (node_network_*)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services import prometheus as prom

_HISTORY_SECONDS = 5 * 60
_HISTORY_STEP = "15s"


def classify_iface(name: str) -> str:
    n = name.lower()
    if n == "lo" or n.startswith("virbr") or n.startswith("vnet"):
        return "other"
    if (
        n.startswith("cni")
        or n.startswith("flannel")
        or n.startswith("calico")
        or n.startswith("tunl")
        or n.startswith("vxlan")
        or n.startswith("veth")
        or n.startswith("docker")
        or n.startswith("kube-ipvs")
        or n.startswith("nodelocaldns")
        or n.startswith("weave")
        or n.startswith("cilium")
        or n.startswith("lxc")
    ):
        return "kubernetes"
    if (
        n.startswith("en")
        or n.startswith("eth")
        or n.startswith("bond")
        or n.startswith("wl")
        or n.startswith("ib")
        or n.startswith("em")
        or bool(re.match(r"^p\d+p\d+", n))
    ):
        return "physical"
    return "other"


def _esc(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"')


def _mbps(v: Optional[float]) -> Optional[float]:
    x = prom.finite(v)
    if x is None:
        return None
    return round(x, 4)


def _bps_from_mbps(mbps: Optional[float]) -> Optional[float]:
    x = prom.finite(mbps)
    if x is None:
        return None
    return round(x * 1e6, 2)


def fetch_node_interfaces(cluster: str, node: str) -> Dict[str, Any]:
    node_esc = _esc(node)
    # Bytes/s * 8 / 1e6 => Mbps. Prefer series labeled with kubernetes node name.
    # Prefer kubernetes-pods scrape job; fall back without job filter if empty.
    # Restrict to physically up NICs via node_network_up == 1 (operstate).
    sel = f'node="{node_esc}",job="kubernetes-pods"'
    up_q = f'max by (device) (node_network_up{{{sel}}} == 1)'
    rx_q = (
        f'(avg by (device) (rate(node_network_receive_bytes_total{{{sel}}}[5m]))'
        f" and on(device) ({up_q})) * 8 / 1e6"
    )
    tx_q = (
        f'(avg by (device) (rate(node_network_transmit_bytes_total{{{sel}}}[5m]))'
        f" and on(device) ({up_q})) * 8 / 1e6"
    )
    rx_bytes_q = (
        f'(max by (device) (node_network_receive_bytes_total{{{sel}}})'
        f" and on(device) ({up_q}))"
    )
    tx_bytes_q = (
        f'(max by (device) (node_network_transmit_bytes_total{{{sel}}})'
        f" and on(device) ({up_q}))"
    )

    rx_res, rx_err = prom.query(cluster, rx_q)
    if rx_err:
        return {
            "cluster": cluster,
            "node": node,
            "source": "prometheus",
            "interfaces": [],
            "history": {"labels": [], "series": {}},
            "error": rx_err,
        }
    tx_res, tx_err = prom.query(cluster, tx_q)
    rx_b_res, _ = prom.query(cluster, rx_bytes_q)
    tx_b_res, _ = prom.query(cluster, tx_bytes_q)
    up_res, _ = prom.query(cluster, up_q)

    up_devs = {
        (sample.get("metric") or {}).get("device")
        for sample in up_res
        if (sample.get("metric") or {}).get("device")
    }

    rx_by: Dict[str, float] = {}
    tx_by: Dict[str, float] = {}
    rx_bytes: Dict[str, int] = {}
    tx_bytes: Dict[str, int] = {}

    for sample in rx_res:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        if not dev:
            continue
        val = prom.sample_value(sample)
        if val is not None:
            rx_by[dev] = val
    for sample in tx_res:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        if not dev:
            continue
        val = prom.sample_value(sample)
        if val is not None:
            tx_by[dev] = val
    for sample in rx_b_res:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        val = prom.sample_value(sample)
        if dev and val is not None:
            rx_bytes[dev] = int(val)
    for sample in tx_b_res:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        val = prom.sample_value(sample)
        if dev and val is not None:
            tx_bytes[dev] = int(val)

    names = sorted(set(rx_by) | set(tx_by) | set(rx_bytes) | set(tx_bytes))
    interfaces: List[Dict[str, Any]] = []
    for name in names:
        kind = classify_iface(name)
        if kind != "physical":
            continue
        if up_devs and name not in up_devs:
            continue
        rx_mbps = _mbps(rx_by.get(name))
        tx_mbps = _mbps(tx_by.get(name))
        interfaces.append(
            {
                "name": name,
                "kind": kind,
                "rx_bytes": rx_bytes.get(name, 0),
                "tx_bytes": tx_bytes.get(name, 0),
                "rx_bps": _bps_from_mbps(rx_mbps),
                "tx_bps": _bps_from_mbps(tx_mbps),
                "rx_mbps": rx_mbps,
                "tx_mbps": tx_mbps,
            }
        )

    up_physical = {i["name"] for i in interfaces}

    # Range history for charts (physically up NICs only).
    end = time.time()
    start = end - _HISTORY_SECONDS
    rx_hist, hist_err = prom.query_range(cluster, rx_q, start, end, step=_HISTORY_STEP)
    tx_hist, _ = prom.query_range(cluster, tx_q, start, end, step=_HISTORY_STEP)

    # Align timestamps from first series.
    ts_set = set()
    for sample in rx_hist + tx_hist:
        for pair in sample.get("values") or []:
            if isinstance(pair, (list, tuple)) and pair:
                ts_set.add(float(pair[0]))
    timestamps = sorted(ts_set)
    labels = [
        datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%H:%M:%S")
        for ts in timestamps
    ]
    ts_index = {ts: i for i, ts in enumerate(timestamps)}
    n = len(timestamps)

    series: Dict[str, Dict[str, List[Optional[float]]]] = {}
    for sample in rx_hist:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        if not dev or classify_iface(dev) != "physical":
            continue
        if up_physical and dev not in up_physical:
            continue
        bucket = series.setdefault(
            dev, {"rx_mbps": [None] * n, "tx_mbps": [None] * n}
        )
        for pair in sample.get("values") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            idx = ts_index.get(float(pair[0]))
            if idx is None:
                continue
            bucket["rx_mbps"][idx] = _mbps(pair[1])
    for sample in tx_hist:
        metric = sample.get("metric") or {}
        dev = metric.get("device")
        if not dev or classify_iface(dev) != "physical":
            continue
        if up_physical and dev not in up_physical:
            continue
        bucket = series.setdefault(
            dev, {"rx_mbps": [None] * n, "tx_mbps": [None] * n}
        )
        for pair in sample.get("values") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            idx = ts_index.get(float(pair[0]))
            if idx is None:
                continue
            bucket["tx_mbps"][idx] = _mbps(pair[1])

    err_parts = []
    if tx_err:
        err_parts.append(tx_err)
    if hist_err:
        err_parts.append(hist_err)
    if not interfaces and not err_parts:
        err_parts.append("no up physical NIC series (is node_exporter scraped?)")

    return {
        "cluster": cluster,
        "node": node,
        "source": "prometheus",
        "interfaces": interfaces,
        "history": {"labels": labels, "series": series},
        "error": "; ".join(err_parts) if err_parts else None,
    }
