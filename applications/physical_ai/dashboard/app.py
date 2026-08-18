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
from datetime import datetime, timezone
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
    """Prefer in-cluster Service/pod IP; Multus is last (N6 macvlan can RST during UE recreate)."""
    name = _ue_deploy_name(idx)
    hosts = [
        f"{name}:{UE_CONSOLE_PORT}",
        f"{name}.{_ns()}.svc:{UE_CONSOLE_PORT}",
    ]
    pod_ip = _ue_pod_ip(idx)
    if pod_ip:
        hosts.append(f"{pod_ip}:{UE_CONSOLE_PORT}")
    hosts.append(f"{_console_ip(idx)}:{UE_CONSOLE_PORT}")
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


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
                "console_url": _http_url(ip) if ip else "",
                "dashboard_url": f"/ue/{idx}/",
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


@app.get("/ue/{idx}")
def ue_dash_redir(idx: int) -> RedirectResponse:
    return RedirectResponse(url=f"/ue/{idx}/", status_code=307)


@app.api_route("/ue/{idx}/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def ue_dash_root(idx: int, request: Request) -> Response:
    return await ue_dash_proxy(idx, "", request)


@app.api_route("/ue/{idx}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def ue_dash_proxy(idx: int, path: str, request: Request) -> Response:
    """Same-origin proxy. Prefer ClusterIP/pod IP; Multus N6 is fallback."""
    suffix = path if path else ""
    q = request.url.query
    data = None
    if request.method not in ("GET", "HEAD"):
        data = await request.body()
    headers = {}
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    last_err = "unreachable"
    last_host = ""
    for host in _ue_upstreams(idx):
        last_host = host
        target = f"http://{host}/{suffix}"
        if q:
            target = f"{target}?{q}"
        req = urllib.request.Request(target, data=data, headers=headers, method=request.method)
        try:
            with urllib.request.urlopen(req, timeout=130) as resp:
                raw = resp.read()
                media = resp.headers.get("Content-Type", "application/octet-stream")
                status = resp.status
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            media = exc.headers.get("Content-Type", "application/octet-stream") if exc.headers else "application/octet-stream"
            status = exc.code
            break
        except urllib.error.URLError as exc:
            last_err = str(exc.reason)
            continue
    else:
        raise HTTPException(status_code=503, detail=f"UE {idx} dashboard unreachable ({last_host}): {last_err}")
    if "text/html" in (media or "") and raw:
        html = raw.decode("utf-8", errors="replace")
        html = html.replace('fetch("/api', f'fetch("/ue/{idx}/api')
        html = html.replace("req(\"/api", f"req(\"/ue/{idx}/api")
        html = html.replace("req(`/api", f"req(`/ue/{idx}/api")
        raw = html.encode("utf-8")
        media = "text/html; charset=utf-8"
    return Response(content=raw, status_code=status, media_type=media)


_slo_lock = threading.Lock()
_slo = {
    "latency_ms": 0.0,
    "throughput_mbps": 0.0,
    "ues": {},  # ue_id -> {latency_ms, throughput_mbps}
}


def _slo_from_ues() -> None:
    ues = {}
    lats = []
    tput = 0.0
    try:
        body = api_ues()
    except Exception:
        body = {"ues": []}
    for u in body.get("ues") or []:
        idx = u.get("client_index")
        uid = str(u.get("name") or f"ue{idx}")
        st = u.get("status") or {}
        last = st.get("last") or {}
        rec = last.get("received") or {}
        sent = last.get("sent") or {}
        lat = float(rec.get("latency_ms") or st.get("latency_ms") or 0.0)
        sent_b = float(sent.get("bytes") or 0.0)
        try:
            interval = float(st.get("interval_s") or 8)
        except (TypeError, ValueError):
            interval = 8.0
        mbps = (sent_b * 8.0) / (interval * 1e6) if interval > 0 and sent_b else 0.0
        ues[uid] = {"latency_ms": lat, "throughput_mbps": mbps}
        if lat:
            lats.append(lat)
        tput += mbps
    with _slo_lock:
        _slo["ues"] = ues
        _slo["latency_ms"] = (sum(lats) / len(lats)) if lats else 0.0
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
        agg_lat = _slo["latency_ms"]
        agg_tput = _slo["throughput_mbps"]
        ues = dict(_slo["ues"])
    lines = [
        "# HELP app_latency_ms Aggregated application latency (milliseconds)",
        "# TYPE app_latency_ms gauge",
        f"app_latency_ms {agg_lat}",
        "# HELP app_throughput_mbps Aggregated application throughput (Mbps)",
        "# TYPE app_throughput_mbps gauge",
        f"app_throughput_mbps {agg_tput}",
        "# HELP app_ue_latency_ms Per-UE application latency (milliseconds)",
        "# TYPE app_ue_latency_ms gauge",
        "# HELP app_ue_throughput_mbps Per-UE application throughput (Mbps)",
        "# TYPE app_ue_throughput_mbps gauge",
    ]
    for uid, vals in ues.items():
        safe = uid.replace('"', "")
        lines.append(f'app_ue_latency_ms{{ue_id="{safe}"}} {vals["latency_ms"]}')
        lines.append(f'app_ue_throughput_mbps{{ue_id="{safe}"}} {vals["throughput_mbps"]}')
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("startup")
def _start_slo_loop() -> None:
    threading.Thread(target=_slo_loop, name="slo-metrics", daemon=True).start()


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
