#!/usr/bin/env python3
"""Physical AI console sidecar: HF token Secret + vLLM health/chat proxy."""
from __future__ import annotations

import base64
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

NS_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
TOKEN_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
CA_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

SECRET_NAME = os.environ.get("HF_SECRET_NAME", "ina-hf-token")
SECRET_KEY = os.environ.get("HF_SECRET_KEY", "token")
DEPLOY_NAME = os.environ.get("HF_DEPLOY_NAME", "application-physical-ai")
VLLM_URL = os.environ.get("VLLM_URL", "http://127.0.0.1:8000").rstrip("/")
STATIC_DIR = Path(os.environ.get("DASHBOARD_STATIC", "/app/static"))
SLICE_ID = int(os.environ.get("SLICE_ID", "2"))
UE_CONSOLE_PORT = int(os.environ.get("UE_CONSOLE_PORT", "80"))
UE_CONSOLE_IP_BASE = int(os.environ.get("UE_CONSOLE_IP_BASE", "200"))
# OTA hits vLLM :8000; iptables REDIRECT net1:8000 → this proxy, then loopback vLLM.
LATENCY_PROXY_PORT = int(os.environ.get("LATENCY_PROXY_PORT", "18080"))
# Drop last-sample latency when no new UL request arrives (exporter scrape is 1s).
LATENCY_STALE_S = float(os.environ.get("LATENCY_STALE_S", "2.5"))


def _http_url(host: str, port: int = UE_CONSOLE_PORT) -> str:
    if port == 80:
        return f"http://{host}/"
    return f"http://{host}:{port}/"

app = FastAPI(title="Physical AI console", docs_url="/api/docs")


class TokenIn(BaseModel):
    token: str = Field(..., min_length=1)


class ChatIn(BaseModel):
    prompt: str = Field(..., min_length=1)
    max_tokens: int = Field(128, ge=1, le=2048)


class UeControlIn(BaseModel):
    send_enabled: Optional[bool] = None
    include_image: Optional[bool] = None


def _console_ip(idx: int) -> str:
    return f"10.1.137.{UE_CONSOLE_IP_BASE + max(int(idx), 1) - 1}"


def _console_mac(idx: int) -> str:
    return f"02:0a:40:{SLICE_ID:02x}:00:{max(int(idx), 1):02x}"


def _ns() -> str:
    return os.environ.get("POD_NAMESPACE") or (NS_FILE.read_text().strip() if NS_FILE.is_file() else "ina-infra")


def _k8s_ctx() -> tuple[str, ssl.SSLContext, dict[str, str]]:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host or not TOKEN_FILE.is_file():
        raise HTTPException(status_code=503, detail="in-cluster Kubernetes credentials not available")
    token = TOKEN_FILE.read_text().strip()
    ctx = ssl.create_default_context()
    if CA_FILE.is_file():
        ctx.load_verify_locations(str(CA_FILE))
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return f"https://{host}:{port}", ctx, headers


def _k8s(method: str, path: str, body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> tuple[int, Any]:
    base, ctx, headers = _k8s_ctx()
    hdrs = dict(headers)
    if extra_headers:
        hdrs.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:400]}
        return exc.code, parsed


def _vllm(method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(f"{VLLM_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:400]}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"vLLM not ready: {exc.reason}") from exc


@app.get("/api/status")
def api_status() -> dict:
    ns = _ns()
    code, secret = _k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{SECRET_NAME}")
    configured = code == 200 and bool((secret or {}).get("data", {}).get(SECRET_KEY))
    vllm_ok = False
    model = None
    vcode, vbody = 0, {}
    try:
        vcode, vbody = _vllm("GET", "/v1/models")
        vllm_ok = vcode == 200
        data = (vbody or {}).get("data") or []
        if data:
            model = data[0].get("id")
    except HTTPException:
        vllm_ok = False
    return {
        "ok": True,
        "namespace": ns,
        "secret": SECRET_NAME,
        "configured": configured,
        "vllm_ready": vllm_ok,
        "model": model,
        "vllm_url": VLLM_URL,
    }


@app.put("/api/hf-token")
def api_save_token(body: TokenIn) -> dict:
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    ns = _ns()
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET_NAME,
            "namespace": ns,
            "labels": {"ina.lab/role": "hf-token"},
        },
        "type": "Opaque",
        "stringData": {SECRET_KEY: token},
    }
    code, _ = _k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{SECRET_NAME}")
    if code == 200:
        code, resp = _k8s("PATCH", f"/api/v1/namespaces/{ns}/secrets/{SECRET_NAME}", payload, extra_headers={"Content-Type": "application/merge-patch+json"})
    else:
        code, resp = _k8s("POST", f"/api/v1/namespaces/{ns}/secrets", payload)
    if code not in (200, 201):
        raise HTTPException(status_code=400, detail=(resp or {}).get("message", f"secret apply failed ({code})"))
    restarted = _restart_deploy(ns)
    return {"ok": True, "configured": True, "restarted": restarted, "message": "Token saved." + (" vLLM restarting." if restarted else "")}


@app.delete("/api/hf-token")
def api_delete_token() -> dict:
    ns = _ns()
    _k8s("DELETE", f"/api/v1/namespaces/{ns}/secrets/{SECRET_NAME}")
    restarted = _restart_deploy(ns)
    return {"ok": True, "configured": False, "restarted": restarted, "message": "Token removed."}


def _restart_deploy(ns: str) -> bool:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    patch = {"spec": {"template": {"metadata": {"annotations": {"ina.lab/restarted-at": stamp}}}}}
    code, _ = _k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{ns}/deployments/{DEPLOY_NAME}",
        patch,
        extra_headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    return code in (200, 201)


@app.post("/api/chat")
def api_chat(body: ChatIn) -> dict:
    code, resp = _vllm(
        "POST",
        "/v1/chat/completions",
        {
            "model": os.environ.get("MODEL_NAME", "nvidia/Cosmos3-Nano"),
            "messages": [{"role": "user", "content": body.prompt}],
            "max_tokens": body.max_tokens,
        },
    )
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=resp)
    choices = (resp or {}).get("choices") or []
    text = ""
    if choices:
        text = ((choices[0].get("message") or {}).get("content")) or ""
    return {"ok": True, "text": text, "raw": resp}


def _ue_idx_from_name(name: str) -> Optional[int]:
    prefix = f"oai-ue-slice-{SLICE_ID}-client-"
    if not name.startswith(prefix):
        return None
    try:
        return int(name[len(prefix) :])
    except ValueError:
        return None


def _ue_deploy_name(idx: int) -> str:
    return f"oai-ue-slice-{SLICE_ID}-client-{idx}"


def _ue_pod_ip(idx: int) -> Optional[str]:
    ns = _ns()
    name = _ue_deploy_name(idx)
    code, pods = _k8s("GET", f"/api/v1/namespaces/{ns}/pods")
    if code != 200:
        return None
    for pod in (pods or {}).get("items") or []:
        labels = (pod.get("metadata") or {}).get("labels") or {}
        if (labels.get("app.kubernetes.io/name") or labels.get("app")) != name:
            continue
        if (pod.get("status") or {}).get("phase") != "Running":
            continue
        ip = (pod.get("status") or {}).get("podIP")
        if ip:
            return str(ip)
    return None


def _ue_upstreams(idx: int) -> list[str]:
    """Query UE console strictly via Multus IP."""
    ip = _console_ip(idx)
    return [f"{ip}:80", f"{ip}:8090", f"{ip}:{UE_CONSOLE_PORT}"]


def _ue_http_idx(
    idx: int,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: int = 8,
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    last_err = "unreachable"
    path = path if path.startswith("/") else f"/{path}"
    for host in _ue_upstreams(idx):
        url = f"http://{host}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode() or "{}"
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, {"raw": raw[:400]}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"message": raw[:400]}
            return exc.code, parsed
        except urllib.error.URLError as exc:
            last_err = f"{host}: {exc.reason}"
            continue
    return 503, {"detail": last_err}


@app.get("/api/ues")
def api_ues() -> dict:
    ns = _ns()
    code, body = _k8s("GET", f"/apis/apps/v1/namespaces/{ns}/deployments")
    if code not in (200, 201):
        raise HTTPException(status_code=502, detail=(body or {}).get("message", f"list deployments failed ({code})"))
    pcode, pods = _k8s("GET", f"/api/v1/namespaces/{ns}/pods")
    pods_by_app: dict[str, dict] = {}
    if pcode == 200:
        for pod in (pods or {}).get("items") or []:
            labels = (pod.get("metadata") or {}).get("labels") or {}
            app = labels.get("app.kubernetes.io/name") or labels.get("app") or ""
            if app:
                pods_by_app[app] = pod
    ues = []
    for dep in (body or {}).get("items") or []:
        meta = dep.get("metadata") or {}
        name = meta.get("name") or ""
        idx = _ue_idx_from_name(name)
        if idx is None:
            continue
        labels = meta.get("labels") or {}
        spec = (dep.get("spec") or {}).get("template") or {}
        spec_labels = (spec.get("metadata") or {}).get("labels") or {}
        ip = labels.get("ina.lab/console-ip") or spec_labels.get("ina.lab/console-ip") or _console_ip(idx)
        raw_mac = labels.get("ina.lab/console-mac") or spec_labels.get("ina.lab/console-mac") or ""
        mac = raw_mac.replace("-", ":") if raw_mac else _console_mac(idx)
        st = (dep.get("status") or {})
        ready = int(st.get("readyReplicas") or 0)
        desired = int(st.get("replicas") or 1)
        pod = pods_by_app.get(name) or {}
        phase = (pod.get("status") or {}).get("phase") or ""
        containers = (pod.get("status") or {}).get("containerStatuses") or []
        sidecar = {c.get("name"): bool(c.get("ready")) for c in containers if isinstance(c, dict)}
        live = None
        sc, live = _ue_http_idx(idx, "/api/status", timeout=3)
        if sc != 200:
            live = {"ok": False, "http_status": sc, **(live if isinstance(live, dict) else {})}
        multus_url = _http_url(ip) if ip else f"http://{_console_ip(idx)}:80"
        ues.append(
            {
                "name": name,
                "client_index": idx,
                "ready": ready,
                "desired": desired,
                "pod_phase": phase,
                "sidecars": sidecar,
                "console_ip": ip,
                "console_mac": mac,
                "console_url": multus_url,
                "dashboard_url": multus_url,
                "connected": (ready >= 1) or (phase == "Running"),
                "status": live,
            }
        )
    ues.sort(key=lambda u: int(u["client_index"]))
    return {"ok": True, "slice_id": SLICE_ID, "ues": ues}


@app.get("/api/ues/{idx}/exchanges")
def api_ue_exchanges(idx: int, limit: int = 40) -> dict:
    code, body = _ue_http_idx(idx, f"/api/exchanges?limit={int(limit)}", timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=body)
    return body


@app.post("/api/ues/{idx}/control")
def api_ue_control(idx: int, body: UeControlIn) -> dict:
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    code, resp = _ue_http_idx(idx, "/api/control", method="POST", body=payload, timeout=8)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=resp)
    return resp


@app.post("/api/ues/{idx}/send-once")
def api_ue_send_once(idx: int) -> dict:
    code, resp = _ue_http_idx(idx, "/api/send-once", method="POST", body={}, timeout=130)
    if code != 200:
        raise HTTPException(status_code=code if code >= 400 else 502, detail=resp)
    return resp


_slo_lock = threading.Lock()
_slo = {
    "latency_ms": None,
    "throughput_mbps": 0.0,
    "ues": {},  # ue_id -> {latency_ms?, throughput_mbps}
}

_ul_lock = threading.Lock()
_ul_delay_ms: dict[str, float] = {}
_ul_delay_at: dict[str, float] = {}
_ul_window: deque[tuple[float, float]] = deque(maxlen=64)


def _grafana_ue_id(app_name: Any, client_index: Any) -> str:
    if isinstance(app_name, str) and app_name.strip():
        return app_name.strip()
    try:
        return f"slice{SLICE_ID}-physical-ai-client-{int(client_index)}"
    except (TypeError, ValueError):
        return f"slice{SLICE_ID}-physical-ai-client"


def _record_ul_latency(ue_id: str, delay_ms: float) -> None:
    uid = str(ue_id or _grafana_ue_id(None, None))
    now = time.time()
    with _ul_lock:
        _ul_delay_ms[uid] = float(delay_ms)
        _ul_delay_at[uid] = now
        _ul_window.append((now, float(delay_ms)))


def _fresh_ul(now: Optional[float] = None) -> tuple[dict[str, float], list[float]]:
    """Uplink delay from messages received within LATENCY_STALE_S. Omit otherwise."""
    now = time.time() if now is None else now
    cutoff = now - max(1.0, LATENCY_STALE_S)
    with _ul_lock:
        for uid in [u for u, ts in _ul_delay_at.items() if ts < cutoff]:
            _ul_delay_ms.pop(uid, None)
            _ul_delay_at.pop(uid, None)
        window = [v for ts, v in _ul_window if ts >= cutoff]
        return dict(_ul_delay_ms), window


def _one_way_ms(_ue_id: str, raw_s: float) -> float:
    """Radio one-way delay: ``t_recv − t_send`` in milliseconds.

    Requires NTP-aligned UE host (usrp) and server host (gpu-a40). Negative
    values are clock skew, not reverse flight time — clamp to 0 like IoT.
    """
    delay_ms = float(raw_s) * 1000.0
    if delay_ms < 0:
        return 0.0
    return delay_ms


class _LatencyProxyHandler(BaseHTTPRequestHandler):
    """Stamp uplink one-way delay (recv − t_send) then forward to vLLM."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _vllm(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes, str]:
        hdrs = {"Content-Type": headers.get("Content-Type") or "application/json"}
        req = urllib.request.Request(
            f"{VLLM_URL}{path}",
            data=body if method not in ("GET", "HEAD") else None,
            headers=hdrs,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type") or "application/json"
                return int(resp.status), raw, ctype
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            ctype = (exc.headers.get("Content-Type") if exc.headers else None) or "application/json"
            return int(exc.code), raw, ctype
        except urllib.error.URLError as exc:
            payload = json.dumps({"error": {"message": f"vLLM not ready: {exc.reason}"}}).encode()
            return 503, payload, "application/json"

    def _reply(self, code: int, raw: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path or "/"
        code, raw, ctype = self._vllm("GET", path, b"", dict(self.headers))
        self._reply(code, raw, ctype)

    def do_POST(self) -> None:  # noqa: N802
        recv = time.time()
        path = self.path or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        raw_in = self.rfile.read(length) if length > 0 else b""
        fwd = raw_in
        t_send = None
        app_name = self.headers.get("X-INA-App-Name")
        client_index = self.headers.get("X-INA-Client-Index")
        hdr_t = self.headers.get("X-INA-T-Send")
        if hdr_t:
            try:
                t_send = float(hdr_t)
            except (TypeError, ValueError):
                t_send = None
        try:
            parsed = json.loads(raw_in) if raw_in else None
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            ina = parsed.pop("ina", None)
            if isinstance(ina, dict):
                if t_send is None:
                    try:
                        t_send = float(ina.get("t_send"))
                    except (TypeError, ValueError):
                        t_send = None
                app_name = app_name or ina.get("app_name")
                if client_index is None:
                    client_index = ina.get("client_index")
            if "t_send" in parsed and t_send is None:
                try:
                    t_send = float(parsed.pop("t_send"))
                except (TypeError, ValueError):
                    parsed.pop("t_send", None)
            fwd = json.dumps(parsed, separators=(",", ":")).encode("utf-8")
        delay_ms = None
        if t_send is not None:
            delay_ms = _one_way_ms(_grafana_ue_id(app_name, client_index), recv - float(t_send))
            _record_ul_latency(_grafana_ue_id(app_name, client_index), delay_ms)
        code, raw_out, ctype = self._vllm("POST", path, fwd, dict(self.headers))
        if delay_ms is not None and "json" in (ctype or ""):
            try:
                out = json.loads(raw_out.decode("utf-8") or "{}")
            except (ValueError, TypeError, UnicodeDecodeError):
                out = None
            if isinstance(out, dict):
                out["ina"] = {
                    "t_send": t_send,
                    "t_recv": recv,
                    "latency_ms": round(delay_ms, 3),
                    "app_name": _grafana_ue_id(app_name, client_index),
                }
                raw_out = json.dumps(out).encode("utf-8")
        self._reply(code, raw_out, ctype)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()


def _start_latency_proxy() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LATENCY_PROXY_PORT), _LatencyProxyHandler)
    threading.Thread(target=server.serve_forever, name="ul-latency-proxy", daemon=True).start()


def _slo_from_ues() -> None:
    ues = {}
    tput = 0.0
    try:
        body = api_ues()
    except Exception:
        body = {"ues": []}
    for u in body.get("ues") or []:
        idx = u.get("client_index")
        st = u.get("status") or {}
        uid = str(st.get("app_name") or u.get("name") or f"slice{SLICE_ID}-physical-ai-client-{idx}")
        last = st.get("last") or {}
        sent = last.get("sent") or {}
        sent_b = float(sent.get("bytes") or 0.0)
        try:
            interval = float(st.get("interval_s") or 8)
        except (TypeError, ValueError):
            interval = 8.0
        mbps = (sent_b * 8.0) / (interval * 1e6) if interval > 0 and sent_b else 0.0
        ues[uid] = {"throughput_mbps": mbps}
        tput += mbps
    ul, window = _fresh_ul()
    for uid, lat in ul.items():
        slot = dict(ues.get(uid) or {"throughput_mbps": 0.0})
        slot["latency_ms"] = lat
        ues[uid] = slot
    with _slo_lock:
        _slo["ues"] = ues
        _slo["latency_ms"] = (sum(window) / len(window)) if window else None
        _slo["throughput_mbps"] = tput


def _slo_loop() -> None:
    while True:
        try:
            _slo_from_ues()
        except Exception:
            pass
        time.sleep(2)


@app.get("/metrics")
def prometheus_metrics() -> Response:
    with _slo_lock:
        agg_tput = _slo["throughput_mbps"]
        ues = dict(_slo["ues"])
    ul, _window = _fresh_ul()
    lines = [
        "# HELP app_throughput_mbps Aggregated application throughput (Mbps)",
        "# TYPE app_throughput_mbps gauge",
        f"app_throughput_mbps {agg_tput}",
        "# HELP app_ue_throughput_mbps Per-UE application throughput (Mbps)",
        "# TYPE app_ue_throughput_mbps gauge",
    ]
    if ul:
        lines.extend(
            [
                "# HELP app_ue_latency_ms Per-UE application latency (milliseconds)",
                "# TYPE app_ue_latency_ms gauge",
            ]
        )
        for uid, lat in ul.items():
            safe = uid.replace('"', "")
            lines.append(f'app_ue_latency_ms{{ue_id="{safe}"}} {lat}')
    for uid, vals in ues.items():
        safe = uid.replace('"', "")
        lines.append(f'app_ue_throughput_mbps{{ue_id="{safe}"}} {vals.get("throughput_mbps") or 0.0}')
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("startup")
def _start_slo_loop() -> None:
    try:
        _start_latency_proxy()
    except Exception:
        pass
    threading.Thread(target=_slo_loop, name="slo-metrics", daemon=True).start()


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
