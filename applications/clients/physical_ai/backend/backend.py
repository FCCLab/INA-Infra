#!/usr/bin/env python3
"""Physical AI UE backend: send multimodal prompts over the PDU, log replies, control send."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from prometheus_client import Gauge, start_http_server
except ImportError:  # pragma: no cover
    Gauge = None  # type: ignore
    start_http_server = None  # type: ignore

SLICE_ID = int(os.environ.get("SLICE_ID", "2"))
CLIENT_INDEX = int(os.environ.get("CLIENT_INDEX", "1"))
UE_NAME = os.environ.get("UE_NAME", f"oai-ue-slice-{SLICE_ID}-client-{CLIENT_INDEX}")
CONSOLE_IP = os.environ.get("CONSOLE_IP", "")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", "")
SERVER_URL = (
    os.environ.get("SERVER_URL")
    or os.environ.get("URL")
    or "http://10.1.137.212:8000"
).rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME") or os.environ.get("MODEL") or "nvidia/Cosmos3-Nano"
# ---------------------------------------------------------------------------
# Network Interfaces: To-Server (default: RAN interface) & API (default: eth0)
# ---------------------------------------------------------------------------
TO_SERVER_IFACE_CFG = (
    os.environ.get("TO_SERVER_IFACE")
    or os.environ.get("SERVER_IFACE")
    or os.environ.get("RAN_IFACE")
    or os.environ.get("PDU_IFACE")
    or f"oaitun_ue{SLICE_ID}"
)
API_IFACE_CFG = os.environ.get("API_IFACE") or "eth0"
PDU_IFACE_CFG = TO_SERVER_IFACE_CFG
PDU_ROUTE_HOSTS = os.environ.get("PDU_ROUTE_HOSTS", "")
PDU_WAIT_TIMEOUT = int(os.environ.get("PDU_WAIT_TIMEOUT", "300"))
_pdu_iface_live = PDU_IFACE_CFG
_pdu_lock = threading.Lock()
_api_iface_live = API_IFACE_CFG
INTERVAL_S = float(os.environ.get("SEND_INTERVAL_S", "8"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "256"))
PROMPT = os.environ.get(
    "UE_PROMPT",
    "Analyze this warehouse corridor for an autonomous mobile robot: "
    "identify potential navigation hazards, dynamic obstacles, and suggest safe path vectors.",
)
IMAGE_WIDTH = int(os.environ.get("IMAGE_WIDTH", "64"))
IMAGE_HEIGHT = int(os.environ.get("IMAGE_HEIGHT", "36"))
LOG_LIMIT = int(os.environ.get("EXCHANGE_LOG_LIMIT", "80"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8001"))
UE_ID = os.environ.get("APP_NAME") or UE_NAME

if Gauge is not None:
    APP_UE_LATENCY_MS = Gauge(
        "app_ue_latency_ms", "Per-UE application latency (milliseconds)", ["ue_id"]
    )
    APP_UE_RTT_MS = Gauge(
        "app_ue_rtt_ms", "Per-UE round-trip time (milliseconds)", ["ue_id"]
    )
    APP_UE_THROUGHPUT_MBPS = Gauge(
        "app_ue_throughput_mbps", "Per-UE application throughput (Mbps)", ["ue_id"]
    )
    APP_LATENCY_MS = Gauge("app_latency_ms", "Aggregated application latency (milliseconds)")
    APP_THROUGHPUT_MBPS = Gauge(
        "app_throughput_mbps", "Aggregated application throughput (Mbps)"
    )
else:
    APP_UE_LATENCY_MS = APP_UE_RTT_MS = APP_UE_THROUGHPUT_MBPS = APP_LATENCY_MS = APP_THROUGHPUT_MBPS = None


_lock = threading.Lock()
_exchanges: deque[dict[str, Any]] = deque(maxlen=LOG_LIMIT)
_state = {
    "send_enabled": os.environ.get("SEND_ENABLED", "1") not in ("0", "false", "False"),
    "include_image": os.environ.get("INCLUDE_IMAGE", "1") not in ("0", "false", "False"),
    "pdu_ready": False,
    "pdu_iface": "",
    "last_error": None,
    "loop_alive": False,
    "in_flight": False,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "last_prompt_tokens": 0,
    "last_completion_tokens": 0,
}


def _png(width: int, height: int) -> bytes:
    """Tiny synthetic RGB PNG (no extra deps)."""
    width = max(8, min(width, 320))
    height = max(8, min(height, 180))
    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            r = (x * 255) // max(width - 1, 1)
            g = (y * 255) // max(height - 1, 1)
            b = 80 + ((x + y) * 40) % 160
            raw += bytes((r & 255, g & 255, b & 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + tag
            + data
            + zlib.crc32(tag + data).to_bytes(4, "big")
        )

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _usage_tokens(usage: Any) -> tuple[int, int, int]:
    if not isinstance(usage, dict):
        return 0, 0, 0
    try:
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    except (TypeError, ValueError):
        prompt = 0
    try:
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    except (TypeError, ValueError):
        completion = 0
    try:
        total = int(usage.get("total_tokens") or (prompt + completion))
    except (TypeError, ValueError):
        total = prompt + completion
    return prompt, completion, total


def get_interface_ip(ifname: str) -> Optional[str]:
    """Retrieve the primary IPv4 address of a network interface."""
    if not ifname:
        return None
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-br", "addr", "show", "dev", ifname],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        parts = out.split()
        if len(parts) >= 3:
            return parts[2].split("/")[0]
    except Exception:
        pass
    return None


def _resolve_api_iface() -> str:
    global _api_iface_live
    if _api_iface_live and Path(f"/sys/class/net/{_api_iface_live}").is_dir():
        return _api_iface_live
    for cand in [API_IFACE_CFG, "eth0"]:
        if cand and Path(f"/sys/class/net/{cand}").is_dir():
            _api_iface_live = cand
            return cand
    return API_IFACE_CFG


def _discover_pdu_iface() -> Optional[str]:
    candidates = []
    for raw in (TO_SERVER_IFACE_CFG, PDU_IFACE_CFG, f"oaitun_ue{SLICE_ID}", "oaitun_ue1", "oaitun_ue2", "oaitun_ue3", "oaitun_ue4", "oaitun_ue5"):
        if raw and raw not in candidates:
            candidates.append(raw)
    try:
        res = subprocess.run(["ip", "-br", "link"], capture_output=True, text=True, timeout=5)
        for line in res.stdout.splitlines():
            name = line.split()[0] if line.strip() else ""
            if name.startswith("oaitun") and name not in candidates:
                candidates.append(name)
    except Exception:
        pass
    for name in candidates:
        try:
            r = subprocess.run(
                ["ip", "-4", "addr", "show", "dev", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and "inet " in r.stdout:
                return name
        except Exception:
            continue
    return None

_resolve_to_server_iface = _discover_pdu_iface


def _server_hosts() -> list[str]:
    hosts: list[str] = ["10.1.137.1"]
    for h in PDU_ROUTE_HOSTS.split(","):
        h = h.strip()
        if h and h not in hosts:
            hosts.append(h)
    # Always pin the application server host from SERVER_URL.
    try:
        from urllib.parse import urlparse

        host = urlparse(SERVER_URL).hostname
        if host and host not in hosts:
            hosts.append(host)
    except Exception:
        pass
    return hosts


def _ping_loop() -> None:
    while True:
        try:
            with _lock:
                ready = bool(_state.get("pdu_ready"))
            if ready:
                res = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", "10.1.137.1"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    import re

                    m = re.search(r"time=([0-9.]+)\s*ms", res.stdout)
                    if m:
                        rtt = float(m.group(1))
                        if APP_UE_RTT_MS is not None:
                            APP_UE_RTT_MS.labels(ue_id=UE_ID).set(rtt)
        except Exception:
            pass
        time.sleep(1.0)



def _pin_pdu() -> bool:
    global _pdu_iface_live
    hosts = _server_hosts()
    if not hosts:
        return True
    iface = _discover_pdu_iface()
    if not iface:
        return False
    ok = False
    for host in hosts:
        r = subprocess.run(
            ["ip", "route", "replace", f"{host}/32", "dev", iface],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            ok = True
    if ok:
        with _pdu_lock:
            _pdu_iface_live = iface
    return ok


def _wait_pdu() -> None:
    elapsed = 0
    while elapsed < PDU_WAIT_TIMEOUT:
        if _pin_pdu():
            with _lock:
                _state["pdu_ready"] = True
                _state["pdu_iface"] = _pdu_iface_live
            return
        time.sleep(2)
        elapsed += 2
    with _lock:
        _state["pdu_ready"] = False
        _state["last_error"] = f"PDU (prefer {PDU_IFACE_CFG}) not ready after {PDU_WAIT_TIMEOUT}s"


def _one_way_ms(parsed: Any) -> Optional[float]:
    if not isinstance(parsed, dict):
        return None
    ina = parsed.get("ina")
    if not isinstance(ina, dict):
        return None
    try:
        return float(ina.get("latency_ms"))
    except (TypeError, ValueError):
        return None


def _openai_chat(prompt: str, include_image: bool) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    image_b64 = None
    image_bytes = 0
    if include_image:
        png = _png(IMAGE_WIDTH, IMAGE_HEIGHT)
        image_b64 = base64.b64encode(png).decode()
        image_bytes = len(png)
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            }
        )
    body = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    t_send = time.time()
    body["ina"] = {
        "t_send": t_send,
        "app_name": UE_ID,
        "client_index": CLIENT_INDEX,
        "ue": UE_NAME,
    }
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-INA-T-Send": str(t_send),
            "X-INA-App-Name": UE_ID,
            "X-INA-Client-Index": str(CLIENT_INDEX),
        },
        method="POST",
    )
    t0 = time.monotonic()
    parsed: Any = {}
    code = 0
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode() or "{}"
            parsed = json.loads(raw)
            code = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {"message": raw[:400]}
        except json.JSONDecodeError:
            parsed = {"message": raw[:400]}
        code = exc.code
        parsed = parsed if isinstance(parsed, dict) else {"message": str(parsed)}
        rtt_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "http_status": code,
            "http_rtt_ms": rtt_ms,
            "latency_ms": _one_way_ms(parsed),
            "sent_bytes": len(payload),
            "image_bytes": image_bytes,
            "include_image": include_image,
            "prompt": prompt,
            "text": "",
            "error": parsed.get("message") or parsed.get("detail") or str(parsed)[:400],
            "raw": parsed,
        }
    except urllib.error.URLError as exc:
        rtt_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "http_status": 0,
            "http_rtt_ms": rtt_ms,
            "latency_ms": None,
            "sent_bytes": len(payload),
            "image_bytes": image_bytes,
            "include_image": include_image,
            "prompt": prompt,
            "text": "",
            "error": str(exc.reason),
            "raw": {},
        }
    rtt_ms = int((time.monotonic() - t0) * 1000)
    choices = (parsed or {}).get("choices") or []
    text = ""
    if choices:
        text = ((choices[0].get("message") or {}).get("content")) or ""
    usage = (parsed or {}).get("usage") or {}
    return {
        "ok": code == 200,
        "http_status": code,
        "http_rtt_ms": rtt_ms,
        "latency_ms": _one_way_ms(parsed),
        "sent_bytes": len(payload),
        "image_bytes": image_bytes,
        "include_image": include_image,
        "prompt": prompt,
        "text": text,
        "error": None if code == 200 else str(parsed)[:400],
        "usage": usage,
        "raw": {"id": parsed.get("id"), "usage": usage, "ina": (parsed or {}).get("ina")},
    }


def _record(result: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "ts": _now(),
        "ue": UE_NAME,
        "client_index": CLIENT_INDEX,
        "direction": "roundtrip",
        "sent": {
            "prompt": result.get("prompt"),
            "include_image": result.get("include_image"),
            "bytes": result.get("sent_bytes"),
            "image_bytes": result.get("image_bytes"),
            "url": f"{SERVER_URL}/v1/chat/completions",
        },
        "received": {
            "ok": result.get("ok"),
            "http_status": result.get("http_status"),
            "text": result.get("text") or "",
            "error": result.get("error"),
            "latency_ms": result.get("latency_ms"),
            "http_rtt_ms": result.get("http_rtt_ms"),
            "usage": result.get("usage") or {},
        },
    }
    prompt_tok, completion_tok, total_tok = _usage_tokens(result.get("usage") or {})
    with _lock:
        _exchanges.appendleft(entry)
        _state["last_error"] = result.get("error")
        _state["in_flight"] = False
        _state["last_prompt_tokens"] = prompt_tok
        _state["last_completion_tokens"] = completion_tok
        if prompt_tok or completion_tok or total_tok:
            _state["prompt_tokens"] = int(_state.get("prompt_tokens") or 0) + prompt_tok
            _state["completion_tokens"] = int(_state.get("completion_tokens") or 0) + completion_tok
            _state["total_tokens"] = int(_state.get("total_tokens") or 0) + total_tok
    lat = result.get("latency_ms")
    sent_b = float(result.get("sent_bytes") or 0.0)
    rtt = float(result.get("http_rtt_ms") or 0.0)
    mbps = (sent_b * 8.0) / (max(rtt, 1.0) * 1e3) if rtt else 0.0
    if APP_LATENCY_MS is not None:
        APP_UE_THROUGHPUT_MBPS.labels(ue_id=UE_ID).set(mbps)
        APP_THROUGHPUT_MBPS.set(mbps)
        if lat is not None:
            APP_UE_LATENCY_MS.labels(ue_id=UE_ID).set(float(lat))
            APP_LATENCY_MS.set(float(lat))

    return entry



def _send_once() -> dict[str, Any]:
    with _lock:
        if _state["in_flight"]:
            raise HTTPException(status_code=409, detail="request already in flight")
        _state["in_flight"] = True
        include_image = bool(_state["include_image"])
        prompt = PROMPT
    try:
        result = _openai_chat(prompt, include_image)
        return _record(result)
    except Exception:
        with _lock:
            _state["in_flight"] = False
        raise


def _loop() -> None:
    _wait_pdu()
    with _lock:
        _state["loop_alive"] = True
    while True:
        with _lock:
            enabled = bool(_state["send_enabled"])
        if enabled:
            try:
                _send_once()
            except HTTPException:
                pass
            except Exception as exc:
                _record(
                    {
                        "ok": False,
                        "http_status": 0,
                        "latency_ms": 0,
                        "sent_bytes": 0,
                        "image_bytes": 0,
                        "include_image": False,
                        "prompt": PROMPT,
                        "text": "",
                        "error": str(exc),
                        "usage": {},
                    }
                )
        time.sleep(max(2.0, INTERVAL_S))
        _pin_pdu()


app = FastAPI(title=f"Physical AI UE {CLIENT_INDEX} backend", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ControlIn(BaseModel):
    send_enabled: Optional[bool] = None
    include_image: Optional[bool] = None


class SendIn(BaseModel):
    prompt: Optional[str] = Field(None, min_length=1)
    include_image: Optional[bool] = None


@app.get("/api/status")
def api_status() -> dict:
    with _lock:
        st = dict(_state)
        n = len(_exchanges)
        last = _exchanges[0] if _exchanges else None
    return {
        "ok": True,
        "ue": UE_NAME,
        "app_name": UE_ID,
        "slice_id": SLICE_ID,
        "client_index": CLIENT_INDEX,
        "console_ip": CONSOLE_IP,
        "console_mac": CONSOLE_MAC,
        "server_url": SERVER_URL,
        "model": MODEL_NAME,
        "pdu_iface": _pdu_iface_live or PDU_IFACE_CFG,
        "to_server_iface": _pdu_iface_live or TO_SERVER_IFACE_CFG,
        "to_server_ip": get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG),
        "api_iface": _resolve_api_iface(),
        "api_ip": get_interface_ip(_resolve_api_iface()),
        "interfaces": {
            "to_server": {
                "name": _pdu_iface_live or TO_SERVER_IFACE_CFG,
                "configured": TO_SERVER_IFACE_CFG,
                "type": "ran",
                "ip": get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG),
                "ready": bool(get_interface_ip(_pdu_iface_live or TO_SERVER_IFACE_CFG)),
            },
            "api": {
                "name": _resolve_api_iface(),
                "configured": API_IFACE_CFG,
                "type": "api",
                "ip": get_interface_ip(_resolve_api_iface()),
                "ready": bool(get_interface_ip(_resolve_api_iface())),
            },
        },
        "interval_s": INTERVAL_S,
        "exchanges": n,
        "last": last,
        **st,
    }


@app.get("/api/interfaces")
def api_interfaces() -> dict:
    to_srv_iface = _resolve_to_server_iface() or TO_SERVER_IFACE_CFG
    to_srv_ip = get_interface_ip(to_srv_iface) if to_srv_iface else None
    api_if = _resolve_api_iface()
    api_ip = get_interface_ip(api_if)
    return {
        "ok": True,
        "to_server_interface": {
            "name": to_srv_iface,
            "configured": TO_SERVER_IFACE_CFG,
            "type": "ran",
            "ip": to_srv_ip,
            "ready": bool(to_srv_ip),
        },
        "api_interface": {
            "name": api_if,
            "configured": API_IFACE_CFG,
            "type": "api",
            "ip": api_ip,
            "ready": bool(api_ip),
        },
    }


@app.get("/api/exchanges")
def api_exchanges(limit: int = 40) -> dict:
    lim = max(1, min(int(limit), LOG_LIMIT))
    with _lock:
        items = list(_exchanges)[:lim]
    return {"ok": True, "ue": UE_NAME, "items": items}


@app.post("/api/control")
def api_control(body: ControlIn) -> dict:
    with _lock:
        if body.send_enabled is not None:
            _state["send_enabled"] = bool(body.send_enabled)
        if body.include_image is not None:
            _state["include_image"] = bool(body.include_image)
        st = dict(_state)
    return {"ok": True, **st}


@app.post("/api/send-once")
def api_send_once(body: Optional[SendIn] = None) -> dict:
    global PROMPT
    if body and body.prompt:
        PROMPT = body.prompt
    if body and body.include_image is not None:
        with _lock:
            _state["include_image"] = bool(body.include_image)
    return _send_once()


@app.on_event("startup")
def _startup() -> None:
    if start_http_server is not None:
        try:
            start_http_server(METRICS_PORT, addr="0.0.0.0")
        except Exception:
            pass
    threading.Thread(target=_ping_loop, name="ue-ping-loop", daemon=True).start()
    threading.Thread(target=_loop, name="ue-send-loop", daemon=True).start()

