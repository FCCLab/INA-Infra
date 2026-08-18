#!/usr/bin/env python3
"""IoT UE frontend: console UI + proxy to local backend."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(os.environ.get("DASHBOARD_STATIC", "/app/static"))
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8090").rstrip("/")
UE_NAME = os.environ.get("UE_NAME", "iot-ue")
CONSOLE_IP = os.environ.get("CONSOLE_IP", "")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", "")

app = FastAPI(title=f"{UE_NAME} console", docs_url=None)


def _forward(method: str, path: str, data: bytes | None) -> Response:
    url = f"{BACKEND_URL}/api/{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "application/json")
            return Response(content=raw, status_code=resp.status, media_type=ctype)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return Response(content=raw, status_code=exc.code, media_type="application/json")
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"backend unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "ue": UE_NAME, "console_ip": CONSOLE_IP, "console_mac": CONSOLE_MAC}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(path: str, request: Request) -> Response:
    data = None
    if request.method not in ("GET", "HEAD"):
        data = await request.body()
    return _forward(request.method, path, data)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
