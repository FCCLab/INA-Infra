#!/usr/bin/env python3
"""Physical AI dashboard sidecar: HF token Secret + vLLM health/chat proxy."""
from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

app = FastAPI(title="Physical AI dashboard", docs_url="/api/docs")


class TokenIn(BaseModel):
    token: str = Field(..., min_length=1)


class ChatIn(BaseModel):
    prompt: str = Field(..., min_length=1)
    max_tokens: int = Field(128, ge=1, le=2048)


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
