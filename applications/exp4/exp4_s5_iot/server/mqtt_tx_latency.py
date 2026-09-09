#!/usr/bin/env python3
"""MQTT application latency: publish() → TX on TO_CLIENT_IFACE.

Captures PACKET_OUTGOING MQTT PUBLISH frames on the server N6 / to-client
iface and records ``t_tx - t_send`` where ``t_send`` is stamped at publish.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import threading
import time
from typing import Callable, Optional

PACKET_OUTGOING = 4
MQTT_PORT = 1883
T_SEND_RE = re.compile(br'"t_send":([0-9.]+)')
LATENCY_FILE = os.environ.get("EXP4_E2E_LATENCY_FILE") or "/tmp/exp4_e2e_latency_ms"


def _iface() -> str:
    return (
        os.environ.get("TO_CLIENT_IFACE")
        or os.environ.get("OTA_IFACE")
        or os.environ.get("N6_IFACE")
        or "net1"
    )


def _tcp_payload(frame: bytes) -> Optional[bytes]:
    if len(frame) < 34:
        return None
    ethertype = struct.unpack("!H", frame[12:14])[0]
    off = 14
    if ethertype == 0x8100 and len(frame) >= 18:
        ethertype = struct.unpack("!H", frame[16:18])[0]
        off = 18
    if ethertype != 0x0800:
        return None
    ip = frame[off:]
    if len(ip) < 20 or (ip[0] >> 4) != 4 or ip[9] != 6:
        return None
    ihl = (ip[0] & 0x0F) * 4
    tot = struct.unpack("!H", ip[2:4])[0]
    if tot > len(ip):
        tot = len(ip)
    if ihl < 20 or tot < ihl + 20:
        return None
    tcp = ip[ihl:tot]
    sport, dport = struct.unpack("!HH", tcp[0:4])
    if sport != MQTT_PORT and dport != MQTT_PORT:
        return None
    doff = (tcp[12] >> 4) * 4
    if doff < 20 or len(tcp) < doff:
        return None
    return tcp[doff:]


def _write_ms(ms: float) -> None:
    try:
        open(LATENCY_FILE, "w", encoding="utf-8").write(f"{ms:.3f}\n")
    except OSError:
        pass


def start_sniffer(
    stop: threading.Event,
    on_sample: Optional[Callable[[float], None]] = None,
    min_interval_s: float = 0.02,
) -> None:
    """Daemon thread: sample publish→TO_CLIENT_IFACE TX delay."""

    def _run() -> None:
        iface = _iface()
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            sock.bind((iface, 0))
            sock.settimeout(0.5)
        except OSError as exc:
            print(f"[mqtt-tx-latency] sniffer unavailable on {iface}: {exc}", flush=True)
            return
        print(f"[mqtt-tx-latency] sniffing PACKET_OUTGOING MQTT on {iface}", flush=True)
        last = 0.0
        try:
            while not stop.is_set():
                try:
                    frame, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                pkttype = addr[2] if isinstance(addr, tuple) and len(addr) > 2 else None
                if pkttype != PACKET_OUTGOING:
                    continue
                now = time.time()
                if now - last < min_interval_s:
                    continue
                payload = _tcp_payload(frame)
                if not payload or b'"pad":' not in payload[:512]:
                    continue
                m = T_SEND_RE.search(payload[:512])
                if not m:
                    continue
                try:
                    t_send = float(m.group(1))
                except ValueError:
                    continue
                if t_send < 1_000_000_000:
                    continue
                ms = max(0.0, (now - t_send) * 1000.0)
                last = now
                _write_ms(ms)
                if on_sample is not None:
                    on_sample(ms)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True, name="mqtt-tx-latency").start()
