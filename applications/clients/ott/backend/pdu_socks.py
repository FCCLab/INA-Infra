"""Minimal SOCKS5 proxy that binds outbound sockets to the 5G PDU address.

Chromium (--proxy-server=socks5://127.0.0.1:1080) reaches YouTube via oaitun_*.
"""
from __future__ import annotations

import logging
import select
import socket
import struct
import threading
from typing import Optional, Tuple

logger = logging.getLogger("ott.ue.pdu_socks")

SOCKS_PORT = int(__import__("os").environ.get("PDU_SOCKS_PORT", "1080"))


class PduSocksProxy:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pdu_ip = ""
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self.bytes_up = 0
        self.bytes_down = 0
        self.conns = 0

    def set_pdu_ip(self, pdu_ip: str) -> None:
        with self._lock:
            self._pdu_ip = pdu_ip or ""

    def pdu_ip(self) -> str:
        with self._lock:
            return self._pdu_ip

    def start(self, pdu_ip: str = "", port: int = SOCKS_PORT) -> None:
        self.set_pdu_ip(pdu_ip)
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._serve, args=(port,), name="pdu-socks5", daemon=True
            )
            self._thread.start()
        logger.info("PDU SOCKS5 listening on 127.0.0.1:%s (bind src=%s)", port, pdu_ip or "any")

    def stats(self) -> dict:
        return {
            "socks_port": SOCKS_PORT,
            "pdu_ip": self.pdu_ip(),
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "connections": self.conns,
            "running": bool(self._thread and self._thread.is_alive()),
        }

    def _serve(self, port: int) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(128)
        srv.settimeout(1.0)
        self._sock = srv
        while not self._stop.is_set():
            try:
                client, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(client,), name="socks-conn", daemon=True
            ).start()
        try:
            srv.close()
        except OSError:
            pass

    def _handle(self, client: socket.socket) -> None:
        remote: Optional[socket.socket] = None
        try:
            client.settimeout(30.0)
            # greeting
            data = client.recv(2)
            if len(data) < 2 or data[0] != 5:
                return
            nmethods = data[1]
            client.recv(nmethods)
            client.sendall(b"\x05\x00")  # no auth

            req = client.recv(4)
            if len(req) < 4 or req[0] != 5 or req[1] != 1:  # CONNECT only
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            atyp = req[3]
            if atyp == 1:  # IPv4
                addr = socket.inet_ntoa(client.recv(4))
            elif atyp == 3:  # domain
                ln = client.recv(1)[0]
                addr = client.recv(ln).decode("utf-8", "replace")
            elif atyp == 4:  # IPv6 — unsupported
                client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            else:
                return
            port = struct.unpack("!H", client.recv(2))[0]

            pdu = self.pdu_ip()
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if pdu:
                remote.bind((pdu, 0))
            remote.settimeout(45.0)
            remote.connect((addr, port))
            # success
            client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
            self.conns += 1
            self._relay(client, remote)
        except Exception as exc:
            logger.debug("socks conn error: %s", exc)
            try:
                client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
        finally:
            for s in (client, remote):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        a.setblocking(False)
        b.setblocking(False)
        sockets = [a, b]
        while True:
            r, _, x = select.select(sockets, [], sockets, 120.0)
            if x or not r:
                break
            for src in r:
                dst = b if src is a else a
                try:
                    data = src.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return
                if src is a:
                    self.bytes_up += len(data)
                else:
                    self.bytes_down += len(data)


PDU_SOCKS = PduSocksProxy()
