"""POST a heartbeat to the Exp4 server so the server console can list this UE."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

PayloadFn = Callable[[], dict[str, Any]]


def default_payload() -> dict[str, Any]:
    console_ip = os.environ.get("CONSOLE_IP") or os.environ.get("MULTUS_IP") or ""
    name = (
        os.environ.get("APP_NAME")
        or os.environ.get("UE_NAME")
        or os.environ.get("CLIENT_ID")
        or ""
    )
    client_id = os.environ.get("CLIENT_ID") or name or (f"ue-{console_ip}" if console_ip else "")
    return {
        "client_id": client_id,
        "name": name or client_id,
        "console_ip": console_ip,
        "console_url": f"http://{console_ip}" if console_ip else "",
    }


def server_api_base() -> str:
    explicit = (os.environ.get("SERVER_API_URL") or os.environ.get("SERVER_URL") or "").rstrip("/")
    if explicit:
        return explicit
    host = (
        os.environ.get("TARGET_SERVER_IP")
        or os.environ.get("SFTP_HOST")
        or os.environ.get("BROKER_HOST")
        or ""
    )
    if not host:
        return ""
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"http://{host}"


def start(*, payload_fn: Optional[PayloadFn] = None, period: float = 3.0) -> None:
    period = float(os.environ.get("EXP4_HEARTBEAT_PERIOD_S", str(period)))
    fn = payload_fn or default_payload

    def _post(url: str, body: bytes) -> None:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3).read()

    def loop() -> None:
        while True:
            try:
                base = server_api_base()
                payload = dict(default_payload())
                extra = fn() or {}
                for key, val in extra.items():
                    if val not in (None, ""):
                        payload[key] = val
                if base and payload.get("client_id"):
                    body = json.dumps(payload).encode("utf-8")
                    try:
                        _post(f"{base}/api/v1/clients/heartbeat", body)
                    except urllib.error.HTTPError:
                        _post(f"{base}/api/clients/heartbeat", body)
            except Exception:
                pass
            time.sleep(period)

    threading.Thread(target=loop, daemon=True, name="exp4-hb").start()
